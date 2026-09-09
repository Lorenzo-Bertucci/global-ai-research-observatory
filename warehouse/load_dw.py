#!/usr/bin/env python3
"""Build the PostgreSQL dimensional warehouse from the reconciled schema."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any


DEFAULT_SCHEMA = Path(__file__).with_name("schema.sql")

DW_TABLES = (
    "dim_date",
    "dim_year",
    "dim_country",
    "dim_topic",
    "dim_institution",
    "dim_source",
    "fact_publication",
    "fact_country_year",
    "bridge_publication_topic",
    "bridge_publication_country",
    "bridge_publication_institution",
)

REQUIRED_RECONCILED_COLUMNS = {
    "r_work": {
        "work_id",
        "publication_date",
        "work_type",
        "language",
        "cited_by_count",
        "primary_topic_id",
        "source_id",
        "is_open_access",
        "open_access_status",
    },
    "r_topic": {
        "topic_id",
        "topic_name",
        "subfield_id",
        "subfield_name",
        "field_id",
        "field_name",
        "domain_id",
        "domain_name",
    },
    "r_work_topic": {"work_id", "topic_id", "topic_rank", "topic_score"},
    "r_country": {
        "country_code_iso2",
        "country_code_iso3",
        "country_name",
        "region_id",
        "region_name",
        "income_level_id",
        "income_level_name",
        "has_world_bank_data",
    },
    "r_work_country": {"work_id", "country_code_iso2"},
    "r_institution": {
        "institution_id",
        "display_name",
        "institution_type",
        "country_code_iso2",
    },
    "r_work_institution": {"work_id", "institution_id"},
    "r_source": {"source_id", "display_name", "source_type", "issn_l"},
    "r_country_year_indicator": {
        "country_code_iso2",
        "year",
        "population",
        "gdp_current_usd",
        "gdp_per_capita_current_usd",
        "internet_users_pct",
        "rd_expenditure_pct_gdp",
    },
}

LOAD_STATEMENTS = (
    (
        "dim_date",
        """
        INSERT INTO dw.dim_date (
            date_key, full_date, day_of_month, month_number,
            quarter_number, calendar_year
        )
        SELECT
            EXTRACT(YEAR FROM publication_date)::INTEGER * 10000
                + EXTRACT(MONTH FROM publication_date)::INTEGER * 100
                + EXTRACT(DAY FROM publication_date)::INTEGER,
            publication_date,
            EXTRACT(DAY FROM publication_date)::SMALLINT,
            EXTRACT(MONTH FROM publication_date)::SMALLINT,
            EXTRACT(QUARTER FROM publication_date)::SMALLINT,
            EXTRACT(YEAR FROM publication_date)::SMALLINT
        FROM (
            SELECT DISTINCT publication_date
            FROM reconciled.r_work
            WHERE publication_date IS NOT NULL
        ) dates
        ORDER BY publication_date
        """,
    ),
    (
        "dim_year",
        """
        INSERT INTO dw.dim_year (calendar_year)
        SELECT DISTINCT year
        FROM reconciled.r_country_year_indicator
        ORDER BY year
        """,
    ),
    (
        "dim_country",
        """
        INSERT INTO dw.dim_country (
            country_code_iso2, country_code_iso3, country_name,
            region_id, region_name, income_level_id, income_level_name,
            has_world_bank_data
        )
        SELECT
            country_code_iso2, country_code_iso3, country_name,
            region_id, region_name, income_level_id, income_level_name,
            has_world_bank_data
        FROM reconciled.r_country
        ORDER BY country_code_iso2
        """,
    ),
    (
        "dim_topic",
        """
        INSERT INTO dw.dim_topic (
            openalex_topic_id, topic_name, subfield_id, subfield_name,
            field_id, field_name, domain_id, domain_name
        )
        SELECT
            topic_id, topic_name, subfield_id, subfield_name,
            field_id, field_name, domain_id, domain_name
        FROM reconciled.r_topic
        ORDER BY topic_id
        """,
    ),
    (
        "dim_institution",
        """
        INSERT INTO dw.dim_institution (
            openalex_institution_id, institution_name,
            institution_type, country_code_iso2
        )
        SELECT institution_id, display_name, institution_type, country_code_iso2
        FROM reconciled.r_institution
        ORDER BY institution_id
        """,
    ),
    (
        "dim_source",
        """
        INSERT INTO dw.dim_source (
            openalex_source_id, source_name, source_type, issn_l
        )
        SELECT source_id, display_name, source_type, issn_l
        FROM reconciled.r_source
        ORDER BY source_id
        """,
    ),
    (
        "fact_publication",
        """
        INSERT INTO dw.fact_publication (
            openalex_work_id, date_key, source_key, publication_type,
            language, is_open_access, open_access_status,
            publication_count, citation_count
        )
        SELECT
            work.work_id,
            date_dim.date_key,
            source_dim.source_key,
            work.work_type,
            work.language,
            work.is_open_access,
            work.open_access_status,
            1,
            work.cited_by_count
        FROM reconciled.r_work work
        LEFT JOIN dw.dim_date date_dim
            ON date_dim.full_date = work.publication_date
        LEFT JOIN dw.dim_source source_dim
            ON source_dim.openalex_source_id = work.source_id
        ORDER BY work.work_id
        """,
    ),
    (
        "fact_country_year",
        """
        INSERT INTO dw.fact_country_year (
            country_key, year_key, population, gdp_current_usd,
            gdp_per_capita_current_usd, internet_users_pct,
            rd_expenditure_pct_gdp
        )
        SELECT
            country_dim.country_key,
            year_dim.year_key,
            indicator.population,
            indicator.gdp_current_usd,
            indicator.gdp_per_capita_current_usd,
            indicator.internet_users_pct,
            indicator.rd_expenditure_pct_gdp
        FROM reconciled.r_country_year_indicator indicator
        JOIN dw.dim_country country_dim
            ON country_dim.country_code_iso2 = indicator.country_code_iso2
        JOIN dw.dim_year year_dim
            ON year_dim.calendar_year = indicator.year
        ORDER BY indicator.country_code_iso2, indicator.year
        """,
    ),
    (
        "bridge_publication_topic",
        """
        INSERT INTO dw.bridge_publication_topic (
            publication_key, topic_key, topic_rank,
            topic_score, is_primary_topic, fractional_weight
        )
        SELECT
            publication.publication_key,
            topic.topic_key,
            work_topic.topic_rank,
            work_topic.topic_score,
            COALESCE(work_topic.topic_id = work.primary_topic_id, FALSE),
            1::NUMERIC / COUNT(*) OVER (PARTITION BY work_topic.work_id)
        FROM reconciled.r_work_topic work_topic
        JOIN reconciled.r_work work
            ON work.work_id = work_topic.work_id
        JOIN dw.fact_publication publication
            ON publication.openalex_work_id = work_topic.work_id
        JOIN dw.dim_topic topic
            ON topic.openalex_topic_id = work_topic.topic_id
        ORDER BY work_topic.work_id, work_topic.topic_rank
        """,
    ),
    (
        "bridge_publication_country",
        """
        INSERT INTO dw.bridge_publication_country (
            publication_key, country_key, fractional_weight
        )
        SELECT
            publication.publication_key,
            country.country_key,
            1::NUMERIC / COUNT(*) OVER (PARTITION BY work_country.work_id)
        FROM reconciled.r_work_country work_country
        JOIN dw.fact_publication publication
            ON publication.openalex_work_id = work_country.work_id
        JOIN dw.dim_country country
            ON country.country_code_iso2 = work_country.country_code_iso2
        ORDER BY work_country.work_id, work_country.country_code_iso2
        """,
    ),
    (
        "bridge_publication_institution",
        """
        INSERT INTO dw.bridge_publication_institution (
            publication_key, institution_key, fractional_weight
        )
        SELECT
            publication.publication_key,
            institution.institution_key,
            1::NUMERIC / COUNT(*) OVER (PARTITION BY work_institution.work_id)
        FROM reconciled.r_work_institution work_institution
        JOIN dw.fact_publication publication
            ON publication.openalex_work_id = work_institution.work_id
        JOIN dw.dim_institution institution
            ON institution.openalex_institution_id = work_institution.institution_id
        ORDER BY work_institution.work_id, work_institution.institution_id
        """,
    ),
)


class WarehouseError(RuntimeError):
    """Raised when the reconciled source or dimensional load is inconsistent."""


def _scalar(connection: Any, query: str) -> int:
    return connection.execute(query).fetchone()[0]


def validate_reconciled_schema(connection: Any) -> None:
    """Require the frozen reconciled v1 contract used by the warehouse."""
    rows = connection.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'reconciled'"
    ).fetchall()
    actual: dict[str, set[str]] = {}
    for table, column in rows:
        actual.setdefault(table, set()).add(column)
    problems = []
    for table, required_columns in REQUIRED_RECONCILED_COLUMNS.items():
        missing = sorted(required_columns - actual.get(table, set()))
        if missing:
            problems.append(f"{table}: {', '.join(missing)}")
    if problems:
        raise WarehouseError(
            "Required reconciled tables or columns are missing: " + "; ".join(problems)
        )


def _run_post_load_checks(connection: Any) -> dict[str, int]:
    """Run direct dimensional grain, lineage, bridge, and coverage checks."""
    counts = {
        table: _scalar(connection, f"SELECT count(*) FROM dw.{table}")
        for table in DW_TABLES
    }
    expected_counts = {
        "dim_date": _scalar(
            connection,
            "SELECT count(DISTINCT publication_date) FROM reconciled.r_work "
            "WHERE publication_date IS NOT NULL",
        ),
        "dim_year": _scalar(
            connection,
            "SELECT count(DISTINCT year) FROM reconciled.r_country_year_indicator",
        ),
        "dim_country": _scalar(connection, "SELECT count(*) FROM reconciled.r_country"),
        "dim_topic": _scalar(connection, "SELECT count(*) FROM reconciled.r_topic"),
        "dim_institution": _scalar(
            connection, "SELECT count(*) FROM reconciled.r_institution"
        ),
        "dim_source": _scalar(connection, "SELECT count(*) FROM reconciled.r_source"),
        "fact_publication": _scalar(connection, "SELECT count(*) FROM reconciled.r_work"),
        "fact_country_year": _scalar(
            connection, "SELECT count(*) FROM reconciled.r_country_year_indicator"
        ),
        "bridge_publication_topic": _scalar(
            connection, "SELECT count(*) FROM reconciled.r_work_topic"
        ),
        "bridge_publication_country": _scalar(
            connection, "SELECT count(*) FROM reconciled.r_work_country"
        ),
        "bridge_publication_institution": _scalar(
            connection, "SELECT count(*) FROM reconciled.r_work_institution"
        ),
    }
    for table, expected in expected_counts.items():
        if counts[table] != expected:
            raise WarehouseError(
                f"Post-load row count mismatch for {table}: "
                f"expected {expected}, got {counts[table]}"
            )

    duplicate_checks = {
        "dim_date.full_date": (
            "SELECT count(*) FROM (SELECT full_date FROM dw.dim_date "
            "GROUP BY full_date HAVING count(*) > 1) duplicates"
        ),
        "dim_year.calendar_year": (
            "SELECT count(*) FROM (SELECT calendar_year FROM dw.dim_year "
            "GROUP BY calendar_year HAVING count(*) > 1) duplicates"
        ),
        "dim_country.iso2": (
            "SELECT count(*) FROM (SELECT country_code_iso2 FROM dw.dim_country "
            "GROUP BY country_code_iso2 HAVING count(*) > 1) duplicates"
        ),
        "dim_topic.openalex_topic_id": (
            "SELECT count(*) FROM (SELECT openalex_topic_id FROM dw.dim_topic "
            "GROUP BY openalex_topic_id HAVING count(*) > 1) duplicates"
        ),
        "dim_institution.openalex_institution_id": (
            "SELECT count(*) FROM (SELECT openalex_institution_id FROM dw.dim_institution "
            "GROUP BY openalex_institution_id HAVING count(*) > 1) duplicates"
        ),
        "dim_source.openalex_source_id": (
            "SELECT count(*) FROM (SELECT openalex_source_id FROM dw.dim_source "
            "GROUP BY openalex_source_id HAVING count(*) > 1) duplicates"
        ),
        "fact_publication.openalex_work_id": (
            "SELECT count(*) FROM (SELECT openalex_work_id FROM dw.fact_publication "
            "GROUP BY openalex_work_id HAVING count(*) > 1) duplicates"
        ),
        "fact_country_year.grain": (
            "SELECT count(*) FROM (SELECT country_key, year_key FROM dw.fact_country_year "
            "GROUP BY country_key, year_key HAVING count(*) > 1) duplicates"
        ),
        "bridge_publication_topic.grain": (
            "SELECT count(*) FROM (SELECT publication_key, topic_key "
            "FROM dw.bridge_publication_topic GROUP BY publication_key, topic_key "
            "HAVING count(*) > 1) duplicates"
        ),
        "bridge_publication_country.grain": (
            "SELECT count(*) FROM (SELECT publication_key, country_key "
            "FROM dw.bridge_publication_country GROUP BY publication_key, country_key "
            "HAVING count(*) > 1) duplicates"
        ),
        "bridge_publication_institution.grain": (
            "SELECT count(*) FROM (SELECT publication_key, institution_key "
            "FROM dw.bridge_publication_institution GROUP BY publication_key, institution_key "
            "HAVING count(*) > 1) duplicates"
        ),
    }
    for grain, query in duplicate_checks.items():
        if _scalar(connection, query):
            raise WarehouseError(f"Post-load duplicate natural or grain key: {grain}")

    if _scalar(
        connection,
        "SELECT count(*) FROM dw.fact_publication publication "
        "JOIN reconciled.r_work work ON work.work_id = publication.openalex_work_id "
        "WHERE publication.publication_count <> 1 "
        "OR publication.citation_count IS DISTINCT FROM work.cited_by_count",
    ):
        raise WarehouseError("Publication measures differ from reconciled works")

    if _scalar(
        connection,
        "SELECT count(*) FROM dw.bridge_publication_topic bridge "
        "JOIN dw.fact_publication publication "
        "ON publication.publication_key = bridge.publication_key "
        "JOIN dw.dim_topic topic ON topic.topic_key = bridge.topic_key "
        "JOIN reconciled.r_work work "
        "ON work.work_id = publication.openalex_work_id "
        "WHERE bridge.is_primary_topic IS DISTINCT FROM "
        "COALESCE(topic.openalex_topic_id = work.primary_topic_id, FALSE)",
    ):
        raise WarehouseError("Primary-topic markers differ from reconciled works")
    if _scalar(
        connection,
        "SELECT count(*) FROM dw.bridge_publication_topic WHERE is_primary_topic",
    ) != _scalar(
        connection,
        "SELECT count(*) FROM reconciled.r_work WHERE primary_topic_id IS NOT NULL",
    ):
        raise WarehouseError("Primary-topic marker coverage is incomplete")

    if _scalar(
        connection,
        "SELECT count(*) FROM dw.fact_country_year fact "
        "JOIN dw.dim_country country ON country.country_key = fact.country_key "
        "JOIN dw.dim_year year_dim ON year_dim.year_key = fact.year_key "
        "JOIN reconciled.r_country_year_indicator source "
        "ON source.country_code_iso2 = country.country_code_iso2 "
        "AND source.year = year_dim.calendar_year "
        "WHERE fact.population IS DISTINCT FROM source.population "
        "OR fact.gdp_current_usd IS DISTINCT FROM source.gdp_current_usd "
        "OR fact.gdp_per_capita_current_usd IS DISTINCT FROM source.gdp_per_capita_current_usd "
        "OR fact.internet_users_pct IS DISTINCT FROM source.internet_users_pct "
        "OR fact.rd_expenditure_pct_gdp IS DISTINCT FROM source.rd_expenditure_pct_gdp",
    ):
        raise WarehouseError("Country-year measures differ from reconciled indicators")

    orphan_checks = {
        "fact_publication": (
            "SELECT count(*) FROM dw.fact_publication fact "
            "LEFT JOIN dw.dim_date date_dim ON date_dim.date_key = fact.date_key "
            "LEFT JOIN dw.dim_source source_dim ON source_dim.source_key = fact.source_key "
            "WHERE (fact.date_key IS NOT NULL AND date_dim.date_key IS NULL) "
            "OR (fact.source_key IS NOT NULL AND source_dim.source_key IS NULL)"
        ),
        "fact_country_year": (
            "SELECT count(*) FROM dw.fact_country_year fact "
            "LEFT JOIN dw.dim_country country ON country.country_key = fact.country_key "
            "LEFT JOIN dw.dim_year year_dim ON year_dim.year_key = fact.year_key "
            "WHERE country.country_key IS NULL OR year_dim.year_key IS NULL"
        ),
        "bridge_publication_topic": (
            "SELECT count(*) FROM dw.bridge_publication_topic bridge "
            "LEFT JOIN dw.fact_publication publication "
            "ON publication.publication_key = bridge.publication_key "
            "LEFT JOIN dw.dim_topic topic ON topic.topic_key = bridge.topic_key "
            "WHERE publication.publication_key IS NULL OR topic.topic_key IS NULL"
        ),
        "bridge_publication_country": (
            "SELECT count(*) FROM dw.bridge_publication_country bridge "
            "LEFT JOIN dw.fact_publication publication "
            "ON publication.publication_key = bridge.publication_key "
            "LEFT JOIN dw.dim_country country ON country.country_key = bridge.country_key "
            "WHERE publication.publication_key IS NULL OR country.country_key IS NULL"
        ),
        "bridge_publication_institution": (
            "SELECT count(*) FROM dw.bridge_publication_institution bridge "
            "LEFT JOIN dw.fact_publication publication "
            "ON publication.publication_key = bridge.publication_key "
            "LEFT JOIN dw.dim_institution institution "
            "ON institution.institution_key = bridge.institution_key "
            "WHERE publication.publication_key IS NULL OR institution.institution_key IS NULL"
        ),
    }
    for table, query in orphan_checks.items():
        if _scalar(connection, query):
            raise WarehouseError(f"Post-load orphan foreign keys found in {table}")

    for bridge in (
        "bridge_publication_topic",
        "bridge_publication_country",
        "bridge_publication_institution",
    ):
        invalid_sums = _scalar(
            connection,
            f"SELECT count(*) FROM (SELECT publication_key FROM dw.{bridge} "
            "GROUP BY publication_key "
            "HAVING abs(sum(fractional_weight) - 1) > 0.000000000001) invalid",
        )
        if invalid_sums:
            raise WarehouseError(f"Fractional weights do not sum to one in {bridge}")

    if _scalar(
        connection,
        "SELECT count(*) FROM dw.fact_country_year fact "
        "JOIN dw.dim_country country ON country.country_key = fact.country_key "
        "WHERE NOT country.has_world_bank_data",
    ):
        raise WarehouseError(
            "Country-year facts exist for geographies without frozen World Bank data"
        )
    return counts


def load_dw(database_url: str, schema_path: Path = DEFAULT_SCHEMA) -> dict[str, int]:
    """Replace only the dw schema in one atomic, set-based PostgreSQL load."""
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL driver missing; install it with: "
            "python3 -m pip install 'psycopg[binary]>=3.2,<4'"
        ) from exc

    ddl = Path(schema_path).read_text(encoding="utf-8")
    with psycopg.connect(database_url) as connection:
        validate_reconciled_schema(connection)
        connection.execute(ddl)
        for _name, statement in LOAD_STATEMENTS:
            connection.execute(statement)
        return _run_post_load_checks(connection)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    try:
        counts = load_dw(database_url, schema_path=args.schema)
    except (WarehouseError, RuntimeError) as exc:
        raise SystemExit(f"DW load failed: {exc}") from exc
    summary = ", ".join(f"{table}={count}" for table, count in counts.items())
    print(f"DW load complete: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
