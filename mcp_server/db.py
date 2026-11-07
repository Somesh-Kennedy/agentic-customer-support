"""Thin psycopg layer over the tables Django migrates (see support/models.py)."""

import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgres://support:support@localhost:5432/support")


@contextmanager
def connect():
    """Yield a dict-row connection; commits on success, rolls back on error."""
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        yield conn


def log_event(conn, ticket_id: int, phase: str, actor: str, summary: str, payload: dict) -> None:
    conn.execute(
        """
        INSERT INTO ticket_events (ticket_id, phase, actor, summary, payload, created_at)
        SELECT %s, %s, %s, %s, %s, now()
        WHERE EXISTS (SELECT 1 FROM tickets WHERE id = %s)
        """,
        (ticket_id, phase, actor, summary, Jsonb(payload), ticket_id),
    )
