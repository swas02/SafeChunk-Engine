import json
import os
import copy
import threading
import time
import shutil
import zipfile
import psutil
import re
import functools
from pathlib import Path

def requires_active(func):
    """
    Decorator to ensure engine methods only run if the engine is properly 
    attached and hasn't been deleted or detached.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self._engine_active:
            self._log(f"Execution Blocked: '{func.__name__}' called on inactive engine.")
            return None
        return func(self, *args, **kwargs)
    return wrapper

class SafeChunkEngine:
    VERSION = "1.3.5"

    def __init__(self, project_id: str, debounce_delay: float = 1.0, base_dir: str = "user_projects"):
        """
        Main Engine Constructor.
        
        Args:
            project_id: Unique string ID for the project folder.
            debounce_delay: Seconds to wait before writing staged changes to disk.
            base_dir: The 'Root' folder where all user projects are stored.
        """
        # Configuration
        self.project_id = project_id
        self.debounce_delay = debounce_delay
        self.base_dir_path = Path(base_dir).resolve()

        # Path Architecture
        self.project_path = self.base_dir_path / self.project_id
        self.chunks_path = self.project_path / "chunks"
        self.backup_path = self.project_path / "chunks_bak"
        self.checkpoint_path = self.project_path / "checkpoints"
        self.lock_file = self.project_path / ".lock"
        self.version_file = self.project_path / "version.json"

        # Threading and Memory Synchronization
        self._write_lock = threading.Lock()
        self._debounce_timer = None
        self._staged_data = {}  # Acts as a high-speed write-ahead buffer
        self.log_history = [] 

        # Communication Hooks (Callbacks)
        self.on_status = None   # For UI status bar updates
        self.on_sync = None     # Triggered after disk write is verified
        self.on_fault = None    # Triggered on critical IO errors

        # Engine Lifecycle State
        self._engine_active = False
        
        # 1. Prepare Folders -> 2. Claim Lock
        self._initialize_env()
        self.attach()

    # --------------------------------------------------------------------------
    # FACTORY & ROOT MANAGEMENT
    # --------------------------------------------------------------------------

    @staticmethod
    def list_all_projects(base_dir: str = "user_projects") -> list:
        """
        Scans the root directory and identifies existing valid projects.
        """
        root = Path(base_dir)
        if not root.exists():
            return []
        
        valid_projects = []
        for item in root.iterdir():
            # A folder is a project if it contains our specific chunk structure
            if item.is_dir() and (item / "chunks").exists():
                valid_projects.append(item.name)
        return valid_projects

    @classmethod
    def new(cls, project_id: str = None, base_dir: str = "user_projects", **kwargs):
        """
        Creates a brand new project. Handles ID collisions by auto-incrementing.
        """
        root = Path(base_dir)
        root.mkdir(parents=True, exist_ok=True)
        
        base_name = project_id or "new_project"
        target_id = base_name
        counter = 1
        
        # Avoid overwriting existing folders
        while (root / target_id).exists():
            target_id = f"{base_name}_{counter}"
            counter += 1
            
        try:
            instance = cls(target_id, base_dir=str(root), **kwargs)
            return instance, "SUCCESS"
        except Exception as e:
            return None, f"FAILED_TO_CREATE: {str(e)}"

    @classmethod
    def open(cls, project_id: str, base_dir: str = "user_projects", **kwargs):
        """
        Opens an existing project. Returns None if the project is locked by another process.
        """
        root = Path(base_dir)
        if not (root / project_id).exists():
            return None, "PROJECT_NOT_FOUND"
        
        try:
            instance = cls(project_id, base_dir=str(root), **kwargs)
            if not instance.is_active():
                return None, "PROJECT_ALREADY_OPEN_IN_ANOTHER_PROCESS"
            return instance, "SUCCESS"
        except Exception as e:
            return None, f"OPEN_ERROR: {str(e)}"

    # --------------------------------------------------------------------------
    # LIFECYCLE MANAGEMENT
    # --------------------------------------------------------------------------

    def attach(self):
        """Claims the project directory by creating a PID-based lock file."""
        if self.lock_file.exists():
            try:
                # Check if the process holding the lock is still alive
                lock_data = self.lock_file.read_text()
                existing_pid = int(lock_data.split(":")[1].strip())
                
                if not psutil.pid_exists(existing_pid):
                    self._log(f"Removing stale lock file from crashed PID {existing_pid}")
                    self.lock_file.unlink()
                else:
                    self._engine_active = False
                    self._log("ATTACH_DENIED: Project is currently open in another window.")
                    return
            except Exception as e:
                self._log(f"Lock Validation Error: {e}")

        try:
            # Create our own lock
            self.lock_file.write_text(f"PID: {os.getpid()}")
            # Store version info for migration support
            self.version_file.write_text(json.dumps({
                "engine_version": self.VERSION, 
                "attached_at": time.time(),
                "project_id": self.project_id
            }, indent=4))
            
            self._engine_active = True
            self._log(f"Engine attached to {self.project_id} successfully.")
        except Exception as e:
            self._engine_active = False
            self._handle_error(f"Critical Lock Failure: {e}")

    def detach(self):
        """Gracefully shuts down the engine, ensuring all data is flushed."""
        if not self._engine_active:
            return

        self._log("Detaching engine. Performing final sync...")
        self.force_sync()

        with self._write_lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
        
        if self.lock_file.exists():
            self.lock_file.unlink()
            
        self._engine_active = False
        self._log("Engine detached. Lock released.")

    def is_active(self) -> bool:
        """Returns True if the engine is healthy and holds the lock."""
        return self._engine_active

    # --------------------------------------------------------------------------
    # CORE DATA OPERATIONS
    # --------------------------------------------------------------------------

    @requires_active
    def stage_update(self, data: dict, chunk_name: str):
        """
        Updates memory buffer and resets the disk-write timer.
        This ensures UI responsiveness while preventing excessive disk wear.
        """
        with self._write_lock:
            # 1. Update the 'In-Memory' shard
            self._staged_data[chunk_name] = copy.deepcopy(data)
            
            # 2. Reset/Start the Debounce Timer
            if self._debounce_timer:
                self._debounce_timer.cancel()
            
            self._debounce_timer = threading.Timer(self.debounce_delay, self._commit_to_disk)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    @requires_active
    def fetch_chunk(self, chunk_name: str) -> dict:
        """
        High-integrity data retrieval. 
        Hierarchy: RAM -> Primary Disk -> Backup Disk.
        """
        # Step 1: Check Memory Buffer (Most recent but uncommitted data)
        with self._write_lock:
            if chunk_name in self._staged_data:
                return copy.deepcopy(self._staged_data[chunk_name])

        # Step 2: Try Primary JSON
        primary_file = self.chunks_path / f"{chunk_name}.json"
        backup_file = self.backup_path / f"{chunk_name}.bak"

        if primary_file.exists():
            try:
                with open(primary_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                self._log(f"Primary shard '{chunk_name}' corrupt. Trying backup...")

        # Step 3: Try Backup JSON (Self-Healing logic)
        if backup_file.exists():
            try:
                with open(backup_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Restore corrupted primary from backup automatically
                    self.stage_update(data, chunk_name)
                    return data
            except Exception as e:
                self._handle_error(f"Total data loss for shard '{chunk_name}': {e}")
        
        return {}

    def delete_project(self, confirmed: bool = False):
        """
        Removes the entire project folder and all its contents.
        """
        if not confirmed:
            self._log("Delete project rejected: Missing confirmation.")
            return False
        
        try:
            self.detach() # Release locks before deleting
            if self.project_path.exists():
                shutil.rmtree(self.project_path)
            self._log(f"Project '{self.project_id}' was successfully wiped.")
            return True
        except Exception as e:
            self._handle_error(f"Failed to delete project: {e}")
            return False

    @requires_active
    def force_sync(self):
        """Immediately writes all memory-staged data to disk."""
        with self._write_lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        self._commit_to_disk()

    # --------------------------------------------------------------------------
    # ATOMIC PERSISTENCE MECHANISM
    # --------------------------------------------------------------------------

    def _commit_to_disk(self):
        """
        The Atomic Write-Ahead-Log (WAL) Logic.
        Flow: Write Temp -> Backup Current -> Replace Current with Temp.
        """
        with self._write_lock:
            if not self._staged_data or not self._engine_active:
                return

            try:
                # Process all staged chunks in this batch
                for chunk_name in list(self._staged_data.keys()):
                    data = self._staged_data[chunk_name]
                    
                    p_file = self.chunks_path / f"{chunk_name}.json"
                    b_file = self.backup_path / f"{chunk_name}.bak"
                    t_file = self.chunks_path / f"{chunk_name}.tmp"

                    # 1. Write to temporary file (safest)
                    with open(t_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=4)
                        f.flush()
                        os.fsync(f.fileno()) # Force OS to write to physical hardware

                    # 2. Backup existing shard
                    if p_file.exists():
                        shutil.copy2(p_file, b_file)

                    # 3. Swap Temp with Primary
                    t_file.replace(p_file)

                    # 4. Remove from RAM staging
                    del self._staged_data[chunk_name]

                self._debounce_timer = None
                if self.on_sync:
                    self.on_sync()
                    
            except Exception as e:
                self._handle_error(f"Sync Failure: {e}")

    # --------------------------------------------------------------------------
    # SNAPSHOTS & RECOVERY
    # --------------------------------------------------------------------------

    @requires_active
    def create_checkpoint(self, label: str = "manual", notes: str = "", retention: int = 10):
        """
        Creates a time-stamped ZIP of the entire project state.
        """
        self.force_sync()
        
        # Sanitize label for filesystem
        clean_label = re.sub(r'[^\w\-_]', '_', label)[:30]
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        zip_name = f"cp_{clean_label}_{timestamp}.zip"
        zip_full_path = self.checkpoint_path / zip_name

        try:
            with zipfile.ZipFile(zip_full_path, "w", zipfile.ZIP_DEFLATED) as zf:
                # Add all chunks
                for f in self.chunks_path.glob("*.json"):
                    zf.write(f, arcname=f"chunks/{f.name}")
                
                # Add metadata
                meta = {
                    "timestamp": timestamp,
                    "label": label,
                    "notes": notes,
                    "engine_ver": self.VERSION
                }
                zf.writestr("checkpoint_meta.json", json.dumps(meta, indent=4))

            # Retention policy: Remove oldest if over limit
            history = sorted(self.checkpoint_path.glob("*.zip"), key=os.path.getmtime)
            while len(history) > retention:
                oldest = history.pop(0)
                oldest.unlink()

            return zip_name
        except Exception as e:
            self._handle_error(f"Checkpoint failed: {e}")
            return None

    @requires_active
    def restore_checkpoint(self, zip_name: str) -> bool:
        """
        Full system restoration from a ZIP file.
        """
        zip_full_path = self.checkpoint_path / zip_name
        if not zip_full_path.exists():
            return False

        try:
            # Atomic Restore Flow
            with self._write_lock:
                # 1. Kill any pending writes
                if self._debounce_timer:
                    self._debounce_timer.cancel()
                self._staged_data.clear()

                # 2. Wipe current active chunks
                for folder in [self.chunks_path, self.backup_path]:
                    for f in folder.glob("*"):
                        f.unlink()
                
                # 3. Extract ZIP
                with zipfile.ZipFile(zip_full_path, "r") as zf:
                    zf.extractall(path=self.project_path)
                
            self._log(f"Project successfully restored from {zip_name}")
            return True
        except Exception as e:
            self._handle_error(f"Restore failed: {e}")
            return False

    # --------------------------------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------------------------------

    def _initialize_env(self):
        """Creates directory structure and cleans up artifacts."""
        for path in [self.chunks_path, self.backup_path, self.checkpoint_path]:
            path.mkdir(parents=True, exist_ok=True)
        
        # Cleanup orphaned temp files from previous crashes
        for tmp_file in self.chunks_path.glob("*.tmp"):
            try:
                tmp_file.unlink()
            except: pass

    def get_health_report(self) -> dict:
        """Returns a diagnostic summary of the project state."""
        return {
            "active": self._engine_active,
            "project": self.project_id,
            "root_path": str(self.base_dir_path),
            "shards_count": len(list(self.chunks_path.glob("*.json"))),
            "checkpoints_count": len(list(self.checkpoint_path.glob("*.zip"))),
            "pending_syncs": len(self._staged_data)
        }

    def list_checkpoints(self) -> list:
        """Returns metadata for all available snapshots."""
        cp_list = []
        for zp in self.checkpoint_path.glob("*.zip"):
            try:
                with zipfile.ZipFile(zp, "r") as zf:
                    meta = json.loads(zf.read("checkpoint_meta.json"))
                    cp_list.append({
                        "filename": zp.name,
                        "label": meta.get("label"),
                        "date": meta.get("timestamp"),
                        "notes": meta.get("notes")
                    })
            except: continue
        return sorted(cp_list, key=lambda x: x['date'], reverse=True)

    def _log(self, message):
        timestamped_msg = f"[{time.strftime('%H:%M:%S')}] {message}"
        self.log_history.append(timestamped_msg)
        if len(self.log_history) > 50: self.log_history.pop(0)
        
        if self.on_status:
            self.on_status(message)
        else:
            print(timestamped_msg)

    def _handle_error(self, error_message):
        self._log(f"CRITICAL FAULT: {error_message}")
        if self.on_fault:
            self.on_fault(str(error_message))
