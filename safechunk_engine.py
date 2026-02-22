import json
import os
import copy
import threading
import time
import shutil
import zipfile
import psutil
from pathlib import Path


class SafeChunkEngine:
    """
    SafeChunk Engine: A fault-tolerant, sharded JSON persistence layer.

    Folder Structure (default):

        project_id/
            .lock
            chunks/
                chunk.json
                chunk.tmp
            chunks_bak/
                chunk.bak
            checkpoints/
                checkpoint_*.zip
    """

    def __init__(self, project_id: str, debounce_delay: float = 1.0, base_dir: str = None):
        self.project_id = project_id
        self.debounce_delay = debounce_delay

        # Path Configuration
        self.root_dir = Path(base_dir) if base_dir else Path(os.getcwd())
        self.project_path = self.root_dir / self.project_id

        self.chunks_path = self.project_path / "chunks"
        self.backup_path = self.project_path / "chunks_bak"
        self.checkpoint_path = self.project_path / "checkpoints"

        self.lock_file = self.project_path / ".lock"

        # Thread Management
        self._write_lock = threading.Lock()
        self._debounce_timer = None
        self._staged_data = {}

        # UI/Application Callbacks
        self.on_status = None
        self.on_sync = None
        self.on_fault = None  # fixed name

        # Lifecycle Initialization
        self._initialize_env()
        self._engine_active = False
        self.attach()

    # -----------------------------------------------------
    # PROJECT LIFECYCLE
    # -----------------------------------------------------

    def attach(self):
        """Claims the project directory and establishes a system lock."""
        if self.lock_file.exists():
            try:
                content = self.lock_file.read_text()
                old_pid = int(content.split(":")[1].strip())
                if not psutil.pid_exists(old_pid):
                    self._log("Detected stale lock. Overriding...")
                    self.lock_file.unlink()
                else:
                    self._engine_active = False
                    return
            except Exception:
                pass

        try:
            self.lock_file.write_text(f"PID: {os.getpid()}")
            self._engine_active = True
            self._log("Engine attached and locking project.")
        except Exception as e:
            self._engine_active = False
            self._handle_error(f"Failed to attach engine: {e}")

    def detach(self):
        """Flushes memory, kills timers, and releases project lock."""
        if not self._engine_active:
            return

        self.force_sync()

        with self._write_lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
                self._debounce_timer = None

        if self.lock_file.exists():
            self.lock_file.unlink()

        self._engine_active = False
        self._log("Engine detached.")

    def is_active(self) -> bool:
        return self._engine_active

    # -----------------------------------------------------
    # DATA OPERATIONS
    # -----------------------------------------------------

    def stage_update(self, data: dict, chunk_name: str):
        """Stages data for a debounced write."""
        if not self._engine_active:
            return

        with self._write_lock:
            self._staged_data[chunk_name] = copy.deepcopy(data)
            self._log(f"Changes staged for '{chunk_name}'...")

            if self._debounce_timer:
                self._debounce_timer.cancel()

            self._debounce_timer = threading.Timer(
                self.debounce_delay,
                self._commit_to_disk
            )
            self._debounce_timer.start()

    def force_sync(self):
        """Immediately commits staged data."""
        if self._debounce_timer:
            self._debounce_timer.cancel()
            self._debounce_timer = None

        self._commit_to_disk()

    def fetch_chunk(self, chunk_name: str) -> dict:
        """Reads a JSON shard with automatic self-healing."""
        primary = self.chunks_path / f"{chunk_name}.json"
        backup = self.backup_path / f"{chunk_name}.bak"

        def _read(path):
            if not path.exists():
                return None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None

        data = _read(primary)
        if data is not None:
            return data

        self._log(f"Primary '{chunk_name}' corrupted. Attempting backup recovery...")

        data = _read(backup)
        if data is not None:
            self.stage_update(data, chunk_name)
            return data

        return {}

    # -----------------------------------------------------
    # ATOMIC STORAGE ENGINE
    # -----------------------------------------------------

    def _commit_to_disk(self):
        with self._write_lock:
            if not self._staged_data:
                return

            self._log("Syncing to disk...")

            try:
                for chunk_name, data in self._staged_data.items():

                    f_path = self.chunks_path / f"{chunk_name}.json"
                    b_path = self.backup_path / f"{chunk_name}.bak"
                    t_path = self.chunks_path / f"{chunk_name}.tmp"

                    # 1. Serialize
                    try:
                        serialized = json.dumps(data, indent=4, default=str)
                    except Exception as e:
                        raise ValueError(f"Serialization failed for {chunk_name}: {e}")

                    # 2. Write temp
                    with open(t_path, "w", encoding="utf-8") as f:
                        f.write(serialized)
                        f.flush()
                        os.fsync(f.fileno())

                    # 3. Integrity verification
                    json.loads(serialized)

                    # 4. Rotate backup and swap
                    if f_path.exists():
                        shutil.copy2(f_path, b_path)

                    t_path.replace(f_path)

                self._staged_data.clear()
                self._debounce_timer = None

                self._log("Sync complete.")
                if self.on_sync:
                    self.on_sync()

            except Exception as e:
                self._handle_error(e)

    # -----------------------------------------------------
    # ARCHIVAL & DIAGNOSTICS
    # -----------------------------------------------------

    def create_checkpoint(self, label: str = "manual", retention: int = 10):
        """Creates a zipped archive of all chunks and backups."""
        self._log(f"Creating checkpoint '{label}'...")
        self.force_sync()

        ts = time.strftime("%Y%m%d_%H%M%S")
        zip_path = self.checkpoint_path / f"checkpoint_{label}_{ts}.zip"

        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:

                for file in self.chunks_path.glob("*.json"):
                    zipf.write(file, arcname=f"chunks/{file.name}")

                for file in self.backup_path.glob("*.bak"):
                    zipf.write(file, arcname=f"chunks_bak/{file.name}")

            # Retention management
            zips = sorted(self.checkpoint_path.glob("*.zip"), key=os.path.getmtime)
            while len(zips) > retention:
                zips.pop(0).unlink()

            self._log("Checkpoint achieved.")

        except Exception as e:
            self._handle_error(f"Checkpoint failed: {e}")

    def get_health_report(self) -> dict:
        """Returns project diagnostics."""
        return {
            "engine_active": self._engine_active,
            "dirty_buffer": len(self._staged_data) > 0,
            "storage_usage": psutil.disk_usage(self.project_path).percent,
            "orphaned_artifacts": len(list(self.chunks_path.glob("*.tmp"))),
            "shards": len(list(self.chunks_path.glob("*.json")))
        }

    # -----------------------------------------------------
    # INTERNALS
    # -----------------------------------------------------

    def _initialize_env(self):
        self.project_path.mkdir(parents=True, exist_ok=True)
        self.chunks_path.mkdir(parents=True, exist_ok=True)
        self.backup_path.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.mkdir(parents=True, exist_ok=True)

    def _log(self, msg):
        if self.on_status:
            self.on_status(msg)

    def _handle_error(self, err):
        msg = f"Engine Error: {str(err)}"
        self._log(msg)
        if self.on_fault:
            self.on_fault(msg)
