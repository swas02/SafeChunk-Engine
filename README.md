# 🚀 SafeChunkEngine v1.3.5

**Fault-Tolerant, Root-Aware, Sharded JSON Persistence Layer**

SafeChunkEngine is a professional-grade, crash-resistant JSON storage engine. It is designed for applications that require "database-like" safety (Atomic writes, WAL, Lock-awareness) using simple local JSON files.

---

# 📁 Storage Architecture

SafeChunkEngine centralizes all projects under a single **Root Directory** (default: `user_projects`).

```
user_projects/              <-- Root Directory
└── project_id/             <-- Specific Project Instance
    ├── .lock               <-- PID Ownership Lock
    ├── version.json        <-- Migration & Engine Metadata
    ├── chunks/             <-- Active JSON Shards (.json)
    ├── chunks_bak/         <-- Redundant Backups (.bak)
    └── checkpoints/        <-- Snapshot Archives (.zip)

```

### Folder Roles

| Component | Role | Resilience |
| --- | --- | --- |
| `chunks/` | Primary Data Store | Uses `.tmp` swap for atomic writes. |
| `chunks_bak/` | Disaster Recovery | Automatically mirrored before every write. |
| `checkpoints/` | Version History | Full ZIP archives with metadata support. |
| `.lock` | Concurrency Guard | Prevents two processes from corrupting the same project. |

---

# 🏁 Quick Start

### Installation

```bash
pip install psutil

```

### Basic Usage

```python
from safe_chunk_engine import SafeChunkEngine

# 1. Initialize Root-Aware Engine
# Use 'new' to create with auto-incrementing ID if name exists
engine, status = SafeChunkEngine.new("bridge_design", base_dir="my_data")

# 2. Stage Data (In-Memory Buffer)
# This is "Debounced" - it won't hit the disk for 1.5 seconds (customizable)
engine.stage_update({"material": "Steel", "safety_factor": 1.5}, "properties")

# 3. Force Immediate Sync
engine.force_sync()

# 4. Create a Snaphot
engine.create_checkpoint(label="pre_optimization", notes="Before changing steel grade")

# 5. Safe Shutdown
engine.detach()

```

---

# 🔐 Professional Lifecycle Management

### Factory Methods

Starting with v1.3.0, factory methods support `**kwargs` to pass configurations directly to the constructor.

* **`SafeChunkEngine.new(project_id, base_dir, **kwargs)`**: Creates a new project. If `bridge` exists, it creates `bridge_1`.
* **`SafeChunkEngine.open(project_id, base_dir, **kwargs)`**: Opens an existing project. Returns `None` if the project is locked by another process.
* **`SafeChunkEngine.list_all_projects(base_dir)`**: Returns a list of all project IDs found in the root.

### The "Ghost Engine" Guard

Every data method is protected by the `@requires_active` decorator. If you attempt to write to an engine that has been deleted or detached, the operation is blocked safely instead of crashing your app.

---

# 🛡 Atomic Integrity Mechanism

SafeChunkEngine uses an **Atomic Swap** pattern to ensure data is never corrupted, even during a power failure:

1. **Serialize:** Data is written to `shard.tmp`.
2. **Verify:** `os.fsync()` forces the OS to commit the bits to physical hardware.
3. **Backup:** The current `shard.json` is copied to `shard.bak`.
4. **Swap:** `shard.tmp` replaces `shard.json` (an atomic OS operation).

---

# 📦 Checkpoints & Recovery

| Method | Action |
| --- | --- |
| `create_checkpoint(label, notes, retention)` | Zips all chunks/backups. Purges archives older than `retention` (default 10). |
| `restore_checkpoint(zip_name)` | **Destructive.** Wipes current state and restores the ZIP content. Includes integrity check. |
| `fetch_chunk(name)` | Automatically "Heals" primary data from `.bak` if corruption is detected. |

---

# 📊 Diagnostics & Monitoring

### Health Reports

Use `engine.get_health_report()` to monitor the engine status in your GUI:

```python
{
    "active": True,           # Lock status
    "project": "my_proj", 
    "root": "C:/data",
    "shards": 12,             # Count of active JSON files
    "orphans": 0              # Count of stale .tmp files (should be 0)
}

```

### Callbacks

Connect these to your UI to keep users informed:

* `on_status(msg)`: General activity logs.
* `on_sync()`: Triggered when the debounce timer successfully writes to disk.
* `on_fault(err)`: Triggered on critical IO or Lock errors.

---

# 🗑 Cleanup & Deletion

### `delete_project(confirmed=True)`

This is the most "Nuclear" option. It:

1. Calls `detach()` to stop all timers and release the lock.
2. Recursively deletes the entire project folder from the root.
3. **Critical:** Your app should set `self.engine = None` after this call.

---

# 🧠 Best Practices

1. **Centralize Roots:** Keep all projects in a dedicated `user_projects` folder.
2. **Graceful Exit:** Always put `engine.detach()` in your GUI's `closeEvent`.
3. **Sharding:** Don't put everything in one chunk. Split data into `profile`, `settings`, and `project_data` chunks for faster performance.
