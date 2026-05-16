"""SQLite wrapper for reminders, birthdays, and anniversaries."""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_FILE = Path(__file__).parent / "assistant.db"


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                fire_at TIMESTAMP NOT NULL,
                kind TEXT NOT NULL DEFAULT 'once',  -- 'once' | 'yearly' | 'monthly' | 'weekly' | 'daily'
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                fired_at TIMESTAMP,                  -- when it last fired (NULL if pending)
                cancelled INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_pending ON reminders(fire_at)
                WHERE fired_at IS NULL AND cancelled = 0;
        """)


def add_reminder(text, fire_at, kind="once"):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders (text, fire_at, kind) VALUES (?, ?, ?)",
            (text, fire_at.isoformat(), kind),
        )
        return cur.lastrowid


def list_pending(include_recent=True, recent_hours=24):
    """Returns upcoming reminders + recently-fired one-offs (last N hours)."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(hours=recent_hours)).isoformat()
    with get_conn() as conn:
        if include_recent:
            rows = conn.execute("""
                SELECT * FROM reminders
                WHERE cancelled = 0 AND (
                    fired_at IS NULL                              -- not fired yet
                    OR kind != 'once'                             -- recurring
                    OR (kind = 'once' AND fired_at >= ?)          -- fired recently
                )
                ORDER BY fire_at
            """, (cutoff,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM reminders
                WHERE cancelled = 0 AND (fired_at IS NULL OR kind != 'once')
                ORDER BY fire_at
            """).fetchall()
        return [dict(r) for r in rows]


def list_all():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE cancelled = 0 ORDER BY fire_at"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_fired(reminder_id, when=None):
    when = when or datetime.now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE reminders SET fired_at = ? WHERE id = ?",
            (when.isoformat(), reminder_id),
        )


def reschedule(reminder_id, new_fire_at):
    with get_conn() as conn:
        conn.execute(
            "UPDATE reminders SET fire_at = ?, fired_at = NULL WHERE id = ?",
            (new_fire_at.isoformat(), reminder_id),
        )


def cancel(reminder_id):
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE reminders SET cancelled = 1 WHERE id = ?",
            (reminder_id,),
        )
        return cur.rowcount > 0


# Bootstrap on import
init_db()