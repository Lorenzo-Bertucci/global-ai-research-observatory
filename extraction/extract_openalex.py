#!/usr/bin/env python3
"""Extract OpenAlex population counts and a 24k stratified work sample."""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/openalex_ai_topics.json"
DEFAULT_POPULATION = ROOT / "data/raw/openalex_ai_population.json"
DEFAULT_OUTPUT = ROOT / "data/raw/openalex_ai_works.jsonl"
DEFAULT_MANIFEST = ROOT / "data/raw/openalex_ai_manifest.json"
DEFAULT_QUALITY_REPORT = ROOT / "validation/results/openalex_data_quality.json"

OPENALEX_ENDPOINT = "https://api.openalex.org/works"
METHOD_NAME = "curated_validated_native_openalex_topics"
METHOD_VERSION = "1.1"
YEARS = tuple(range(2018, 2026))
FROM_DATE = "2018-01-01"
TO_DATE = "2025-12-31"
ALLOWED_WORK_TYPES = (
    "article",
    "review",
    "conference-paper",
    "preprint",
    "book",
    "book-chapter",
)
TARGET_SAMPLE_SIZE = 24_000
SEED_BASE = 20_260_906
SANITY_SAMPLE_SEED = 20_260_906_99
SAMPLE_MAX = 10_000
PER_PAGE = 100
REQUEST_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 4
MAX_REFILL_ATTEMPTS = 3
SELECT_FIELDS = ",".join(
    (
        "id",
        "doi",
        "display_name",
        "title",
        "publication_date",
        "publication_year",
        "type",
        "language",
        "cited_by_count",
        "is_retracted",
        "primary_topic",
        "topics",
        "keywords",
        "authorships",
        "primary_location",
        "open_access",
    )
)


def short_id(value: Any) -> str | None:
    """Return the last component of either a short or URL-form OpenAlex ID."""
    if value is None:
        return None
    identifier = str(value).strip().rstrip("/").rsplit("/", 1)[-1]
    return identifier or None


def load_topic_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load and validate the frozen Topic configuration."""
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("status") != "frozen" or config.get("version") != METHOD_VERSION:
        raise ValueError("Topic configuration must be frozen at version 1.1")
    if config.get("method") != METHOD_NAME:
        raise ValueError("Unexpected Topic-selection method")
    topics = config.get("topics")
    if not isinstance(topics, list) or not topics:
        raise ValueError("Frozen Topic configuration is empty")
    identifiers = [short_id(topic.get("id")) for topic in topics]
    if any(not value or not value.startswith("T") for value in identifiers):
        raise ValueError("Invalid OpenAlex Topic ID in configuration")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate OpenAlex Topic ID in configuration")
    return config


TOPIC_CONFIG = load_topic_config()
AI_TOPIC_SET_FINAL = frozenset(short_id(topic["id"]) for topic in TOPIC_CONFIG["topics"])
AI_TOPIC_NAMES_FINAL = {
    short_id(topic["id"]): topic["display_name"] for topic in TOPIC_CONFIG["topics"]
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_api_key() -> str | None:
    """Read the API key from the environment or the ignored local .env file."""
    for name in ("OPENALEX_API_KEY", "OPEN_ALEX_KEY"):
        if os.getenv(name):
            return os.environ[name]
    env_path = ROOT / ".env"
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in {"OPENALEX_API_KEY", "OPEN_ALEX_KEY"}:
            return value.strip().strip('"').strip("'") or None
    return None


def build_filter(*, year: int | None = None) -> str:
    """Build the sole approved native Works filter."""
    scope = (
        f"publication_year:{year}"
        if year is not None
        else f"from_publication_date:{FROM_DATE},to_publication_date:{TO_DATE}"
    )
    return ",".join(
        (
            f"topics.id:{'|'.join(sorted(AI_TOPIC_SET_FINAL))}",
            scope,
            f"type:{'|'.join(ALLOWED_WORK_TYPES)}",
            "is_retracted:false",
        )
    )


def new_api_stats() -> dict[str, Any]:
    return {
        "requests": 0,
        "retries": 0,
        "responses_429": 0,
        "responses_5xx": 0,
        "timeouts": 0,
        "network_errors": 0,
        "cost_usd": 0.0,
        "rate_limit_remaining_last": None,
        "rate_limit_reset_seconds_last": None,
    }


def fetch_json(
    params: dict[str, Any],
    *,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch one OpenAlex response with bounded retry and diagnostics."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    request_params = {key: value for key, value in params.items() if value is not None}
    api_key = read_api_key()
    if api_key:
        request_params["api_key"] = api_key
    request = Request(
        f"{OPENALEX_ENDPOINT}?{urlencode(request_params)}",
        headers={
            "Accept": "application/json",
            "User-Agent": "global-ai-research-observatory/final-sample-v1.1",
        },
    )
    open_url = opener or urlopen
    retryable = {429, 500, 502, 503, 504}
    for attempt in range(1, max_attempts + 1):
        if stats is not None:
            stats["requests"] += 1
        try:
            with open_url(request, timeout=timeout) as response:
                payload = json.load(response)
                if not isinstance(payload, dict):
                    raise RuntimeError("OpenAlex returned a non-object JSON response")
                if stats is not None:
                    meta = payload.get("meta") or {}
                    stats["cost_usd"] += float(meta.get("cost_usd") or 0)
                    headers = getattr(response, "headers", {})
                    stats["rate_limit_remaining_last"] = headers.get("X-RateLimit-Remaining")
                    stats["rate_limit_reset_seconds_last"] = headers.get("X-RateLimit-Reset")
                return payload
        except HTTPError as exc:
            if stats is not None:
                if exc.code == 429:
                    stats["responses_429"] += 1
                if 500 <= exc.code <= 599:
                    stats["responses_5xx"] += 1
            if exc.code not in retryable or attempt == max_attempts:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"OpenAlex returned HTTP {exc.code}: {detail}") from exc
            if stats is not None:
                stats["retries"] += 1
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = float(retry_after) if retry_after else 2 ** (attempt - 1)
            except ValueError:
                delay = 2 ** (attempt - 1)
            sleep(min(delay, 16))
        except (URLError, TimeoutError) as exc:
            if stats is not None:
                stats["network_errors"] += 1
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError):
                    stats["timeouts"] += 1
            if attempt == max_attempts:
                reason = getattr(exc, "reason", exc)
                raise RuntimeError(f"Could not reach OpenAlex: {reason}") from exc
            if stats is not None:
                stats["retries"] += 1
            sleep(min(2 ** (attempt - 1), 16))
    raise RuntimeError("OpenAlex request failed")


def parse_population(payload: dict[str, Any]) -> tuple[int, dict[int, int]]:
    """Parse and cross-check a publication_year grouped response."""
    groups = payload.get("group_by")
    if not isinstance(groups, list):
        raise ValueError("OpenAlex population response has no group_by list")
    counts = {year: 0 for year in YEARS}
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("Invalid OpenAlex group object")
        try:
            year = int(group.get("key"))
            count = int(group.get("count"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid publication-year group") from exc
        if year not in counts or count < 0:
            raise ValueError(f"Unexpected publication-year group: {year}")
        counts[year] += count
    try:
        total = int((payload.get("meta") or {}).get("count"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Population response has no valid meta.count") from exc
    if total != sum(counts.values()):
        raise ValueError(
            f"Population mismatch: meta.count={total}, yearly sum={sum(counts.values())}"
        )
    return total, counts


def largest_remainder_allocation(
    population_by_year: dict[int, int], target: int
) -> dict[int, int]:
    """Allocate a proportional integer sample with deterministic year tie-breaks."""
    if target < 0 or any(value < 0 for value in population_by_year.values()):
        raise ValueError("Population counts and target must be non-negative")
    total = sum(population_by_year.values())
    if total == 0:
        if target:
            raise ValueError("Cannot sample from an empty population")
        return {year: 0 for year in sorted(population_by_year)}
    effective_target = min(target, total)
    allocation: dict[int, int] = {}
    remainders: list[tuple[int, int]] = []
    for year, population in sorted(population_by_year.items()):
        base, remainder = divmod(effective_target * population, total)
        allocation[year] = base
        remainders.append((remainder, year))
    remaining = effective_target - sum(allocation.values())
    for _remainder, year in sorted(remainders, key=lambda row: (-row[0], row[1]))[:remaining]:
        allocation[year] += 1
    if sum(allocation.values()) != effective_target:
        raise AssertionError("Largest-remainder allocation did not reach its target")
    if any(allocation[year] > population_by_year[year] for year in allocation):
        raise AssertionError("Sample allocation exceeds a stratum population")
    return allocation


def seed_for_year(year: int) -> int:
    return int(f"{SEED_BASE}{year % 100:02d}")


def build_sampling_plan(
    population_by_year: dict[int, int], target: int = TARGET_SAMPLE_SIZE
) -> dict[int, dict[str, Any]]:
    total = sum(population_by_year.values())
    allocation = largest_remainder_allocation(population_by_year, target)
    plan: dict[int, dict[str, Any]] = {}
    for year in sorted(population_by_year):
        population = population_by_year[year]
        sample = allocation[year]
        plan[year] = {
            "population": population,
            "population_share": population / total if total else 0.0,
            "target_sample": sample,
            "sampling_probability": sample / population if population else None,
            "sampling_weight": population / sample if sample else None,
            "seed": seed_for_year(year),
        }
    return plan


def matched_selected_topic_ids(work: dict[str, Any]) -> set[str]:
    topics = work.get("topics")
    if not isinstance(topics, list):
        return set()
    return {
        identifier
        for topic in topics
        if isinstance(topic, dict)
        if (identifier := short_id(topic.get("id"))) in AI_TOPIC_SET_FINAL
    }


def validate_work(work: dict[str, Any], expected_year: int | None = None) -> str | None:
    """Return an exclusion reason, or None when every local guardrail passes."""
    if not isinstance(work, dict) or not short_id(work.get("id")):
        return "missing_id"
    topics = work.get("topics")
    if not isinstance(topics, list) or not topics:
        return "missing_topics"
    if not matched_selected_topic_ids(work):
        return "selected_topic"
    if expected_year is not None and work.get("publication_year") != expected_year:
        return "year_scope"
    if work.get("type") not in ALLOWED_WORK_TYPES:
        return "type_scope"
    if work.get("is_retracted") is True:
        return "retracted"
    return None


def fetch_population(
    fetcher: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[int, dict[int, int]]:
    payload = fetcher(
        {"filter": build_filter(), "group_by": "publication_year", "per_page": PER_PAGE}
    )
    return parse_population(payload)


def fetch_year_sample(
    year: int,
    sample_size: int,
    seed: int,
    fetcher: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fetch one reproducible annual sample using supported basic paging."""
    if not 0 <= sample_size <= SAMPLE_MAX:
        raise ValueError(f"Annual sample must be between 0 and {SAMPLE_MAX}")
    if sample_size == 0:
        return []
    rows: list[dict[str, Any]] = []
    expected_count: int | None = None
    page = 1
    while expected_count is None or len(rows) < expected_count:
        payload = fetcher(
            {
                "filter": build_filter(year=year),
                "sample": sample_size,
                "seed": seed,
                "select": SELECT_FIELDS,
                "per_page": PER_PAGE,
                "page": page,
            }
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise RuntimeError("OpenAlex response field 'results' is not a list")
        meta = payload.get("meta") or {}
        try:
            page_count = int(meta.get("count"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Sample response has no valid meta.count") from exc
        if expected_count is None:
            expected_count = page_count
            if expected_count != sample_size:
                raise RuntimeError(
                    f"OpenAlex returned sample count {expected_count}; expected {sample_size}"
                )
        elif page_count != expected_count:
            raise RuntimeError("OpenAlex sample count changed during pagination")
        if not results:
            raise RuntimeError("OpenAlex sample pagination ended before meta.count")
        rows.extend(results)
        page += 1
    if len(rows) != expected_count:
        raise RuntimeError(
            f"OpenAlex sample pagination returned {len(rows)} rows; expected {expected_count}"
        )
    return rows


def entity_key(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    identifier = short_id(value.get("id"))
    if not identifier:
        return None
    return identifier, str(value.get("display_name") or identifier)


class QualityAccumulator:
    """Small in-process profiler for the final detailed sample."""

    def __init__(self) -> None:
        self.missing_doi = 0
        self.missing_primary_topic = 0
        self.missing_country = 0
        self.missing_institution = 0
        self.missing_source = 0
        self.work_types: Counter[str] = Counter()
        self.primary_topics: Counter[str] = Counter()
        self.subfields: Counter[str] = Counter()
        self.fields: Counter[str] = Counter()
        self.domains: Counter[str] = Counter()
        self.names: dict[str, str] = {}

    def observe(self, work: dict[str, Any]) -> None:
        if not work.get("doi"):
            self.missing_doi += 1
        primary = entity_key(work.get("primary_topic"))
        if primary:
            identifier, name = primary
            self.primary_topics[identifier] += 1
            self.names[identifier] = name
        else:
            self.missing_primary_topic += 1
        self.work_types[str(work.get("type") or "<missing>")] += 1

        authorships = work.get("authorships")
        countries: set[str] = set()
        institutions: set[str] = set()
        if isinstance(authorships, list):
            for authorship in authorships:
                if not isinstance(authorship, dict):
                    continue
                raw_countries = authorship.get("countries")
                if isinstance(raw_countries, list):
                    countries.update(str(value) for value in raw_countries if value)
                raw_institutions = authorship.get("institutions")
                if isinstance(raw_institutions, list):
                    for institution in raw_institutions:
                        if not isinstance(institution, dict):
                            continue
                        identifier = short_id(institution.get("id"))
                        if identifier:
                            institutions.add(identifier)
                        if institution.get("country_code"):
                            countries.add(str(institution["country_code"]))
        if not countries:
            self.missing_country += 1
        if not institutions:
            self.missing_institution += 1

        location = work.get("primary_location")
        source = location.get("source") if isinstance(location, dict) else None
        if not entity_key(source):
            self.missing_source += 1

        hierarchy_counters = (
            ("subfield", self.subfields),
            ("field", self.fields),
            ("domain", self.domains),
        )
        topics = work.get("topics") if isinstance(work.get("topics"), list) else []
        for field_name, counter in hierarchy_counters:
            work_values: set[str] = set()
            for topic in topics:
                value = topic.get(field_name) if isinstance(topic, dict) else None
                parsed = entity_key(value)
                if parsed:
                    identifier, name = parsed
                    work_values.add(identifier)
                    self.names[identifier] = name
            counter.update(work_values)

    def top(self, counter: Counter[str], limit: int = 20) -> list[dict[str, Any]]:
        return [
            {"id": identifier, "display_name": self.names.get(identifier, identifier), "count": count}
            for identifier, count in counter.most_common(limit)
        ]


def collect_sample(
    temporary_output: Path,
    plan: dict[int, dict[str, Any]],
    fetcher: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], QualityAccumulator]:
    """Fetch annual samples, enforce guardrails, deduplicate, and refill if needed."""
    temporary_output.parent.mkdir(parents=True, exist_ok=True)
    seen_ids: set[str] = set()
    quality = QualityAccumulator()
    stats: dict[str, Any] = {
        "records_received": 0,
        "records_inspected": 0,
        "records_written": 0,
        "duplicates_removed": 0,
        "missing_ids": 0,
        "missing_topics": 0,
        "selected_topic_guardrail_failures": 0,
        "scope_guardrail_failures": 0,
        "guardrail_failures": 0,
        "refill_attempts": 0,
        "actual_by_year": {str(year): 0 for year in sorted(plan)},
    }

    def consider(work: Any, year: int, output_file: Any) -> bool:
        stats["records_inspected"] += 1
        reason = validate_work(work, expected_year=year) if isinstance(work, dict) else "missing_id"
        if reason:
            if reason == "missing_id":
                stats["missing_ids"] += 1
            elif reason == "missing_topics":
                stats["missing_topics"] += 1
            elif reason == "selected_topic":
                stats["selected_topic_guardrail_failures"] += 1
            else:
                stats["scope_guardrail_failures"] += 1
            stats["guardrail_failures"] += 1
            return False
        work_id = short_id(work["id"])
        assert work_id is not None
        if work_id in seen_ids:
            stats["duplicates_removed"] += 1
            return False
        seen_ids.add(work_id)
        output_file.write(json.dumps(work, ensure_ascii=False) + "\n")
        stats["records_written"] += 1
        stats["actual_by_year"][str(year)] += 1
        quality.observe(work)
        return True

    with temporary_output.open("w", encoding="utf-8") as output_file:
        for year, row in sorted(plan.items()):
            target = int(row["target_sample"])
            annual_rows = fetch_year_sample(year, target, int(row["seed"]), fetcher)
            stats["records_received"] += len(annual_rows)
            for work in annual_rows:
                if stats["actual_by_year"][str(year)] >= target:
                    break
                consider(work, year, output_file)

            for attempt in range(1, MAX_REFILL_ATTEMPTS + 1):
                deficit = target - stats["actual_by_year"][str(year)]
                if deficit <= 0:
                    break
                stats["refill_attempts"] += 1
                refill_size = min(SAMPLE_MAX, row["population"], max(100, deficit * 3))
                refill_seed = int(row["seed"]) + attempt * 100_000
                refill_rows = fetch_year_sample(year, refill_size, refill_seed, fetcher)
                stats["records_received"] += len(refill_rows)
                for work in refill_rows:
                    if stats["actual_by_year"][str(year)] >= target:
                        break
                    consider(work, year, output_file)

            actual = stats["actual_by_year"][str(year)]
            if actual != target:
                raise RuntimeError(
                    f"Could not fill {year} stratum: expected {target}, obtained {actual}"
                )
            print(f"{year}: sampled {actual}/{row['population']}", flush=True)

    if stats["records_written"] != sum(row["target_sample"] for row in plan.values()):
        raise RuntimeError("Final unique sample size differs from allocated target")
    if stats["records_written"] != len(seen_ids):
        raise RuntimeError("Final output contains duplicate OpenAlex Work IDs")
    return stats, quality


def sanity_check(path: Path, row_count: int) -> dict[str, Any]:
    """Check a deterministic 100-work sample from the completed temporary file."""
    size = min(100, row_count)
    indices = set(random.Random(SANITY_SAMPLE_SEED).sample(range(row_count), size))
    works: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for index, line in enumerate(source):
            if index in indices:
                works.append(json.loads(line))
    errors: list[str] = []
    identifiers = [short_id(work.get("id")) for work in works]
    if len(identifiers) != len(set(identifiers)):
        errors.append("duplicate_ids")
    for work in works:
        reason = validate_work(work)
        if reason:
            errors.append(reason)
        if work.get("publication_year") not in YEARS:
            errors.append("year_scope")
    return {
        "seed": SANITY_SAMPLE_SEED,
        "sample_size": size,
        "unique_ids": len(set(identifiers)),
        "all_have_topics": all(bool(work.get("topics")) for work in works),
        "all_have_selected_topic": all(bool(matched_selected_topic_ids(work)) for work in works),
        "all_years_in_scope": all(work.get("publication_year") in YEARS for work in works),
        "all_types_allowed": all(work.get("type") in ALLOWED_WORK_TYPES for work in works),
        "none_explicitly_retracted": all(work.get("is_retracted") is not True for work in works),
        "errors": sorted(set(errors)),
        "passed": not errors and len(works) == size,
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_quality_report(
    population_total: int,
    population_by_year: dict[int, int],
    plan: dict[int, dict[str, Any]],
    stats: dict[str, Any],
    quality: QualityAccumulator,
    sanity: dict[str, Any],
) -> dict[str, Any]:
    actual_total = stats["records_written"]
    representativeness: dict[str, Any] = {}
    for year, row in sorted(plan.items()):
        actual = stats["actual_by_year"][str(year)]
        sample_share = actual / actual_total if actual_total else 0.0
        representativeness[str(year)] = {
            "population_share": row["population_share"],
            "sample_share": sample_share,
            "absolute_share_difference": abs(row["population_share"] - sample_share),
        }
    return {
        "generated_at": utc_now(),
        "method_name": METHOD_NAME,
        "method_version": METHOD_VERSION,
        "methodological_validation": {
            "sample_size": 300,
            "clear_ai": 295,
            "ambiguous": 2,
            "false_positive": 3,
            "strict_precision": 0.9833,
            "broad_precision": 0.99,
            "strict_precision_2018_2021": 0.9667,
            "strict_precision_2022_2025": 1.0,
        },
        "population_total": population_total,
        "population_by_year": {str(year): count for year, count in sorted(population_by_year.items())},
        "target_sample_size": sum(row["target_sample"] for row in plan.values()),
        "actual_sample_size": actual_total,
        "unique_work_ids": actual_total,
        "duplicates": 0,
        "duplicates_removed_during_extraction": stats["duplicates_removed"],
        "guardrail_failures": stats["guardrail_failures"],
        "expected_by_year": {str(year): row["target_sample"] for year, row in sorted(plan.items())},
        "actual_by_year": stats["actual_by_year"],
        "sampling_representativeness": representativeness,
        "missing_doi": quality.missing_doi,
        "missing_primary_topic": quality.missing_primary_topic,
        "missing_country": quality.missing_country,
        "missing_institution": quality.missing_institution,
        "missing_source": quality.missing_source,
        "work_type_distribution": dict(sorted(quality.work_types.items())),
        "top_primary_topics": quality.top(quality.primary_topics),
        "top_subfields": quality.top(quality.subfields),
        "top_fields": quality.top(quality.fields),
        "top_domains": quality.top(quality.domains),
        "sanity_sample": sanity,
    }


def run_extraction(
    population_path: Path,
    output_path: Path,
    manifest_path: Path,
    quality_path: Path,
    *,
    fetcher: Callable[[dict[str, Any]], dict[str, Any]],
    request_stats: dict[str, Any],
) -> dict[str, Any]:
    """Run the complete extraction and publish all outputs only after success."""
    started_at = utc_now()
    temporary_paths = {
        final: final.with_name(f"{final.name}.tmp")
        for final in (population_path, output_path, manifest_path, quality_path)
    }
    for temporary in temporary_paths.values():
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.unlink(missing_ok=True)
    try:
        population_total, population_by_year = fetch_population(fetcher)
        plan = build_sampling_plan(population_by_year)
        population_document = {
            "method": METHOD_NAME,
            "method_version": METHOD_VERSION,
            "topic_ids": sorted(AI_TOPIC_SET_FINAL),
            "date_range": {"from": FROM_DATE, "to": TO_DATE},
            "allowed_work_types": list(ALLOWED_WORK_TYPES),
            "is_retracted": False,
            "population_total": population_total,
            "population_by_year": {
                str(year): count for year, count in sorted(population_by_year.items())
            },
            "retrieved_at": utc_now(),
        }
        write_json(temporary_paths[population_path], population_document)

        extraction_stats, quality = collect_sample(temporary_paths[output_path], plan, fetcher)
        sanity = sanity_check(temporary_paths[output_path], extraction_stats["records_written"])
        if not sanity["passed"]:
            raise RuntimeError(f"Final sanity sample failed: {sanity['errors']}")

        completed_at = utc_now()
        actual_total = extraction_stats["records_written"]
        sampling_by_year: dict[str, Any] = {}
        for year, row in sorted(plan.items()):
            actual = extraction_stats["actual_by_year"][str(year)]
            sampling_by_year[str(year)] = {
                **row,
                "actual_sample": actual,
                "sample_share": actual / actual_total if actual_total else 0.0,
            }
        manifest = {
            "method_name": METHOD_NAME,
            "method_version": METHOD_VERSION,
            "topic_config_version": TOPIC_CONFIG["version"],
            "topic_config_status": TOPIC_CONFIG["status"],
            "selected_topic_ids": sorted(AI_TOPIC_SET_FINAL),
            "selected_topic_names": [AI_TOPIC_NAMES_FINAL[key] for key in sorted(AI_TOPIC_SET_FINAL)],
            "sampling_method": "proportional_stratified_random_sample_by_publication_year_largest_remainder",
            "target_sample_size": sum(row["target_sample"] for row in plan.values()),
            "actual_sample_size": actual_total,
            "seed_base": SEED_BASE,
            "seed_derivation": "decimal concatenation of seed_base and the final two digits of publication year",
            "population_total": population_total,
            "population_by_year": population_document["population_by_year"],
            "sampling_by_year": sampling_by_year,
            "date_range": {"from": FROM_DATE, "to": TO_DATE},
            "allowed_work_types": list(ALLOWED_WORK_TYPES),
            "is_retracted": False,
            "openalex_endpoint": OPENALEX_ENDPOINT,
            "exact_filters": {
                "population": build_filter(),
                "annual_template": build_filter(year=2018).replace("publication_year:2018", "publication_year:{YEAR}"),
            },
            "population_query": {"group_by": "publication_year", "per_page": PER_PAGE},
            "sample_query": {"sample": "target_sample_y", "seed": "seed_y", "per_page": PER_PAGE, "paging": "page"},
            "started_at": started_at,
            "completed_at": completed_at,
            "api": {**request_stats, "cost_usd": round(request_stats["cost_usd"], 4)},
            **extraction_stats,
            "missing_by_field": {
                "doi": quality.missing_doi,
                "primary_topic": quality.missing_primary_topic,
                "country": quality.missing_country,
                "institution": quality.missing_institution,
                "source": quality.missing_source,
            },
            "sanity_sample": sanity,
            "outputs": {
                "population": str(population_path),
                "works": str(output_path),
                "manifest": str(manifest_path),
                "quality_report": str(quality_path),
            },
        }
        write_json(temporary_paths[manifest_path], manifest)
        report = build_quality_report(
            population_total, population_by_year, plan, extraction_stats, quality, sanity
        )
        write_json(temporary_paths[quality_path], report)

        for final in (population_path, output_path, manifest_path, quality_path):
            os.replace(temporary_paths[final], final)
        return manifest
    except Exception:
        for temporary in temporary_paths.values():
            temporary.unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--quality-report", type=Path, default=DEFAULT_QUALITY_REPORT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    request_stats = new_api_stats()
    print("Retrieving complete OpenAlex population counts...", flush=True)
    manifest = run_extraction(
        args.population,
        args.output,
        args.manifest,
        args.quality_report,
        fetcher=lambda params: fetch_json(params, stats=request_stats),
        request_stats=request_stats,
    )
    print(f"Saved {manifest['actual_sample_size']} unique works to {args.output}")
    print(f"Population: {manifest['population_total']}")
    print(f"Manifest: {args.manifest}")
    print(f"Quality report: {args.quality_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
