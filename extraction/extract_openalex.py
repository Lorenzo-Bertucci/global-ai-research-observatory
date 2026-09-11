#!/usr/bin/env python3
"""Extract the final 50k reproducible, year-stratified OpenAlex sample."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/raw/openalex_ai_works.jsonl"
DEFAULT_MANIFEST = ROOT / "data/raw/openalex_ai_manifest.json"

# Final corpus and sampling methodology. No external OpenAlex config is required.
OPENALEX_ENDPOINT = "https://api.openalex.org/works"
OPENALEX_RATE_LIMIT_ENDPOINT = "https://api.openalex.org/rate-limit"
AI_SUBFIELD_ID = "1702"
START_YEAR = 2018
END_YEAR = 2025
YEARS = tuple(range(START_YEAR, END_YEAR + 1))
TARGET_SAMPLE_SIZE = 50_000
MASTER_SEED = 20_260_910
ALLOWED_WORK_TYPES = (
    "article",
    "review",
    "conference-paper",
    "preprint",
    "book",
    "book-chapter",
)
PER_PAGE = 100
SAMPLE_MAX = 10_000
REQUEST_TIMEOUT_SECONDS = 45
MAX_ATTEMPTS = 4
RETRY_CREDIT_RESERVE = 16
METHOD_NAME = "primary_topic_ai_subfield_year_stratified_native_sample"
METHOD_VERSION = "3.0"
SELECT_FIELDS = ",".join(
    (
        "id", "doi", "display_name", "title", "publication_date",
        "publication_year", "type", "language", "cited_by_count",
        "is_retracted", "primary_topic", "topics", "keywords",
        "authorships", "primary_location", "open_access",
    )
)


class ExtractionError(RuntimeError):
    """Raised when source or output validation prevents atomic publication."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_id(value: Any) -> str | None:
    if value is None:
        return None
    identifier = str(value).strip().rstrip("/").rsplit("/", 1)[-1]
    return identifier or None


def read_api_key() -> str | None:
    """Read a key from process state or the ignored local .env file."""
    for name in ("OPENALEX_API_KEY", "OPEN_ALEX_KEY"):
        if os.getenv(name):
            return os.environ[name]
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in {"OPENALEX_API_KEY", "OPEN_ALEX_KEY"}:
            return value.strip().strip('"').strip("'") or None
    return None


def build_filter(*, year: int) -> str:
    if year not in YEARS:
        raise ValueError(f"year must be between {START_YEAR} and {END_YEAR}")
    return ",".join(
        (
            f"primary_topic.subfield.id:{AI_SUBFIELD_ID}",
            f"publication_year:{year}",
            f"type:{'|'.join(ALLOWED_WORK_TYPES)}",
            "is_retracted:false",
        )
    )


def annual_seed(year: int) -> int:
    if year not in YEARS:
        raise ValueError(f"year must be between {START_YEAR} and {END_YEAR}")
    return MASTER_SEED + year


def new_api_stats() -> dict[str, Any]:
    return {
        "requests": 0, "retries": 0, "responses_429": 0,
        "responses_5xx": 0, "timeouts": 0, "network_errors": 0,
        "cost_usd": 0.0, "rate_limit_remaining_last": None,
        "rate_limit_reset_seconds_last": None,
    }


def fetch_json(
    params: dict[str, Any] | None = None,
    *,
    endpoint: str = OPENALEX_ENDPOINT,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET one OpenAlex response with bounded retry and no credential logging."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    request_params = {
        key: value for key, value in (params or {}).items() if value is not None
    }
    api_key = read_api_key()
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "User-Agent": "global-ai-research-observatory/final-native-sample-v3",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = endpoint + (f"?{urlencode(request_params)}" if request_params else "")
    request = Request(url, headers=headers)
    open_url = opener or urlopen
    retryable = {429, 500, 502, 503, 504}
    for attempt in range(1, max_attempts + 1):
        if stats is not None:
            stats["requests"] += 1
        try:
            with open_url(request, timeout=timeout) as response:
                compressed = getattr(response, "headers", {}).get("Content-Encoding") == "gzip"
                payload = json.load(gzip.GzipFile(fileobj=response) if compressed else response)
                if not isinstance(payload, dict):
                    raise ExtractionError("OpenAlex returned a non-object response")
                if stats is not None:
                    meta = payload.get("meta") or {}
                    stats["cost_usd"] += float(meta.get("cost_usd") or 0)
                    response_headers = getattr(response, "headers", {})
                    stats["rate_limit_remaining_last"] = response_headers.get("X-RateLimit-Remaining")
                    stats["rate_limit_reset_seconds_last"] = response_headers.get("X-RateLimit-Reset")
                return payload
        except HTTPError as exc:
            if stats is not None:
                stats["responses_429"] += int(exc.code == 429)
                stats["responses_5xx"] += int(500 <= exc.code <= 599)
            if exc.code not in retryable or attempt == max_attempts:
                raise ExtractionError(
                    f"OpenAlex returned HTTP {exc.code} after {attempt} attempt(s)"
                ) from None
            if stats is not None:
                stats["retries"] += 1
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = float(retry_after) if retry_after else 2 ** (attempt - 1)
            except ValueError:
                delay = 2 ** (attempt - 1)
            sleep(min(max(delay, 0), 30))
        except (URLError, TimeoutError) as exc:
            if stats is not None:
                stats["network_errors"] += 1
                reason = getattr(exc, "reason", exc)
                stats["timeouts"] += int(
                    isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError)
                )
            if attempt == max_attempts:
                raise ExtractionError(
                    f"Could not reach OpenAlex after {attempt} attempt(s)"
                ) from None
            if stats is not None:
                stats["retries"] += 1
            sleep(min(2 ** (attempt - 1), 16))
    raise ExtractionError("OpenAlex request failed")


def parse_rate_limit(payload: dict[str, Any]) -> dict[str, Any]:
    """Retain budget metadata only; never retain the returned key fragment."""
    rate = payload.get("rate_limit") if isinstance(payload.get("rate_limit"), dict) else payload
    fields = (
        "credits_limit", "credits_remaining", "credits_used",
        "daily_budget_usd", "daily_remaining_usd", "daily_used_usd",
        "resets_at", "resets_in_seconds",
    )
    return {field: rate.get(field) for field in fields if rate.get(field) is not None}


def ensure_budget(rate: dict[str, Any], required_credits: int) -> None:
    remaining = rate.get("credits_remaining")
    if remaining is None:
        raise ExtractionError("OpenAlex did not report the remaining API budget")
    if int(remaining) < required_credits:
        raise ExtractionError(
            f"Insufficient OpenAlex budget: {remaining} credits remain; "
            f"at least {required_credits} are required"
        )


def fetch_population_by_year(
    fetcher: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[int, int]:
    """Read current qualifying counts using one metadata-only request per year."""
    counts: dict[int, int] = {}
    for year in YEARS:
        payload = fetcher(
            {"filter": build_filter(year=year), "select": "id", "per_page": 1}
        )
        try:
            count = int((payload.get("meta") or {}).get("count"))
        except (TypeError, ValueError) as exc:
            raise ExtractionError(f"OpenAlex returned no valid count for {year}") from exc
        if count < 1:
            raise ExtractionError(f"OpenAlex returned an empty qualifying stratum for {year}")
        counts[year] = count
    return counts


def largest_remainder_allocation(
    population_by_year: dict[int, int], target: int | None = None
) -> dict[int, int]:
    """Allocate target proportionally with deterministic year tie-breaking."""
    if target is None:
        target = TARGET_SAMPLE_SIZE
    if target < 1 or set(population_by_year) != set(YEARS):
        raise ValueError("population must cover all configured years and target must be positive")
    if any(count < 1 for count in population_by_year.values()):
        raise ValueError("annual populations must be positive")
    total = sum(population_by_year.values())
    if target > total:
        raise ValueError("sample target exceeds the qualifying population")
    allocation: dict[int, int] = {}
    remainders: list[tuple[int, int]] = []
    for year, population in sorted(population_by_year.items()):
        base, remainder = divmod(target * population, total)
        allocation[year] = base
        remainders.append((remainder, year))
    missing = target - sum(allocation.values())
    for _remainder, year in sorted(remainders, key=lambda row: (-row[0], row[1]))[:missing]:
        allocation[year] += 1
    if sum(allocation.values()) != target:
        raise AssertionError("largest-remainder allocation did not reach the target")
    if any(value > SAMPLE_MAX for value in allocation.values()):
        raise ExtractionError("An annual allocation exceeds OpenAlex sample=10000")
    return allocation


def validate_work(work: Any, *, expected_year: int | None = None) -> str | None:
    if not isinstance(work, dict) or not (short_id(work.get("id")) or "").startswith("W"):
        return "invalid_work_id"
    year = work.get("publication_year")
    if year not in YEARS or (expected_year is not None and year != expected_year):
        return "publication_year"
    try:
        publication_date = date.fromisoformat(work.get("publication_date") or "")
    except (TypeError, ValueError):
        return "publication_date"
    if publication_date.year != year:
        return "publication_date_year_mismatch"
    if work.get("type") not in ALLOWED_WORK_TYPES:
        return "work_type"
    if work.get("is_retracted") is not False:
        return "retracted_or_unknown"
    primary = work.get("primary_topic")
    if not isinstance(primary, dict) or not short_id(primary.get("id")):
        return "primary_topic"
    if short_id((primary.get("subfield") or {}).get("id")) != AI_SUBFIELD_ID:
        return "primary_topic_subfield"
    topics = work.get("topics")
    if not isinstance(topics, list) or not topics:
        return "topics"
    topic_ids = {short_id(topic.get("id")) for topic in topics if isinstance(topic, dict)}
    if short_id(primary.get("id")) not in topic_ids:
        return "primary_topic_relationship"
    return None


def fetch_year_sample(
    year: int,
    sample_size: int,
    seed: int,
    fetcher: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Page through one OpenAlex native sampled result set exactly once."""
    if not 1 <= sample_size <= SAMPLE_MAX:
        raise ValueError(f"sample_size must be between 1 and {SAMPLE_MAX}")
    rows: list[dict[str, Any]] = []
    expected_pages = math.ceil(sample_size / PER_PAGE)
    for page in range(1, expected_pages + 1):
        payload = fetcher(
            {
                "filter": build_filter(year=year), "sample": sample_size,
                "seed": seed, "select": SELECT_FIELDS, "per_page": PER_PAGE,
                "page": page,
            }
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise ExtractionError(f"OpenAlex results are invalid for {year} page {page}")
        try:
            reported = int((payload.get("meta") or {}).get("count"))
        except (TypeError, ValueError) as exc:
            raise ExtractionError(f"OpenAlex sample count is invalid for {year}") from exc
        if reported != sample_size:
            raise ExtractionError(
                f"OpenAlex reported {reported} sampled works for {year}; expected {sample_size}"
            )
        rows.extend(results)
    if len(rows) != sample_size:
        raise ExtractionError(
            f"Downloaded {len(rows)} sampled works for {year}; expected {sample_size}"
        )
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(
    *,
    output_path: Path = DEFAULT_OUTPUT,
    manifest_path: Path = DEFAULT_MANIFEST,
    fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    rate_fetcher: Callable[[], dict[str, Any]] | None = None,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build, fully validate, and atomically publish the final direct sample."""
    request_stats = stats if stats is not None else new_api_stats()
    get = fetcher or (lambda params: fetch_json(params, stats=request_stats))
    get_rate = rate_fetcher or (
        lambda: fetch_json(endpoint=OPENALEX_RATE_LIMIT_ENDPOINT, stats=request_stats)
    )
    temporary_output = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for path in (temporary_output, temporary_manifest):
        path.unlink(missing_ok=True)

    # Eight counts + at most 507 annual pages + a bounded retry reserve.
    initial_required = (
        len(YEARS) + math.ceil(TARGET_SAMPLE_SIZE / PER_PAGE)
        + len(YEARS) - 1 + RETRY_CREDIT_RESERVE
    )
    started_at = utc_now()
    try:
        initial_rate = parse_rate_limit(get_rate())
        ensure_budget(initial_rate, initial_required)
        population = fetch_population_by_year(get)
        allocation = largest_remainder_allocation(population)
        sample_pages = sum(math.ceil(value / PER_PAGE) for value in allocation.values())
        pre_sample_rate = parse_rate_limit(get_rate())
        ensure_budget(pre_sample_rate, sample_pages + RETRY_CREDIT_RESERVE)

        seen: set[str] = set()
        actual_by_year: Counter[int] = Counter()
        duplicate_count = 0
        guardrail_failures = 0
        with temporary_output.open("w", encoding="utf-8") as destination:
            for year in YEARS:
                rows = fetch_year_sample(year, allocation[year], annual_seed(year), get)
                for work in rows:
                    failure = validate_work(work, expected_year=year)
                    if failure:
                        guardrail_failures += 1
                        raise ExtractionError(f"Corpus guardrail failed for {year}: {failure}")
                    work_id = short_id(work["id"])
                    assert work_id is not None
                    if work_id in seen:
                        duplicate_count += 1
                        raise ExtractionError(f"Duplicate sampled OpenAlex work: {work_id}")
                    seen.add(work_id)
                    actual_by_year[year] += 1
                    destination.write(json.dumps(work, ensure_ascii=False) + "\n")
                print(
                    f"{year}: downloaded {actual_by_year[year]:,}/{allocation[year]:,}",
                    flush=True,
                )
            destination.flush()
            os.fsync(destination.fileno())

        if len(seen) != TARGET_SAMPLE_SIZE:
            raise ExtractionError(
                f"Final unique sample has {len(seen)} works; expected {TARGET_SAMPLE_SIZE}"
            )
        if dict(actual_by_year) != allocation:
            raise ExtractionError("Actual annual counts differ from the allocation")

        completed_at = utc_now()
        manifest = {
            "method_name": METHOD_NAME,
            "method_version": METHOD_VERSION,
            "extraction_timestamp_utc": completed_at,
            "source": OPENALEX_ENDPOINT,
            "years": [START_YEAR, END_YEAR],
            "corpus_filter": "primary_topic.subfield.id = 1702",
            "ai_subfield_id": AI_SUBFIELD_ID,
            "allowed_work_types": list(ALLOWED_WORK_TYPES),
            "exclude_retracted": True,
            "all_associated_topics_preserved": True,
            "target_sample_size": TARGET_SAMPLE_SIZE,
            "master_seed": MASTER_SEED,
            "annual_seed_strategy": "master_seed + publication_year",
            "annual_seeds": {str(year): annual_seed(year) for year in YEARS},
            "annual_population_counts": {str(year): population[year] for year in YEARS},
            "total_qualifying_population": sum(population.values()),
            "allocation_method": "proportional by year; largest remainder with ascending-year tie-break",
            "annual_sample_allocations": {str(year): allocation[year] for year in YEARS},
            "actual_downloaded_by_year": {str(year): actual_by_year[year] for year in YEARS},
            "actual_total": len(seen),
            "duplicate_count": duplicate_count,
            "corpus_guardrail_failure_count": guardrail_failures,
            "complete": True,
            "sample_parameter": "OpenAlex native sample + seed; basic page paging",
            "per_page": PER_PAGE,
            "started_at_utc": started_at,
            "sha256": sha256_file(temporary_output),
            "api": {
                "requests": request_stats["requests"],
                "retries": request_stats["retries"],
                "responses_429": request_stats["responses_429"],
                "responses_5xx": request_stats["responses_5xx"],
                "timeouts": request_stats["timeouts"],
                "network_errors": request_stats["network_errors"],
                "cost_usd": round(request_stats["cost_usd"], 4),
                "initial_budget": initial_rate,
                "pre_sample_budget": pre_sample_rate,
            },
        }
        temporary_manifest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_output, output_path)
        os.replace(temporary_manifest, manifest_path)
        return manifest
    except Exception:
        temporary_output.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = extract(output_path=args.output, manifest_path=args.manifest)
    except (ExtractionError, ValueError, OSError) as exc:
        raise SystemExit(f"OpenAlex extraction failed: {exc}") from exc
    print(
        f"Saved {manifest['actual_total']:,} unique works; "
        f"qualifying population {manifest['total_qualifying_population']:,}."
    )
    print(f"Manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
