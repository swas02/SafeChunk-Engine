# SafeChunkEngine

**Fault-Tolerant, Sharded JSON Persistence Layer**

SafeChunkEngine is a crash-resistant, lock-aware, atomic JSON storage engine designed for applications that need safe local persistence without using a full database.

It stores data in separate JSON “chunks” inside a structured project directory, with automatic backups, atomic writes, and self-healing recovery.

---

# 📁 Default Project Structure

When initialized, SafeChunkEngine automatically creates:

```
project_id/
│
├── .lock
│
├── chunks/
│   ├── users.json
│   ├── settings.json
│   └── logs.json
│
├── chunks_bak/
│   ├── users.bak
│   ├── settings.bak
│   └── logs.bak
│
└── checkpoints/
    └── checkpoint_manual_YYYYMMDD_HHMMSS.zip
```

### Folder Roles

| Folder         | Purpose                            |
| -------------- | ---------------------------------- |
| `chunks/`      | Active JSON data files             |
| `chunks_bak/`  | Backup copies of previous versions |
| `checkpoints/` | Full project zip archives          |
| `.lock`        | Process ownership lock             |

---

# 🚀 Installation

## Requirements

```bash
pip install psutil
```

---

# 🏁 Quick Start

```python
from safe_chunk_engine import SafeChunkEngine

engine = SafeChunkEngine("my_project")

# Write data
engine.stage_update({"name": "Alice"}, "users")

# Force immediate disk write
engine.force_sync()

# Read data
data = engine.fetch_chunk("users")
print(data)

engine.detach()
```

---

# 🔐 Engine Lifecycle

## Initialization

```python
engine = SafeChunkEngine(
    project_id="my_project",
    debounce_delay=1.0,      # seconds before write
    base_dir=None            # optional storage location
)
```

### What Happens Automatically

* Creates project folders
* Checks for existing `.lock`
* Detects stale PID
* Claims lock if safe

---

## Lock Protection

Only one active process can own a project.

```python
engine.is_active()
```

Returns:

* `True` → Safe to use
* `False` → Another process owns it

If the previous process crashed, the engine auto-detects stale locks.

---

## Clean Shutdown (Important)

Always detach on exit:

```python
engine.detach()
```

This:

* Flushes pending writes
* Cancels timers
* Removes lock
* Prevents corruption

Recommended:

```python
try:
    run_app()
finally:
    engine.detach()
```

---

# ✍️ Writing Data

## Debounced Write

```python
engine.stage_update(data_dict, "chunk_name")
```

Example:

```python
engine.stage_update({"theme": "dark"}, "settings")
```

### What Happens

1. Data is deep-copied
2. Stored in memory
3. Timer starts
4. Additional updates reset timer
5. After delay → atomic disk write

Prevents excessive disk operations.

---

## Force Immediate Sync

```python
engine.force_sync()
```

Use when:

* Closing application
* Before critical operations
* Before checkpoint

---

# 📖 Reading Data

```python
data = engine.fetch_chunk("users")
```

Returns:

* Valid dictionary if exists
* `{}` if missing
* Automatically heals from backup if corrupted

---

# 🛡 Atomic Write Process

Each write follows:

1. Serialize JSON
2. Write to `chunks/chunk.tmp`
3. Flush + `fsync()`
4. Verify JSON integrity
5. Copy existing file to `chunks_bak/`
6. Replace `.json` with `.tmp`

This guarantees:

* No partial writes
* Crash safety
* Backup fallback

---

# 🔄 Automatic Self-Healing

If a `.json` file becomes corrupted:

1. Engine detects invalid JSON
2. Attempts to read `.bak`
3. Restores from backup
4. Rewrites primary file

No manual recovery required.

---

# 📦 Checkpoints (Full Archive Backup)

Create a full project archive:

```python
engine.create_checkpoint(label="manual")
```

Creates:

```
checkpoints/checkpoint_manual_YYYYMMDD_HHMMSS.zip
```

Includes:

* `chunks/`
* `chunks_bak/`

---

## Retention Control

```python
engine.create_checkpoint(label="auto", retention=5)
```

Keeps only the latest 5 archives.

---

# 📊 Health Monitoring

```python
report = engine.get_health_report()
print(report)
```

Example output:

```python
{
  "engine_active": True,
  "dirty_buffer": False,
  "storage_usage": 42.5,
  "orphaned_artifacts": 0,
  "shards": 3
}
```

### Field Explanation

| Field                | Meaning                      |
| -------------------- | ---------------------------- |
| `engine_active`      | Lock ownership               |
| `dirty_buffer`       | Unsaved staged data          |
| `storage_usage`      | Disk usage %                 |
| `orphaned_artifacts` | Leftover `.tmp` files        |
| `shards`             | Number of active JSON chunks |

---

# 🔔 Callbacks (Optional UI Integration)

## Status Messages

```python
def status(msg):
    print("STATUS:", msg)

engine.on_status = status
```

---

## Sync Complete Callback

```python
def on_sync():
    print("Disk sync complete")

engine.on_sync = on_sync
```

---

## Error Handling Callback

```python
def on_fault(error_message):
    print("ERROR:", error_message)

engine.on_fault = on_fault
```

---

# 🧠 Best Practices

✔ Separate logical domains into different chunks
✔ Use meaningful chunk names
✔ Always call `detach()`
✔ Use checkpoints before risky updates
✔ Monitor health report periodically
✔ Avoid storing non-serializable objects

---

# ⚠ Common Mistakes

| Mistake                               | Problem                  |
| ------------------------------------- | ------------------------ |
| Not detaching                         | Lock remains active      |
| Using same project in 2 processes     | Lock conflict            |
| Editing JSON manually during runtime  | Possible corruption      |
| Calling `force_sync()` too frequently | Performance impact       |
| Ignoring health report                | Disk issues go unnoticed |

---

# 🏗 Example Application Layout

```python
engine = SafeChunkEngine("crm_system")

engine.stage_update(users_data, "users")
engine.stage_update(settings_data, "settings")
engine.stage_update(logs_data, "logs")

engine.force_sync()
engine.create_checkpoint("daily_backup")

engine.detach()
```

Result:

```
crm_system/
    chunks/
        users.json
        settings.json
        logs.json
    chunks_bak/
        users.bak
        settings.bak
        logs.bak
    checkpoints/
        checkpoint_daily_backup_*.zip
```

---

# 📌 When To Use SafeChunkEngine

### Good For

* Desktop apps
* Lightweight local storage
* Crash-resistant JSON persistence
* Single-writer applications
* Structured file-based storage

### Not Ideal For

* High-concurrency distributed systems
* Complex relational queries
* Multi-node environments
* Large-scale database workloads

---

# 🏁 Summary

SafeChunkEngine provides:

* Atomic writes
* Backup rotation
* Self-healing recovery
* Lock protection
* Debounced persistence
* Full project checkpointing
* Clean folder separation (`chunks/` & `chunks_bak/`)

It delivers database-like safety while staying fully file-based and lightweight.
