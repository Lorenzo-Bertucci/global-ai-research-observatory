#!/usr/bin/env python3
"""Build the PostgreSQL reconciled layer from the OpenAlex and World Bank raw JSONL."""

from __future__ import annotations

import argparse
import json
import hashlib
import os
import re
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPENALEX = ROOT / "data/raw/openalex_ai_works.jsonl"
DEFAULT_WORLD_BANK = ROOT / "data/raw/world_bank_country_year.jsonl"
DEFAULT_SCHEMA = Path(__file__).with_name("schema.sql")
BATCH_SIZE = 1_000

# Valid ISO geographies observed in OpenAlex but absent from the frozen WDI extract.
# Add a row here only when its identity can be established without remapping it.
OPENALEX_ONLY_GEOGRAPHY_POLICY = {
    "GF": {"country_code_iso3": "GUF", "country_name": "French Guiana"},
    "GP": {"country_code_iso3": "GLP", "country_name": "Guadeloupe"},
    "MQ": {"country_code_iso3": "MTQ", "country_name": "Martinique"},
    "MS": {"country_code_iso3": "MSR", "country_name": "Montserrat"},
    "RE": {"country_code_iso3": "REU", "country_name": "Réunion"},
    "TW": {"country_code_iso3": "TWN", "country_name": "Taiwan"},
}

COUNTRY_FIELDS = (
    "country_code_iso2",
    "country_code_iso3",
    "country_name",
    "region_id",
    "region_name",
    "income_level_id",
    "income_level_name",
    "has_world_bank_data",
)
TOPIC_FIELDS = (
    "topic_id",
    "topic_name",
    "subfield_id",
    "subfield_name",
    "field_id",
    "field_name",
    "domain_id",
    "domain_name",
)
INSTITUTION_FIELDS = (
    "institution_id",
    "display_name",
    "institution_type",
    "country_code_iso2",
)
SOURCE_FIELDS = ("source_id", "display_name", "source_type", "issn_l")
WORK_FIELDS = (
    "work_id",
    "doi",
    "title",
    "publication_date",
    "publication_year",
    "work_type",
    "language",
    "cited_by_count",
    "primary_topic_id",
    "source_id",
    "is_open_access",
)
INDICATOR_FIELDS = (
    "country_code_iso2",
    "year",
    "population",
    "gdp_current_usd",
    "gdp_per_capita_current_usd",
    "internet_users_pct",
    "rd_expenditure_pct_gdp",
)
TABLE_FIELDS = {
    "r_country": COUNTRY_FIELDS,
    "r_topic": TOPIC_FIELDS,
    "r_institution": INSTITUTION_FIELDS,
    "r_source": SOURCE_FIELDS,
    "r_work": WORK_FIELDS,
    "r_work_topic": ("work_id", "topic_id", "topic_rank", "topic_score"),
    "r_work_country": ("work_id", "country_code_iso2"),
    "r_work_institution": ("work_id", "institution_id"),
    "r_country_year_indicator": INDICATOR_FIELDS,
}
LOAD_ORDER = tuple(TABLE_FIELDS)


class ReconciliationError(ValueError):
    """Raised when raw records cannot be reconciled without losing information."""


def _jsonl_rows(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    if not path.is_file():
        raise ReconciliationError(f"Input file does not exist: {path}")
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReconciliationError(
                    f"Invalid JSON in {path} at line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise ReconciliationError(
                    f"Expected a JSON object in {path} at line {line_number}"
                )
            yield line_number, row


def _text(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ReconciliationError(f"Missing required field {field}")
        return None
    if not isinstance(value, str):
        raise ReconciliationError(f"Field {field} must be a string or null")
    value = value.strip()
    if not value:
        if required:
            raise ReconciliationError(f"Field {field} cannot be empty")
        return None
    return value


def _country_code(value: Any, field: str, length: int) -> str:
    code = _text(value, field, required=True)
    assert code is not None
    if not re.fullmatch(rf"[A-Z]{{{length}}}", code):
        raise ReconciliationError(
            f"Field {field} must be an uppercase {length}-letter code, got {code!r}"
        )
    return code


def _integer(value: Any, field: str, *, required: bool = False) -> int | None:
    if value is None:
        if required:
            raise ReconciliationError(f"Missing required field {field}")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReconciliationError(f"Field {field} must be an integer or null")
    return value


def _number(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReconciliationError(f"Field {field} must be numeric or null")
    return Decimal(str(value))


def _boolean(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ReconciliationError(f"Field {field} must be boolean or null")
    return value


def _date(value: Any, field: str) -> date | None:
    text = _text(value, field)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ReconciliationError(f"Field {field} is not an ISO date: {text!r}") from exc


def _list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReconciliationError(f"Field {field} must be a list or null")
    return value


def _object(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ReconciliationError(f"Field {field} must be an object or null")
    return value


def _nested_entity(value: Any, field: str) -> tuple[str | None, str | None]:
    entity = _object(value, field)
    if entity is None:
        return None, None
    return _text(entity.get("id"), f"{field}.id"), _text(
        entity.get("display_name"), f"{field}.display_name"
    )


def _merge_entity(
    destination: dict[Any, tuple[Any, ...]],
    key: Any,
    row: tuple[Any, ...],
    fields: tuple[str, ...],
    entity_name: str,
) -> None:
    existing = destination.get(key)
    if existing is None:
        destination[key] = row
        return
    merged = list(existing)
    for index, (old, new) in enumerate(zip(existing, row)):
        if old is None and new is not None:
            merged[index] = new
        elif old is not None and new is not None and old != new:
            raise ReconciliationError(
                f"Conflicting {entity_name} {key!r} field {fields[index]}: "
                f"{old!r} != {new!r}"
            )
    destination[key] = tuple(merged)


def _topic_row(topic: dict[str, Any], field: str) -> tuple[Any, ...]:
    topic_id = _text(topic.get("id"), f"{field}.id", required=True)
    subfield_id, subfield_name = _nested_entity(topic.get("subfield"), f"{field}.subfield")
    field_id, field_name = _nested_entity(topic.get("field"), f"{field}.field")
    domain_id, domain_name = _nested_entity(topic.get("domain"), f"{field}.domain")
    return (
        topic_id,
        _text(topic.get("display_name"), f"{field}.display_name"),
        subfield_id,
        subfield_name,
        field_id,
        field_name,
        domain_id,
        domain_name,
    )


def _empty_data() -> dict[str, Any]:
    return {
        "r_country": {},
        "r_topic": {},
        "r_institution": {},
        "r_source": {},
        "r_work": {},
        "r_work_topic": {},
        "r_work_country": set(),
        "r_work_institution": set(),
        "r_country_year_indicator": {},
        "indicator_nulls": Counter(),
    }


def _read_world_bank(path: Path, data: dict[str, Any]) -> None:
    for line_number, raw in _jsonl_rows(path):
        prefix = f"World Bank line {line_number}"
        iso2 = _country_code(raw.get("country_code_iso2"), f"{prefix}.country_code_iso2", 2)
        iso3 = _country_code(raw.get("country_code_iso3"), f"{prefix}.country_code_iso3", 3)
        country = (
            iso2,
            iso3,
            _text(raw.get("country_name"), f"{prefix}.country_name"),
            _text(raw.get("region_id"), f"{prefix}.region_id"),
            _text(raw.get("region_name"), f"{prefix}.region_name"),
            _text(raw.get("income_level_id"), f"{prefix}.income_level_id"),
            _text(raw.get("income_level_name"), f"{prefix}.income_level_name"),
            True,
        )
        _merge_entity(data["r_country"], iso2, country, COUNTRY_FIELDS, "country")

        year = _integer(raw.get("year"), f"{prefix}.year", required=True)
        assert year is not None
        key = (iso2, year)
        if key in data["r_country_year_indicator"]:
            raise ReconciliationError(f"Duplicate World Bank country-year key: {key}")
        measures = tuple(
            _number(raw.get(field), f"{prefix}.{field}") for field in INDICATOR_FIELDS[2:]
        )
        data["r_country_year_indicator"][key] = (iso2, year, *measures)
        for field, value in zip(INDICATOR_FIELDS[2:], measures):
            if value is None:
                data["indicator_nulls"][field] += 1

    if not data["r_country"] or not data["r_country_year_indicator"]:
        raise ReconciliationError(f"World Bank input is empty: {path}")
    iso3_values = [row[1] for row in data["r_country"].values()]
    if len(iso3_values) != len(set(iso3_values)):
        raise ReconciliationError("World Bank input maps more than one ISO2 code to the same ISO3 code")


def _read_openalex(path: Path, data: dict[str, Any]) -> None:
    used_country_codes: set[str] = set()
    for line_number, raw in _jsonl_rows(path):
        prefix = f"OpenAlex line {line_number}"
        work_id = _text(raw.get("id"), f"{prefix}.id", required=True)
        assert work_id is not None

        raw_title = _text(raw.get("title"), f"{prefix}.title")
        display_name = _text(raw.get("display_name"), f"{prefix}.display_name")
        if raw_title and display_name and raw_title != display_name:
            raise ReconciliationError(
                f"Conflicting title/display_name for work {work_id!r}: "
                f"{raw_title!r} != {display_name!r}"
            )
        title = raw_title or display_name

        raw_topics = raw.get("topics")
        topic_ids: set[str] = set()
        for rank, value in enumerate(_list(raw_topics, f"{prefix}.topics"), 1):
            topic = _object(value, f"{prefix}.topics[{rank - 1}]")
            if topic is None:
                raise ReconciliationError(f"{prefix}.topics[{rank - 1}] cannot be null")
            topic_row = _topic_row(topic, f"{prefix}.topics[{rank - 1}]")
            topic_id = topic_row[0]
            _merge_entity(data["r_topic"], topic_id, topic_row, TOPIC_FIELDS, "topic")
            relation = (
                work_id,
                topic_id,
                rank,
                float(_number(topic.get("score"), f"{prefix}.topics[{rank - 1}].score"))
                if topic.get("score") is not None
                else None,
            )
            _merge_entity(
                data["r_work_topic"],
                (work_id, topic_id),
                relation,
                TABLE_FIELDS["r_work_topic"],
                "work-topic relationship",
            )
            topic_ids.add(topic_id)

        primary = _object(raw.get("primary_topic"), f"{prefix}.primary_topic")
        primary_topic_id = None
        if primary is not None:
            primary_row = _topic_row(primary, f"{prefix}.primary_topic")
            primary_topic_id = primary_row[0]
            _merge_entity(
                data["r_topic"], primary_topic_id, primary_row, TOPIC_FIELDS, "topic"
            )
            if primary_topic_id not in topic_ids:
                raise ReconciliationError(
                    f"Primary topic {primary_topic_id!r} for work {work_id!r} "
                    "is absent from topics[]"
                )

        source_id = None
        location = _object(raw.get("primary_location"), f"{prefix}.primary_location")
        source = _object(
            location.get("source") if location else None,
            f"{prefix}.primary_location.source",
        )
        if source is not None:
            source_id = _text(
                source.get("id"), f"{prefix}.primary_location.source.id", required=True
            )
            assert source_id is not None
            source_row = (
                source_id,
                _text(source.get("display_name"), f"{prefix}.primary_location.source.display_name"),
                _text(source.get("type"), f"{prefix}.primary_location.source.type"),
                _text(source.get("issn_l"), f"{prefix}.primary_location.source.issn_l"),
            )
            _merge_entity(data["r_source"], source_id, source_row, SOURCE_FIELDS, "source")

        for authorship_index, value in enumerate(
            _list(raw.get("authorships"), f"{prefix}.authorships")
        ):
            authorship = _object(value, f"{prefix}.authorships[{authorship_index}]")
            if authorship is None:
                raise ReconciliationError(
                    f"{prefix}.authorships[{authorship_index}] cannot be null"
                )
            for country_index, value in enumerate(
                _list(
                    authorship.get("countries"),
                    f"{prefix}.authorships[{authorship_index}].countries",
                )
            ):
                code = _country_code(
                    value,
                    f"{prefix}.authorships[{authorship_index}].countries[{country_index}]",
                    2,
                )
                used_country_codes.add(code)
                data["r_work_country"].add((work_id, code))

            for institution_index, value in enumerate(
                _list(
                    authorship.get("institutions"),
                    f"{prefix}.authorships[{authorship_index}].institutions",
                )
            ):
                institution = _object(
                    value,
                    f"{prefix}.authorships[{authorship_index}].institutions[{institution_index}]",
                )
                if institution is None:
                    raise ReconciliationError(
                        f"{prefix}.authorships[{authorship_index}].institutions[{institution_index}] "
                        "cannot be null"
                    )
                institution_id = _text(
                    institution.get("id"),
                    f"{prefix}.authorships[{authorship_index}].institutions[{institution_index}].id",
                    required=True,
                )
                assert institution_id is not None
                raw_code = institution.get("country_code")
                institution_country = (
                    _country_code(
                        raw_code,
                        f"{prefix}.authorships[{authorship_index}].institutions[{institution_index}].country_code",
                        2,
                    )
                    if raw_code is not None and raw_code != ""
                    else None
                )
                if institution_country:
                    used_country_codes.add(institution_country)
                    data["r_work_country"].add((work_id, institution_country))
                institution_row = (
                    institution_id,
                    _text(
                        institution.get("display_name"),
                        f"{prefix}.authorships[{authorship_index}].institutions[{institution_index}].display_name",
                    ),
                    _text(
                        institution.get("type"),
                        f"{prefix}.authorships[{authorship_index}].institutions[{institution_index}].type",
                    ),
                    institution_country,
                )
                _merge_entity(
                    data["r_institution"],
                    institution_id,
                    institution_row,
                    INSTITUTION_FIELDS,
                    "institution",
                )
                data["r_work_institution"].add((work_id, institution_id))

        open_access = _object(raw.get("open_access"), f"{prefix}.open_access")
        cited_by_count = _integer(raw.get("cited_by_count"), f"{prefix}.cited_by_count")
        if cited_by_count is not None and cited_by_count < 0:
            raise ReconciliationError(
                f"Field {prefix}.cited_by_count cannot be negative"
            )
        publication_date = _date(raw.get("publication_date"), f"{prefix}.publication_date")
        publication_year = _integer(raw.get("publication_year"), f"{prefix}.publication_year")
        if (
            publication_date is not None
            and publication_year is not None
            and publication_date.year != publication_year
        ):
            raise ReconciliationError(
                f"Publication date/year mismatch for work {work_id!r}: "
                f"{publication_date.isoformat()} != {publication_year}"
            )
        work_row = (
            work_id,
            _text(raw.get("doi"), f"{prefix}.doi"),
            title,
            publication_date,
            publication_year,
            _text(raw.get("type"), f"{prefix}.type"),
            _text(raw.get("language"), f"{prefix}.language"),
            cited_by_count,
            primary_topic_id,
            source_id,
            _boolean(open_access.get("is_oa"), f"{prefix}.open_access.is_oa")
            if open_access
            else None,
        )
        _merge_entity(data["r_work"], work_id, work_row, WORK_FIELDS, "work")

    if not data["r_work"]:
        raise ReconciliationError(f"OpenAlex input is empty: {path}")
    missing_from_world_bank = used_country_codes - set(data["r_country"])
    unrecognized = sorted(
        missing_from_world_bank - set(OPENALEX_ONLY_GEOGRAPHY_POLICY)
    )
    if unrecognized:
        raise ReconciliationError(
            "OpenAlex country codes are absent from the World Bank master and "
            "not recognized by the OpenAlex-only geography policy: "
            + ", ".join(unrecognized)
        )
    for code in sorted(missing_from_world_bank):
        geography = OPENALEX_ONLY_GEOGRAPHY_POLICY[code]
        country = (
            code,
            geography["country_code_iso3"],
            geography["country_name"],
            None,
            None,
            None,
            None,
            False,
        )
        _merge_entity(data["r_country"], code, country, COUNTRY_FIELDS, "country")

    iso3_values = [row[1] for row in data["r_country"].values()]
    if len(iso3_values) != len(set(iso3_values)):
        raise ReconciliationError(
            "Reconciled countries map more than one ISO2 code to the same ISO3 code"
        )

def build_reconciled_data(openalex_path: Path, world_bank_path: Path) -> dict[str, Any]:
    """Read, validate, deduplicate, and reconcile the two source-oriented inputs."""
    # File integrity is source-agnostic: no corpus-selection rules belong here.
    source_path = Path(openalex_path)
    manifest_path = source_path.with_name(source_path.name.replace("_works.jsonl", "_manifest.json"))
    if manifest_path != source_path and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("sha256"):
            digest = hashlib.sha256()
            with source_path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != manifest["sha256"]:
                raise ReconciliationError("Raw OpenAlex checksum differs from manifest; restore or publish a consistent extraction")
    data = _empty_data()
    _read_world_bank(Path(world_bank_path), data)
    _read_openalex(Path(openalex_path), data)
    return data


def geographic_coverage_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Summarize geographic identity separately from frozen World Bank coverage."""
    openalex_only = sorted(
        row[0] for row in data["r_country"].values() if not row[-1]
    )
    return {
        "reconciled_geographic_entities": len(data["r_country"]),
        "world_bank_geographic_entities": len(data["r_country"]) - len(openalex_only),
        "openalex_only_geographic_entities": openalex_only,
    }


def _chunks(rows: list[tuple[Any, ...]], size: int = BATCH_SIZE) -> Iterable[list[tuple[Any, ...]]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _rows_for_table(data: dict[str, Any], table: str) -> list[tuple[Any, ...]]:
    values = data[table]
    rows = list(values.values()) if isinstance(values, dict) else list(values)
    return sorted(rows, key=lambda row: tuple("" if value is None else str(value) for value in row))


def _insert_rows(connection: Any, table: str, fields: tuple[str, ...], rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    columns = ", ".join(fields)
    placeholders = ", ".join(["%s"] * len(fields))
    statement = f"INSERT INTO reconciled.{table} ({columns}) VALUES ({placeholders})"
    with connection.cursor() as cursor:
        for batch in _chunks(rows):
            cursor.executemany(statement, batch)


def _run_post_load_checks(connection: Any, data: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in LOAD_ORDER:
        actual = connection.execute(f"SELECT count(*) FROM reconciled.{table}").fetchone()[0]
        expected = len(data[table])
        if actual != expected:
            raise ReconciliationError(
                f"Post-load row count mismatch for {table}: expected {expected}, got {actual}"
            )
        counts[table] = actual

    duplicate_keys = {
        "r_work": "work_id",
        "r_topic": "topic_id",
        "r_institution": "institution_id",
        "r_source": "source_id",
        "r_work_topic": "work_id, topic_id",
        "r_work_country": "work_id, country_code_iso2",
        "r_work_institution": "work_id, institution_id",
        "r_country_year_indicator": "country_code_iso2, year",
    }
    for table, key in duplicate_keys.items():
        duplicate_count = connection.execute(
            f"SELECT count(*) FROM (SELECT {key} FROM reconciled.{table} "
            f"GROUP BY {key} HAVING count(*) > 1) duplicates"
        ).fetchone()[0]
        if duplicate_count:
            raise ReconciliationError(f"Post-load duplicate keys found in {table}")

    orphan_checks = {
        "r_work.primary_topic_id": (
            "SELECT count(*) FROM reconciled.r_work child "
            "LEFT JOIN reconciled.r_topic parent ON parent.topic_id = child.primary_topic_id "
            "WHERE child.primary_topic_id IS NOT NULL AND parent.topic_id IS NULL"
        ),
        "r_work.source_id": (
            "SELECT count(*) FROM reconciled.r_work child "
            "LEFT JOIN reconciled.r_source parent ON parent.source_id = child.source_id "
            "WHERE child.source_id IS NOT NULL AND parent.source_id IS NULL"
        ),
        "r_work_topic.work_id": (
            "SELECT count(*) FROM reconciled.r_work_topic child "
            "LEFT JOIN reconciled.r_work parent ON parent.work_id = child.work_id "
            "WHERE parent.work_id IS NULL"
        ),
        "r_work_topic.topic_id": (
            "SELECT count(*) FROM reconciled.r_work_topic child "
            "LEFT JOIN reconciled.r_topic parent ON parent.topic_id = child.topic_id "
            "WHERE parent.topic_id IS NULL"
        ),
        "r_work_country": (
            "SELECT count(*) FROM reconciled.r_work_country child "
            "LEFT JOIN reconciled.r_work work_parent ON work_parent.work_id = child.work_id "
            "LEFT JOIN reconciled.r_country country_parent "
            "ON country_parent.country_code_iso2 = child.country_code_iso2 "
            "WHERE work_parent.work_id IS NULL OR country_parent.country_code_iso2 IS NULL"
        ),
        "r_work_institution": (
            "SELECT count(*) FROM reconciled.r_work_institution child "
            "LEFT JOIN reconciled.r_work work_parent ON work_parent.work_id = child.work_id "
            "LEFT JOIN reconciled.r_institution institution_parent "
            "ON institution_parent.institution_id = child.institution_id "
            "WHERE work_parent.work_id IS NULL OR institution_parent.institution_id IS NULL"
        ),
        "r_institution.country_code_iso2": (
            "SELECT count(*) FROM reconciled.r_institution child "
            "LEFT JOIN reconciled.r_country parent "
            "ON parent.country_code_iso2 = child.country_code_iso2 "
            "WHERE child.country_code_iso2 IS NOT NULL AND parent.country_code_iso2 IS NULL"
        ),
        "r_country_year_indicator": (
            "SELECT count(*) FROM reconciled.r_country_year_indicator child "
            "LEFT JOIN reconciled.r_country parent "
            "ON parent.country_code_iso2 = child.country_code_iso2 "
            "WHERE parent.country_code_iso2 IS NULL"
        ),
    }
    for relationship, query in orphan_checks.items():
        if connection.execute(query).fetchone()[0]:
            raise ReconciliationError(f"Post-load orphan records found for {relationship}")

    primary_mismatches = connection.execute(
        "SELECT w.work_id, w.primary_topic_id FROM reconciled.r_work w "
        "WHERE w.primary_topic_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM reconciled.r_work_topic wt "
        "WHERE wt.work_id = w.work_id AND wt.topic_id = w.primary_topic_id)"
    ).fetchall()
    if primary_mismatches:
        raise ReconciliationError(
            f"Post-load primary-topic mismatches found: {len(primary_mismatches)}"
        )

    negative_citations = connection.execute(
        "SELECT count(*) FROM reconciled.r_work WHERE cited_by_count < 0"
    ).fetchone()[0]
    if negative_citations:
        raise ReconciliationError("Post-load negative citation counts found")

    for field in INDICATOR_FIELDS[2:]:
        actual_nulls = connection.execute(
            f"SELECT count(*) FROM reconciled.r_country_year_indicator WHERE {field} IS NULL"
        ).fetchone()[0]
        expected_nulls = data["indicator_nulls"][field]
        if actual_nulls != expected_nulls:
            raise ReconciliationError(
                f"World Bank null preservation failed for {field}: "
                f"expected {expected_nulls}, got {actual_nulls}"
            )

    fabricated_indicators = connection.execute(
        "SELECT count(*) FROM reconciled.r_country_year_indicator indicator "
        "JOIN reconciled.r_country country "
        "ON country.country_code_iso2 = indicator.country_code_iso2 "
        "WHERE NOT country.has_world_bank_data"
    ).fetchone()[0]
    if fabricated_indicators:
        raise ReconciliationError(
            "Country-year indicators found for geographies without World Bank data"
        )
    return counts


def load_reconciled(
    database_url: str,
    openalex_path: Path = DEFAULT_OPENALEX,
    world_bank_path: Path = DEFAULT_WORLD_BANK,
    schema_path: Path = DEFAULT_SCHEMA,
) -> dict[str, int]:
    """Replace only the reconciled schema in one atomic PostgreSQL transaction."""
    data = build_reconciled_data(Path(openalex_path), Path(world_bank_path))
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL driver missing; install it with: python3 -m pip install 'psycopg[binary]>=3.2,<4'"
        ) from exc

    ddl = Path(schema_path).read_text(encoding="utf-8")
    with psycopg.connect(database_url) as connection:
        connection.execute(ddl)
        for table in LOAD_ORDER:
            _insert_rows(connection, table, TABLE_FIELDS[table], _rows_for_table(data, table))
        return _run_post_load_checks(connection, data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openalex", type=Path, default=DEFAULT_OPENALEX)
    parser.add_argument("--world-bank", type=Path, default=DEFAULT_WORLD_BANK)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate and summarize the inputs without connecting to PostgreSQL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.validate_only:
        try:
            data = build_reconciled_data(args.openalex, args.world_bank)
        except ReconciliationError as exc:
            raise SystemExit(f"Reconciled input validation failed: {exc}") from exc
        summary = geographic_coverage_summary(data)
        openalex_only = summary["openalex_only_geographic_entities"]
        print(
            "Reconciled input validation complete: "
            f"{summary['reconciled_geographic_entities']} geographic entities; "
            f"{summary['world_bank_geographic_entities']} with World Bank data"
        )
        if openalex_only:
            print(
                f"OpenAlex-only geographic entities ({len(openalex_only)}): "
                + ", ".join(openalex_only)
            )
            print("World Bank data are unavailable for these geographic entities.")
        return 0

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required unless --validate-only is used")
    try:
        counts = load_reconciled(
            database_url,
            openalex_path=args.openalex,
            world_bank_path=args.world_bank,
            schema_path=args.schema,
        )
    except (ReconciliationError, RuntimeError) as exc:
        raise SystemExit(f"Reconciled load failed: {exc}") from exc
    summary = ", ".join(f"{table}={count}" for table, count in counts.items())
    print(f"Reconciled load complete: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
