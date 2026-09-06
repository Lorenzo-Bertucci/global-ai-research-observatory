#!/usr/bin/env python3
"""Extract World Bank WDI country-year indicators for 2018–2025."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/raw/world_bank_country_year.jsonl"
DEFAULT_MANIFEST = ROOT / "data/raw/world_bank_manifest.json"
DEFAULT_QUALITY_REPORT = ROOT / "validation/results/world_bank_data_quality.json"

API_BASE = "https://api.worldbank.org/v2"
API_VERSION = "v2"
SOURCE_ID = "2"
SOURCE_NAME = "World Development Indicators"
COUNTRY_ENDPOINT = f"{API_BASE}/country"
SOURCE_ENDPOINT = f"{API_BASE}/source/{SOURCE_ID}"
INDICATOR_METADATA_ENDPOINT = f"{API_BASE}/indicator"
YEARS = tuple(range(2018, 2026))
PER_PAGE = 1000
REQUEST_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 4
METHOD = "world_bank_wdi_multi_indicator_country_year_skeleton"

# Ordered by output schema. The original WDI codes are retained here and in the
# manifest so the wide raw dataset remains traceable to its source series.
INDICATORS = {
    "population": "SP.POP.TOTL",
    "gdp_current_usd": "NY.GDP.MKTP.CD",
    "gdp_per_capita_current_usd": "NY.GDP.PCAP.CD",
    "internet_users_pct": "IT.NET.USER.ZS",
    "rd_expenditure_pct_gdp": "GB.XPD.RSDV.GD.ZS",
}
CODE_TO_FIELD = {code: field for field, code in INDICATORS.items()}
MEASURE_FIELDS = tuple(INDICATORS)


def utc_timestamp() -> str:
    """Return a reproducible UTC timestamp representation."""
    return datetime.now(timezone.utc).isoformat()


def build_url(endpoint: str, params: dict[str, Any]) -> str:
    """Build a deterministic request URL, omitting parameters with None values."""
    clean = {key: value for key, value in params.items() if value is not None}
    return f"{endpoint}?{urlencode(clean)}"


def new_api_stats() -> dict[str, Any]:
    """Create mutable API activity counters used by the manifest."""
    return {
        "requests": 0,
        "pages_fetched": 0,
        "pages_by_endpoint": {},
        "retries": 0,
        "errors": [],
    }


def fetch_json(
    endpoint: str,
    params: dict[str, Any],
    *,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stats: dict[str, Any] | None = None,
) -> Any:
    """Fetch JSON with explicit timeout and bounded retries for transient errors."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    url = build_url(endpoint, params)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "global-ai-research-observatory/world-bank-wdi-v1",
        },
    )
    open_url = opener or urlopen
    retryable = {429, 500, 502, 503, 504}

    for attempt in range(1, max_attempts + 1):
        if stats is not None:
            stats["requests"] += 1
        try:
            with open_url(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code not in retryable or attempt == max_attempts:
                message = f"World Bank returned HTTP {exc.code}: {detail}"
                if stats is not None:
                    stats["errors"].append(message)
                raise RuntimeError(message) from exc
            if stats is not None:
                stats["retries"] += 1
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = float(retry_after) if retry_after else 2 ** (attempt - 1)
            except ValueError:
                delay = 2 ** (attempt - 1)
            sleep(min(delay, 16))
        except (URLError, TimeoutError) as exc:
            if attempt == max_attempts:
                reason = getattr(exc, "reason", exc)
                message = f"Could not reach World Bank: {reason}"
                if stats is not None:
                    stats["errors"].append(message)
                raise RuntimeError(message) from exc
            if stats is not None:
                stats["retries"] += 1
            sleep(min(2 ** (attempt - 1), 16))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            message = f"World Bank returned invalid JSON: {exc}"
            if stats is not None:
                stats["errors"].append(message)
            raise RuntimeError(message) from exc
    raise RuntimeError("World Bank request failed")


def parse_list_response(payload: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse the API V2 list response shape: [pagination metadata, records]."""
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError("Expected World Bank response [metadata, records]")
    metadata, records = payload[0], payload[1]
    if not isinstance(metadata, dict):
        raise ValueError("World Bank pagination metadata is not an object")
    if records is None:
        records = []
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise ValueError("World Bank records are not a list of objects")
    return metadata, records


def fetch_paginated(
    endpoint: str,
    params: dict[str, Any],
    *,
    fetcher: Callable[[str, dict[str, Any]], Any],
    stats: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch every page declared by API response metadata."""
    requested_page = 1
    rows: list[dict[str, Any]] = []
    first_metadata: dict[str, Any] | None = None
    declared_pages: int | None = None

    while declared_pages is None or requested_page <= declared_pages:
        page_params = dict(params)
        page_params["page"] = requested_page
        payload = fetcher(endpoint, page_params)
        metadata, page_rows = parse_list_response(payload)
        try:
            response_page = int(metadata.get("page", requested_page))
            response_pages = int(metadata.get("pages", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid World Bank pagination metadata") from exc
        if response_page != requested_page:
            raise ValueError(
                f"Requested page {requested_page}, received page {response_page}"
            )
        if response_pages < 1:
            raise ValueError("World Bank response declared fewer than one page")
        if declared_pages is not None and response_pages != declared_pages:
            raise ValueError("World Bank page count changed during pagination")
        declared_pages = response_pages
        first_metadata = first_metadata or metadata
        rows.extend(page_rows)
        if stats is not None:
            stats["pages_fetched"] += 1
            by_endpoint = stats["pages_by_endpoint"]
            by_endpoint[endpoint] = by_endpoint.get(endpoint, 0) + 1
        requested_page += 1

    assert first_metadata is not None
    reported_total = first_metadata.get("total")
    if reported_total is not None and int(reported_total) != len(rows):
        raise ValueError(
            f"World Bank reported {reported_total} records but pagination returned {len(rows)}"
        )
    return rows, first_metadata


def nested_value(record: dict[str, Any], key: str, child: str) -> Any:
    """Read optional nested metadata without failing on missing/null objects."""
    value = record.get(key)
    return value.get(child) if isinstance(value, dict) else None


def is_aggregate_country(record: dict[str, Any]) -> bool:
    """Identify aggregates using the current World Bank country-region marker."""
    region_id = str(nested_value(record, "region", "id") or "").strip().upper()
    region_name = str(nested_value(record, "region", "value") or "").strip().casefold()
    return region_id == "NA" or region_name == "aggregates"


def normalize_country(record: dict[str, Any]) -> dict[str, Any]:
    """Select country metadata needed by the country-year dataset."""
    return {
        "country_code_iso3": str(record.get("id") or "").strip(),
        "country_code_iso2": str(record.get("iso2Code") or "").strip(),
        "country_name": str(record.get("name") or "").strip(),
        "region_id": str(nested_value(record, "region", "id") or "").strip(),
        "region_name": str(nested_value(record, "region", "value") or "").strip(),
        "income_level_id": str(
            nested_value(record, "incomeLevel", "id") or ""
        ).strip(),
        "income_level_name": str(
            nested_value(record, "incomeLevel", "value") or ""
        ).strip(),
    }


def select_real_countries(
    country_records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return normalized real economies and excluded aggregate records."""
    real: list[dict[str, Any]] = []
    aggregates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in country_records:
        if is_aggregate_country(record):
            aggregates.append(record)
            continue
        country = normalize_country(record)
        iso3 = country["country_code_iso3"]
        if not iso3:
            raise ValueError("Real country/economy record has an empty World Bank ISO3 ID")
        if iso3 in seen_ids:
            raise ValueError(f"Duplicate World Bank country ID in metadata: {iso3}")
        seen_ids.add(iso3)
        real.append(country)
    return sorted(real, key=lambda row: row["country_code_iso3"]), aggregates


def normalize_indicator_metadata(record: dict[str, Any], output_field: str) -> dict[str, Any]:
    """Keep the official indicator metadata required for lineage."""
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    return {
        "code": str(record.get("id") or "").strip(),
        "official_name": str(record.get("name") or "").strip(),
        "output_field": output_field,
        "unit": str(record.get("unit") or "").strip(),
        "source_id": str(source.get("id") or "").strip(),
        "source_name": str(source.get("value") or "").strip(),
        "source_note": str(record.get("sourceNote") or "").strip(),
        "source_organization": str(record.get("sourceOrganization") or "").strip(),
    }


def verify_indicator_metadata(
    records_by_code: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Validate that every configured indicator exists in WDI source 2."""
    verified: list[dict[str, Any]] = []
    for output_field, code in INDICATORS.items():
        records = records_by_code.get(code, [])
        matches = [
            row
            for row in records
            if str(row.get("id") or "").upper() == code
            and str(nested_value(row, "source", "id") or "") == SOURCE_ID
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one metadata record for {code} in source {SOURCE_ID}, found {len(matches)}"
            )
        metadata = normalize_indicator_metadata(matches[0], output_field)
        if not metadata["official_name"]:
            raise ValueError(f"Indicator {code} has no official name")
        verified.append(metadata)
    return verified


def build_skeleton(
    countries: Iterable[dict[str, Any]], years: Iterable[int] = YEARS
) -> dict[tuple[str, int], dict[str, Any]]:
    """Build the complete real-economy × year grain before adding measures."""
    skeleton: dict[tuple[str, int], dict[str, Any]] = {}
    for country in countries:
        for year in years:
            row = dict(country)
            row["year"] = int(year)
            row.update({field: None for field in MEASURE_FIELDS})
            key = (row["country_code_iso3"], row["year"])
            if key in skeleton:
                raise ValueError(f"Duplicate country-year skeleton key: {key}")
            skeleton[key] = row
    return skeleton


def pivot_observations(
    countries: Iterable[dict[str, Any]],
    observations: Iterable[dict[str, Any]],
    *,
    years: Iterable[int] = YEARS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply long WDI observations to a complete country-year skeleton."""
    allowed_years = {int(year) for year in years}
    country_list = list(countries)
    real_ids = {country["country_code_iso3"] for country in country_list}
    skeleton = build_skeleton(country_list, sorted(allowed_years))
    seen_observations: set[tuple[str, int, str]] = set()
    excluded_aggregate_observations = 0
    unexpected_indicators: Counter[str] = Counter()
    out_of_period_observations = 0

    for observation in observations:
        indicator_code = str(nested_value(observation, "indicator", "id") or "").strip()
        if indicator_code not in CODE_TO_FIELD:
            unexpected_indicators[indicator_code or "<missing>"] += 1
            continue
        iso3 = str(observation.get("countryiso3code") or "").strip()
        if iso3 not in real_ids:
            excluded_aggregate_observations += 1
            continue
        try:
            year = int(observation.get("date"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid year in World Bank observation: {observation.get('date')!r}") from exc
        if year not in allowed_years:
            out_of_period_observations += 1
            continue
        observation_key = (iso3, year, indicator_code)
        if observation_key in seen_observations:
            raise ValueError(f"Duplicate indicator observation: {observation_key}")
        seen_observations.add(observation_key)
        value = observation.get("value")
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError(f"Non-numeric value for {observation_key}: {value!r}")
        skeleton[(iso3, year)][CODE_TO_FIELD[indicator_code]] = value

    rows = [skeleton[key] for key in sorted(skeleton)]
    stats = {
        "indicator_observations_applied": len(seen_observations),
        "aggregate_or_non_country_observations_excluded": excluded_aggregate_observations,
        "unexpected_indicators": dict(sorted(unexpected_indicators.items())),
        "out_of_period_observations": out_of_period_observations,
    }
    return rows, stats


def duplicate_country_year_count(rows: Iterable[dict[str, Any]]) -> int:
    keys = [(row.get("country_code_iso3"), row.get("year")) for row in rows]
    return len(keys) - len(set(keys))


def analyze_quality(
    rows: list[dict[str, Any]], countries: list[dict[str, Any]]
) -> dict[str, Any]:
    """Profile completeness and flag, but never repair, invalid values."""
    row_count = len(rows)
    country_ids = {country["country_code_iso3"] for country in countries}
    present_counts = {
        field: sum(row[field] is not None for row in rows) for field in MEASURE_FIELDS
    }
    missing_counts = {field: row_count - count for field, count in present_counts.items()}
    coverage = {
        field: {
            "present": present_counts[field],
            "missing": missing_counts[field],
            "coverage_pct": round(100 * present_counts[field] / row_count, 4)
            if row_count
            else 0.0,
        }
        for field in MEASURE_FIELDS
    }
    by_year: dict[str, dict[str, dict[str, Any]]] = {}
    for year in YEARS:
        year_rows = [row for row in rows if row["year"] == year]
        by_year[str(year)] = {}
        for field in MEASURE_FIELDS:
            present = sum(row[field] is not None for row in year_rows)
            missing = len(year_rows) - present
            by_year[str(year)][field] = {
                "present": present,
                "missing": missing,
                "coverage_pct": round(100 * present / len(year_rows), 4)
                if year_rows
                else 0.0,
            }

    countries_without_values: dict[str, dict[str, Any]] = {}
    for field in MEASURE_FIELDS:
        with_value = {
            row["country_code_iso3"] for row in rows if row[field] is not None
        }
        without = sorted(country_ids - with_value)
        countries_without_values[field] = {"count": len(without), "countries": without}

    invalid_ranges: dict[str, list[dict[str, Any]]] = {field: [] for field in MEASURE_FIELDS}
    for row in rows:
        for field in MEASURE_FIELDS:
            value = row[field]
            if value is None:
                continue
            invalid = (
                (field == "population" and value <= 0)
                or (field in {"gdp_current_usd", "gdp_per_capita_current_usd"} and value < 0)
                or (field in {"internet_users_pct", "rd_expenditure_pct_gdp"} and not 0 <= value <= 100)
            )
            if invalid:
                invalid_ranges[field].append(
                    {
                        "country_code_iso3": row["country_code_iso3"],
                        "year": row["year"],
                        "value": value,
                    }
                )

    all_null_rows = [
        {"country_code_iso3": row["country_code_iso3"], "year": row["year"]}
        for row in rows
        if all(row[field] is None for field in MEASURE_FIELDS)
    ]
    rows_by_year = Counter(row["year"] for row in rows)
    rd_without = countries_without_values["rd_expenditure_pct_gdp"]
    rd_with_at_least_one = len(country_ids) - rd_without["count"]
    invalid_count = sum(len(values) for values in invalid_ranges.values())

    return {
        "method": "country_year_skeleton_completeness_and_range_checks",
        "generated_at": utc_timestamp(),
        "grain": "one real World Bank economy × one year",
        "period": {"start_year": YEARS[0], "end_year": YEARS[-1]},
        "no_missing_values_imputed": True,
        "missing_value_policy": "World Bank null observations are preserved as JSON null; no imputation is applied.",
        "country_count": len(country_ids),
        "years": list(YEARS),
        "row_count": row_count,
        "expected_rows": len(country_ids) * len(YEARS),
        "rows_by_year": {str(year): rows_by_year.get(year, 0) for year in YEARS},
        "duplicate_country_year_count": duplicate_country_year_count(rows),
        "missing_counts": missing_counts,
        "missing_percentages": {
            field: round(100 * count / row_count, 4) if row_count else 0.0
            for field, count in missing_counts.items()
        },
        "coverage_by_indicator": coverage,
        "coverage_by_indicator_and_year": by_year,
        "countries_with_zero_available_values": countries_without_values,
        "rows_with_all_indicators_null": {
            "count": len(all_null_rows),
            "country_years": all_null_rows,
        },
        "value_sanity_checks": {
            "rules": {
                "population": "null or > 0",
                "gdp_current_usd": "null or >= 0",
                "gdp_per_capita_current_usd": "null or >= 0",
                "internet_users_pct": "null or between 0 and 100 inclusive",
                "rd_expenditure_pct_gdp": "null or between 0 and 100 inclusive",
            },
            "invalid_range_count": invalid_count,
            "invalid_ranges": invalid_ranges,
        },
        "outlier_flags": {
            "definition": "Only explicit domain-range violations are flagged; valid extreme values are not modified.",
            "count": invalid_count,
            "by_indicator": {
                field: len(invalid_ranges[field]) for field in MEASURE_FIELDS
            },
        },
        "rd_coverage_analysis": {
            "indicator_code": INDICATORS["rd_expenditure_pct_gdp"],
            "overall": coverage["rd_expenditure_pct_gdp"],
            "by_year": {
                year: values["rd_expenditure_pct_gdp"] for year, values in by_year.items()
            },
            "countries_with_at_least_one_value": rd_with_at_least_one,
            "countries_without_any_value": rd_without["count"],
            "countries_without_any_value_codes": rd_without["countries"],
            "interpretation": "Low or zero recent-year coverage is treated as source missingness, not as a pipeline failure.",
        },
    }


def validate_final_dataset(
    rows: list[dict[str, Any]],
    countries: list[dict[str, Any]],
    quality: dict[str, Any],
) -> None:
    """Enforce post-extraction structural quality gates."""
    real_ids = {country["country_code_iso3"] for country in countries}
    if not rows or not real_ids:
        raise ValueError("World Bank extraction produced no data")
    if {row["year"] for row in rows} != set(YEARS):
        raise ValueError("Final dataset does not contain exactly years 2018–2025")
    if duplicate_country_year_count(rows):
        raise ValueError("Final dataset contains duplicate country-year rows")
    if any(not row["country_code_iso3"] for row in rows):
        raise ValueError("Final dataset contains an empty ISO3 country code")
    if any(row["country_code_iso3"] not in real_ids for row in rows):
        raise ValueError("Final dataset contains an aggregate or unknown entity")
    expected = len(real_ids) * len(YEARS)
    if len(rows) != expected or quality["expected_rows"] != expected:
        raise ValueError("Final dataset is not a complete country-year skeleton")
    sorted_keys = sorted((row["country_code_iso3"], row["year"]) for row in rows)
    actual_keys = [(row["country_code_iso3"], row["year"]) for row in rows]
    if actual_keys != sorted_keys:
        raise ValueError("Final dataset order is not deterministic")


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write formatted JSON and atomically replace the final path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_jsonl_atomic(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write deterministic JSONL and atomically replace the final path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def extract(
    output_path: Path = DEFAULT_OUTPUT,
    manifest_path: Path = DEFAULT_MANIFEST,
    quality_path: Path = DEFAULT_QUALITY_REPORT,
    *,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
    per_page: int = PER_PAGE,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run the complete live World Bank extraction and validation workflow."""
    started_at = datetime.now(timezone.utc)
    api_stats = new_api_stats()

    def request(endpoint: str, params: dict[str, Any]) -> Any:
        return fetch_json(
            endpoint,
            params,
            timeout=timeout,
            max_attempts=max_attempts,
            opener=opener,
            sleep=sleep,
            stats=api_stats,
        )

    base_params = {"format": "json", "per_page": per_page}
    country_records, country_response_meta = fetch_paginated(
        COUNTRY_ENDPOINT, base_params, fetcher=request, stats=api_stats
    )
    countries, aggregate_records = select_real_countries(country_records)

    source_records, _ = fetch_paginated(
        SOURCE_ENDPOINT, base_params, fetcher=request, stats=api_stats
    )
    source_matches = [row for row in source_records if str(row.get("id")) == SOURCE_ID]
    if len(source_matches) != 1:
        raise ValueError(f"Could not verify World Bank source {SOURCE_ID}")
    source_name = str(source_matches[0].get("name") or "").strip()
    if source_name != SOURCE_NAME:
        raise ValueError(
            f"Source {SOURCE_ID} is {source_name!r}, expected {SOURCE_NAME!r}"
        )

    metadata_by_code: dict[str, list[dict[str, Any]]] = {}
    for code in INDICATORS.values():
        records, _ = fetch_paginated(
            f"{INDICATOR_METADATA_ENDPOINT}/{code}",
            {**base_params, "source": SOURCE_ID},
            fetcher=request,
            stats=api_stats,
        )
        metadata_by_code[code] = records
    indicator_metadata = verify_indicator_metadata(metadata_by_code)

    indicator_path = ";".join(INDICATORS.values())
    data_endpoint = f"{API_BASE}/country/all/indicator/{indicator_path}"
    data_params = {
        "source": SOURCE_ID,
        "date": f"{YEARS[0]}:{YEARS[-1]}",
        "format": "json",
        "per_page": per_page,
    }
    observations, data_response_meta = fetch_paginated(
        data_endpoint, data_params, fetcher=request, stats=api_stats
    )
    rows, pivot_stats = pivot_observations(countries, observations)
    quality = analyze_quality(rows, countries)
    validate_final_dataset(rows, countries, quality)

    for metadata in indicator_metadata:
        field = metadata["output_field"]
        metadata["availability_2018_2025"] = {
            "available": quality["coverage_by_indicator"][field]["present"] > 0,
            **quality["coverage_by_indicator"][field],
        }

    finished_at = datetime.now(timezone.utc)
    expected_rows = len(countries) * len(YEARS)
    missing_by_year = {
        year: {
            field: values[field]["missing"] for field in MEASURE_FIELDS
        }
        for year, values in quality["coverage_by_indicator_and_year"].items()
    }
    manifest = {
        "method": METHOD,
        "extraction_timestamp": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "api_version": API_VERSION,
        "api_base": API_BASE,
        "authentication_required": False,
        "source_id": SOURCE_ID,
        "source_name": source_name,
        "date_range": {"start_year": YEARS[0], "end_year": YEARS[-1], "years": list(YEARS)},
        "grain": "one real World Bank economy × one year",
        "indicator_lineage": dict(INDICATORS),
        "indicators": indicator_metadata,
        "country_metadata_endpoint": COUNTRY_ENDPOINT,
        "country_filter_rule": {
            "real_economy": "region.id != 'NA' and region.value != 'Aggregates'",
            "aggregate": "region.id == 'NA' or region.value == 'Aggregates' (case-insensitive)",
            "basis": "current World Bank country metadata semantics verified at extraction time",
        },
        "entities_returned": len(country_records),
        "real_countries_retained": len(countries),
        "aggregates_excluded": len(aggregate_records),
        "excluded_aggregate_codes": sorted(str(row.get("id") or "") for row in aggregate_records),
        "expected_country_year_rows": expected_rows,
        "actual_country_year_rows": len(rows),
        "duplicates": quality["duplicate_country_year_count"],
        "missing_by_indicator": quality["missing_counts"],
        "missing_pct_by_indicator": quality["missing_percentages"],
        "missing_by_indicator_and_year": missing_by_year,
        "no_missing_values_imputed": True,
        "api_requests": api_stats["requests"],
        "pages_fetched": {
            "total": api_stats["pages_fetched"],
            "by_endpoint": api_stats["pages_by_endpoint"],
        },
        "retries": api_stats["retries"],
        "errors": api_stats["errors"],
        "response_metadata": {
            "countries": country_response_meta,
            "indicator_data": data_response_meta,
        },
        "exact_queries": {
            "country_metadata": {
                "endpoint": COUNTRY_ENDPOINT,
                "parameters": base_params,
                "first_page_url": build_url(COUNTRY_ENDPOINT, {**base_params, "page": 1}),
            },
            "source_metadata": {
                "endpoint": SOURCE_ENDPOINT,
                "parameters": base_params,
                "first_page_url": build_url(SOURCE_ENDPOINT, {**base_params, "page": 1}),
            },
            "indicator_metadata": [
                {
                    "code": code,
                    "endpoint": f"{INDICATOR_METADATA_ENDPOINT}/{code}",
                    "parameters": {**base_params, "source": SOURCE_ID},
                    "first_page_url": build_url(
                        f"{INDICATOR_METADATA_ENDPOINT}/{code}",
                        {**base_params, "source": SOURCE_ID, "page": 1},
                    ),
                }
                for code in INDICATORS.values()
            ],
            "indicator_data": {
                "endpoint": data_endpoint,
                "parameters": data_params,
                "first_page_url": build_url(data_endpoint, {**data_params, "page": 1}),
            },
        },
        "pivot_statistics": pivot_stats,
        "output": str(output_path),
        "quality_report": str(quality_path),
    }

    # The dataset is published only after all in-memory structural gates pass.
    write_jsonl_atomic(output_path, rows)
    write_json_atomic(manifest_path, manifest)
    write_json_atomic(quality_path, quality)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--quality-report", type=Path, default=DEFAULT_QUALITY_REPORT)
    parser.add_argument("--timeout", type=int, default=REQUEST_TIMEOUT_SECONDS)
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)
    parser.add_argument("--per-page", type=int, default=PER_PAGE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = extract(
        output_path=args.output,
        manifest_path=args.manifest,
        quality_path=args.quality_report,
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        per_page=args.per_page,
    )
    print(
        "World Bank extraction complete: "
        f"{manifest['actual_country_year_rows']} rows, "
        f"{manifest['real_countries_retained']} countries/economies, "
        f"{manifest['duplicates']} duplicates"
    )


if __name__ == "__main__":
    main()
