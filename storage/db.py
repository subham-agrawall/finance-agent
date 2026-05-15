import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "finance.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            description TEXT NOT NULL,
            merchant TEXT,
            amount REAL NOT NULL,
            account_id TEXT NOT NULL,
            person TEXT,
            is_group INTEGER DEFAULT 0,
            split_ratio REAL DEFAULT 1.0,
            adsy_ratio REAL DEFAULT NULL,
            category TEXT,
            source_category TEXT,
            reviewed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS merchant_cache (
            merchant_name TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    # Migration for existing DBs that predate adsy_ratio
    try:
        conn.execute("ALTER TABLE transactions ADD COLUMN adsy_ratio REAL DEFAULT NULL")
        conn.execute("UPDATE transactions SET adsy_ratio = 1.0 - split_ratio WHERE adsy_ratio IS NULL")
        conn.commit()
    except Exception:
        pass  # column already exists


def upsert_merchant_cache(conn: sqlite3.Connection, merchant: str, category: str, source: str) -> None:
    conn.execute(
        "INSERT INTO merchant_cache(merchant_name, category, source) VALUES(?,?,?) "
        "ON CONFLICT(merchant_name) DO UPDATE SET category=excluded.category, source=excluded.source",
        (merchant.upper(), category, source),
    )
    conn.commit()


def get_cached_category(conn: sqlite3.Connection, merchant: str) -> str | None:
    row = conn.execute(
        "SELECT category FROM merchant_cache WHERE merchant_name = ?",
        (merchant.upper(),),
    ).fetchone()
    return row["category"] if row else None
