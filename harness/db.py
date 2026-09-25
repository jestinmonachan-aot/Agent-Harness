"""SQLite persistence for the agent harness. Two tables:

  jobs  — one row per repo analysis session (repo_url, findings, migration/
          deployment results)
  steps — one row per (job_id, step_name) execution, updated live by
          worker.py so a crash mid-run still leaves an accurate status
          to resume from.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path("data/harness_data.db")


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # allow concurrent reader (Streamlit) + writer (worker)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_url        TEXT NOT NULL,
                repo_path       TEXT,
                findings        TEXT,
                migration_url   TEXT,
                deployment_url  TEXT,
                created_at      REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS steps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id      INTEGER NOT NULL REFERENCES jobs(id),
                step_name   TEXT NOT NULL,       -- 'analyze' | 'migrate' | 'deploy'
                status      TEXT NOT NULL,       -- 'running' | 'done' | 'error'
                result      TEXT,                -- JSON-ish string payload, step-specific
                error       TEXT,
                started_at  REAL NOT NULL,
                updated_at  REAL NOT NULL,
                UNIQUE(job_id, step_name)
            );
            """
        )


# ---------------------------------------------------------------- jobs ----

def create_job(repo_url: str) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (repo_url, created_at) VALUES (?, ?)",
            (repo_url, time.time()),
        )
        return cur.lastrowid


def get_job(job_id: int) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def save_repo_path(job_id: int, repo_path: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE jobs SET repo_path = ? WHERE id = ?", (repo_path, job_id))


def save_findings(job_id: int, findings: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE jobs SET findings = ? WHERE id = ?", (findings, job_id))


def save_migration(job_id: int, migration_url: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE jobs SET migration_url = ? WHERE id = ?", (migration_url, job_id))


def save_deployment(job_id: int, deployment_url: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE jobs SET deployment_url = ? WHERE id = ?", (deployment_url, job_id))


def find_latest_job_for_repo(repo_url: str) -> sqlite3.Row | None:
    """Used on Streamlit restart to resume a job instead of starting fresh."""
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE repo_url = ? ORDER BY created_at DESC LIMIT 1",
            (repo_url,),
        ).fetchone()


# --------------------------------------------------------------- steps ----

def start_step(job_id: int, step_name: str) -> None:
    now = time.time()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO steps (job_id, step_name, status, started_at, updated_at)
            VALUES (?, ?, 'running', ?, ?)
            ON CONFLICT(job_id, step_name) DO UPDATE SET
                status = 'running', result = NULL, error = NULL,
                started_at = excluded.started_at, updated_at = excluded.updated_at
            """,
            (job_id, step_name, now, now),
        )


def finish_step(job_id: int, step_name: str, status: str, result: str = "", error: str = "") -> None:
    assert status in ("done", "error")
    with _conn() as conn:
        conn.execute(
            """
            UPDATE steps SET status = ?, result = ?, error = ?, updated_at = ?
            WHERE job_id = ? AND step_name = ?
            """,
            (status, result, error, time.time(), job_id, step_name),
        )


def get_step(job_id: int, step_name: str) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM steps WHERE job_id = ? AND step_name = ?",
            (job_id, step_name),
        ).fetchone()


def get_all_steps(job_id: int) -> list[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM steps WHERE job_id = ? ORDER BY started_at", (job_id,)
        ).fetchall()