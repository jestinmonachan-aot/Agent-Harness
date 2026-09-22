"""SQLite persistence for the harness pipeline: analysis, migration, deployment."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from datetime import datetime

DB_PATH = Path("harness_data.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        repo_url TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'created',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS findings (
        job_id TEXT PRIMARY KEY,
        content_md TEXT,
        created_at TEXT,
        FOREIGN KEY (job_id) REFERENCES jobs(id)
    );
    CREATE TABLE IF NOT EXISTS migration (
        job_id TEXT PRIMARY KEY,
        output_repo_url TEXT,
        status TEXT DEFAULT 'pending',
        updated_at TEXT,
        FOREIGN KEY (job_id) REFERENCES jobs(id)
    );
    CREATE TABLE IF NOT EXISTS deployment (
        job_id TEXT PRIMARY KEY,
        docker_status TEXT DEFAULT 'pending',
        app_url TEXT,
        updated_at TEXT,
        FOREIGN KEY (job_id) REFERENCES jobs(id)
    );
    """)
    conn.commit()
    conn.close()


def create_job(repo_url: str) -> str:
    job_id = str(uuid.uuid4())
    conn = get_conn()
    conn.execute(
        "INSERT INTO jobs (id, repo_url, status, created_at) VALUES (?, ?, ?, ?)",
        (job_id, repo_url, "created", datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    return job_id


def update_job_status(job_id: str, status: str):
    conn = get_conn()
    conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    conn.commit()
    conn.close()


def save_findings(job_id: str, content_md: str):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO findings (job_id, content_md, created_at) VALUES (?, ?, ?)",
        (job_id, content_md, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    update_job_status(job_id, "analyzed")


def save_migration(job_id: str, output_repo_url: str, status: str = "complete"):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO migration (job_id, output_repo_url, status, updated_at) VALUES (?, ?, ?, ?)",
        (job_id, output_repo_url, status, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    update_job_status(job_id, "migrated")


def save_deployment(job_id: str, app_url: str, docker_status: str = "complete"):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO deployment (job_id, docker_status, app_url, updated_at) VALUES (?, ?, ?, ?)",
        (job_id, docker_status, app_url, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    update_job_status(job_id, "deployed")


def get_job_full(job_id: str) -> dict:
    conn = get_conn()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    findings = conn.execute("SELECT * FROM findings WHERE job_id=?", (job_id,)).fetchone()
    migration = conn.execute("SELECT * FROM migration WHERE job_id=?", (job_id,)).fetchone()
    deployment = conn.execute("SELECT * FROM deployment WHERE job_id=?", (job_id,)).fetchone()
    conn.close()
    return {
        "job": dict(job) if job else None,
        "findings": dict(findings) if findings else None,
        "migration": dict(migration) if migration else None,
        "deployment": dict(deployment) if deployment else None,
    }


def list_jobs() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]