"""Small, read-only Psycopg boundary used by the Streamlit dashboard."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from typing import Any

import pandas as pd


class DashboardDatabaseError(RuntimeError):
    """A safe-to-display database configuration or query error."""


def database_url() -> str:
    """Return the project's established PostgreSQL connection setting."""
    value = os.getenv("DATABASE_URL")
    if not value:
        raise DashboardDatabaseError(
            "DATABASE_URL is not configured. Set it to the PostgreSQL database "
            "containing the loaded dw schema, then restart the dashboard."
        )
    return value


def database_cache_key() -> str:
    """Identify a connection for caching without exposing its connection string."""
    return hashlib.sha256(database_url().encode("utf-8")).hexdigest()[:12]


def fetch_dataframe(query: str, params: Sequence[Any] = ()) -> pd.DataFrame:
    """Execute one SELECT on a connection that PostgreSQL enforces as read-only."""
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise DashboardDatabaseError(
            "Psycopg 3 is not installed. Install dashboard/requirements.txt."
        ) from exc

    try:
        with psycopg.connect(
            database_url(),
            autocommit=True,
            row_factory=dict_row,
            application_name="global_ai_observatory_dashboard",
            options="-c default_transaction_read_only=on -c statement_timeout=45000",
        ) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
    except Exception as exc:
        # Do not echo the exception: driver messages can include connection details.
        raise DashboardDatabaseError(
            "The dashboard could not query the warehouse. Check DATABASE_URL, "
            "confirm that the dw schema is loaded, and verify database access."
        ) from exc

    return pd.DataFrame(rows)
