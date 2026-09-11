#!/usr/bin/env python3
"""Preflight and launch the local Global AI Research Observatory dashboard."""

from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE_URL = "postgresql:///global_ai_observatory"


class PreflightError(RuntimeError):
    pass


def project_python() -> Path | None:
    """Return the project virtual-environment interpreter when it exists."""
    candidates = (
        ROOT / ".venv/bin/python",
        ROOT / ".venv/Scripts/python.exe",
    )
    return next((path for path in candidates if path.is_file()), None)


def use_project_environment() -> None:
    """Re-execute once with .venv so the documented command needs no activation."""
    interpreter = project_python()
    if (
        interpreter
        and Path(sys.executable).resolve() != interpreter.resolve()
        and os.getenv("OBSERVATORY_VENV_REEXEC") != "1"
    ):
        environment = os.environ.copy()
        environment["OBSERVATORY_VENV_REEXEC"] = "1"
        os.execve(
            str(interpreter),
            [str(interpreter), str(Path(__file__).resolve()), *sys.argv[1:]],
            environment,
        )


def database_url(override: str | None = None) -> str:
    return override or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL


def preflight(dsn: str | None = None) -> dict[str, object]:
    if sys.version_info < (3, 11):
        raise PreflightError("Python 3.11 or newer is required.")
    for module in ("psycopg", "pandas", "plotly", "streamlit"):
        try:
            importlib.import_module(module)
        except ImportError:
            raise PreflightError(
                "Required packages are missing. Install dashboard/requirements.txt."
            ) from None

    app = ROOT / "dashboard/app.py"
    if not app.is_file():
        raise PreflightError("Dashboard entry point dashboard/app.py is missing.")
    for path in (ROOT / "dashboard").glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    import psycopg

    required = (
        "dw.fact_publication",
        "dw.fact_country_year",
        "dw.dim_date",
        "dw.dim_year",
        "dw.dim_country",
        "dw.dim_topic",
        "dw.bridge_publication_topic",
        "dw.bridge_publication_country",
        "dw.bridge_publication_institution",
    )
    try:
        with psycopg.connect(
            database_url(dsn),
            connect_timeout=5,
            options="-c default_transaction_read_only=on -c statement_timeout=30000",
        ) as connection:
            missing = [
                table
                for table in required
                if connection.execute(
                    "SELECT to_regclass(%s)", (table,)
                ).fetchone()[0]
                is None
            ]
            if missing:
                raise PreflightError(
                    "Missing warehouse tables: " + ", ".join(missing)
                )
            publications = connection.execute(
                "SELECT count(*) FROM dw.fact_publication"
            ).fetchone()[0]
            if publications < 1:
                raise PreflightError("dw.fact_publication is empty.")
            years = [
                row[0]
                for row in connection.execute(
                    "SELECT calendar_year FROM dw.dim_year ORDER BY calendar_year"
                )
            ]
            if not set(range(2018, 2026)).issubset(years):
                raise PreflightError("dw.dim_year does not include 2018–2025.")
            for bridge in (
                "bridge_publication_topic",
                "bridge_publication_country",
                "bridge_publication_institution",
            ):
                populated = connection.execute(
                    f"SELECT EXISTS (SELECT 1 FROM dw.{bridge})"
                ).fetchone()[0]
                if not populated:
                    raise PreflightError(f"dw.{bridge} is empty.")
    except PreflightError:
        raise
    except Exception:
        raise PreflightError(
            "Could not validate the local PostgreSQL warehouse. "
            "Check that PostgreSQL is running and the project database is loaded."
        ) from None
    return {"publications": publications, "years": years}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        summary = preflight()
        print(
            f"Preflight PASS: {summary['publications']:,} publications; "
            "warehouse years include 2018–2025."
        )
        if args.check_only:
            return 0
        return subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(ROOT / "dashboard/app.py"),
            ],
            cwd=ROOT,
            env={**os.environ, "DATABASE_URL": database_url()},
        )
    except (PreflightError, SyntaxError) as exc:
        print(f"Dashboard startup failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Dashboard stopped.")
        return 0


if __name__ == "__main__":
    use_project_environment()
    raise SystemExit(main())
