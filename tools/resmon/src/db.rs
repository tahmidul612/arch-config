//! SQLite storage. The schema matches the original Python collector, so old databases keep working.

use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, Result};
use rusqlite::{Connection, OpenFlags, params};

use crate::paths;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER PRIMARY KEY,
    interval_s REAL,
    cpu_pct REAL,            -- whole machine, 0-100
    load1 REAL,
    mem_total_kb INTEGER,
    mem_avail_kb INTEGER,    -- used = total - available (excludes reclaimable cache)
    mem_cached_kb INTEGER,
    mem_shmem_kb INTEGER,
    swap_used_kb INTEGER,
    swapin_pages INTEGER,    -- delta since previous sample
    swapout_pages INTEGER,
    psi_cpu_some REAL,       -- avg10, percent of time stalled
    psi_mem_some REAL,
    psi_mem_full REAL,
    psi_io_some REAL,
    gpu_util REAL,
    gpu_mem_util REAL,
    vram_used_mb REAL,
    vram_total_mb REAL,
    gpu_power_w REAL,
    gpu_temp_c REAL
);
CREATE TABLE IF NOT EXISTS groups (
    ts INTEGER,
    grp TEXT,
    detail TEXT,             -- biggest member processes by memory
    nprocs INTEGER,
    mem_kb INTEGER,          -- PSS where readable, RSS otherwise
    cpu_pct REAL,            -- percent of ONE core (100 = one full core)
    vram_mb REAL,
    gpu_sm_pct REAL
);
CREATE INDEX IF NOT EXISTS groups_ts ON groups(ts);
CREATE TABLE IF NOT EXISTS events (
    ts INTEGER,
    kind TEXT,
    detail TEXT
);
";

pub fn path() -> PathBuf {
    std::env::var_os("RESMON_DB").map_or_else(|| paths::data_dir().join("resmon.db"), PathBuf::from)
}

/// Writer: creates the database if needed.
pub fn open_rw() -> Result<Connection> {
    let path = path();
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let db = Connection::open(&path).with_context(|| format!("opening {}", path.display()))?;
    db.busy_timeout(Duration::from_secs(10))?;
    db.execute_batch(SCHEMA)?;
    // Nothing filters by grp; that index cost ~20% of the file and an update on every insert.
    db.execute_batch("DROP INDEX IF EXISTS groups_grp")?;
    // Cap the WAL left on disk after a checkpoint. A reader holding a snapshot for hours
    // still blocks checkpoints (that once grew it to 12 GB), so readers must stay short.
    db.pragma_update(None, "journal_mode", "WAL")?;
    db.pragma_update(None, "journal_size_limit", 64 << 20)?;
    Ok(db)
}

/// Reader: never creates or migrates anything.
pub fn open_ro() -> Result<Connection> {
    let path = path();
    let db = Connection::open_with_flags(&path, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
        .with_context(|| format!("opening {} (is the collector running?)", path.display()))?;
    db.busy_timeout(Duration::from_secs(10))?;
    Ok(db)
}

pub fn event(db: &Connection, ts: i64, kind: &str, detail: &str) -> Result<()> {
    db.execute("INSERT INTO events VALUES (?1, ?2, ?3)", params![ts, kind, detail])?;
    Ok(())
}
