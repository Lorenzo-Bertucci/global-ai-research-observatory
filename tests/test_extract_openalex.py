import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from extraction import extract_openalex as extractor


class Response:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


AI_TOPIC = {
    "id": "https://openalex.org/T10028",
    "display_name": "Topic Modeling",
    "subfield": {"id": "https://openalex.org/subfields/1702"},
}
SECONDARY_TOPIC = {
    "id": "https://openalex.org/T99999",
    "display_name": "Secondary",
    "subfield": {"id": "https://openalex.org/subfields/9999"},
}


def work(work_id="W1", year=2022, topics=None, **extra):
    topics = [AI_TOPIC] if topics is None else topics
    value = {
        "id": f"https://openalex.org/{work_id}" if work_id else None,
        "doi": None,
        "display_name": "Example",
        "title": "Example",
        "publication_date": f"{year}-01-01",
        "publication_year": year,
        "type": "article",
        "language": "en",
        "cited_by_count": 0,
        "is_retracted": False,
        "primary_topic": topics[0] if topics else None,
        "topics": topics,
        "keywords": [],
        "authorships": [],
        "primary_location": None,
        "open_access": None,
    }
    value.update(extra)
    return value


class OpenAlexExtractionTests(unittest.TestCase):
    def test_corpus_constants_and_year_filter_are_explicit(self):
        self.assertEqual(extractor.AI_SUBFIELD_ID, "1702")
        self.assertEqual(extractor.YEARS, tuple(range(2018, 2026)))
        self.assertEqual(extractor.TARGET_SAMPLE_SIZE, 50_000)
        value = extractor.build_filter(year=2022)
        self.assertIn("primary_topic.subfield.id:1702", value)
        self.assertIn("publication_year:2022", value)
        self.assertIn("is_retracted:false", value)
        self.assertNotIn("topics.id:", value)

    def test_allocation_is_deterministic_and_totals_exactly_50000(self):
        population = {
            2018: 135_061,
            2019: 149_177,
            2020: 161_895,
            2021: 171_702,
            2022: 168_366,
            2023: 196_327,
            2024: 225_986,
            2025: 278_602,
        }
        first = extractor.largest_remainder_allocation(population)
        second = extractor.largest_remainder_allocation(population)
        self.assertEqual(first, second)
        self.assertEqual(sum(first.values()), 50_000)
        self.assertTrue(all(value <= extractor.SAMPLE_MAX for value in first.values()))

    def test_annual_seed_is_stable_and_year_specific(self):
        self.assertEqual(extractor.annual_seed(2018), extractor.MASTER_SEED + 2018)
        self.assertEqual(extractor.annual_seed(2025), extractor.MASTER_SEED + 2025)
        self.assertNotEqual(extractor.annual_seed(2018), extractor.annual_seed(2019))

    def test_primary_selects_work_and_all_topics_are_preserved(self):
        selected = work(topics=[AI_TOPIC, SECONDARY_TOPIC])
        self.assertIsNone(extractor.validate_work(selected, expected_year=2022))
        self.assertEqual(selected["topics"], [AI_TOPIC, SECONDARY_TOPIC])
        rejected = work(topics=[SECONDARY_TOPIC, AI_TOPIC])
        self.assertEqual(
            extractor.validate_work(rejected), "primary_topic_subfield"
        )

    def test_scope_and_relationship_guardrails(self):
        cases = (
            ({"year": 2017}, "publication_year"),
            ({"publication_date": "2021-01-01"}, "publication_date_year_mismatch"),
            ({"publication_date": "bad"}, "publication_date"),
            ({"type": "dataset"}, "work_type"),
            ({"is_retracted": True}, "retracted_or_unknown"),
            ({"is_retracted": None}, "retracted_or_unknown"),
            ({"primary_topic": None}, "primary_topic"),
            ({"topics": [SECONDARY_TOPIC], "primary_topic": AI_TOPIC}, "primary_topic_relationship"),
        )
        for fields, expected in cases:
            with self.subTest(fields=fields):
                self.assertEqual(extractor.validate_work(work(**fields)), expected)

    def test_population_count_uses_one_matching_request_per_year(self):
        calls = []

        def fetch(params):
            calls.append(params)
            year = int(params["filter"].split("publication_year:", 1)[1].split(",", 1)[0])
            return {"meta": {"count": year}}

        counts = extractor.fetch_population_by_year(fetch)
        self.assertEqual(counts[2018], 2018)
        self.assertEqual(len(calls), 8)
        self.assertTrue(all(call["per_page"] == 1 for call in calls))

    def test_native_sample_pages_every_result_and_keeps_secondary_topics(self):
        calls = []

        def fetch(params):
            calls.append(params)
            page = params["page"]
            ids = range((page - 1) * 2, min(page * 2, 5))
            return {
                "meta": {"count": 5},
                "results": [work(f"W{index}", topics=[AI_TOPIC, SECONDARY_TOPIC]) for index in ids],
            }

        with patch.object(extractor, "PER_PAGE", 2):
            rows = extractor.fetch_year_sample(2022, 5, 77, fetch)
        self.assertEqual(len(rows), 5)
        self.assertEqual([call["page"] for call in calls], [1, 2, 3])
        self.assertTrue(all(call["sample"] == 5 and call["seed"] == 77 for call in calls))
        self.assertEqual(rows[0]["topics"], [AI_TOPIC, SECONDARY_TOPIC])

    def test_atomic_success_writes_manifest_and_exact_unique_sample(self):
        years = (2018, 2019)
        sample_rows = {
            2018: [work("W1", 2018, [AI_TOPIC, SECONDARY_TOPIC]), work("W2", 2018)],
            2019: [work("W3", 2019), work("W4", 2019)],
        }

        def fetch(params):
            year = int(params["filter"].split("publication_year:", 1)[1].split(",", 1)[0])
            if "sample" not in params:
                return {"meta": {"count": 10}}
            return {"meta": {"count": 2}, "results": sample_rows[year]}

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = base / "works.jsonl"
            manifest_path = base / "manifest.json"
            stats = extractor.new_api_stats()
            with (
                patch.object(extractor, "YEARS", years),
                patch.object(extractor, "TARGET_SAMPLE_SIZE", 4),
                patch.object(extractor, "PER_PAGE", 2),
            ):
                manifest = extractor.extract(
                    output_path=output,
                    manifest_path=manifest_path,
                    fetcher=fetch,
                    rate_fetcher=lambda: {"credits_remaining": 100},
                    stats=stats,
                )
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertEqual(len({row["id"] for row in rows}), 4)
            self.assertEqual(rows[0]["topics"], [AI_TOPIC, SECONDARY_TOPIC])
            self.assertEqual(manifest["actual_total"], 4)
            self.assertEqual(manifest["duplicate_count"], 0)
            self.assertEqual(manifest["corpus_guardrail_failure_count"], 0)
            self.assertTrue(manifest["complete"])
            self.assertEqual(extractor.sha256_file(output), manifest["sha256"])
            self.assertFalse((base / "works.jsonl.tmp").exists())

    def test_failure_preserves_previous_canonical_files(self):
        years = (2018, 2019)

        def fetch(params):
            year = int(params["filter"].split("publication_year:", 1)[1].split(",", 1)[0])
            if "sample" not in params:
                return {"meta": {"count": 10}}
            invalid = work(f"W{year}", year, is_retracted=True)
            valid = work(f"W{year}x", year)
            return {"meta": {"count": 2}, "results": [invalid, valid]}

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = base / "works.jsonl"
            manifest = base / "manifest.json"
            output.write_text("previous raw\n")
            manifest.write_text('{"previous": true}\n')
            with (
                patch.object(extractor, "YEARS", years),
                patch.object(extractor, "TARGET_SAMPLE_SIZE", 4),
                patch.object(extractor, "PER_PAGE", 2),
                self.assertRaisesRegex(extractor.ExtractionError, "guardrail"),
            ):
                extractor.extract(
                    output_path=output,
                    manifest_path=manifest,
                    fetcher=fetch,
                    rate_fetcher=lambda: {"credits_remaining": 100},
                )
            self.assertEqual(output.read_text(), "previous raw\n")
            self.assertEqual(json.loads(manifest.read_text()), {"previous": True})
            self.assertFalse((base / "works.jsonl.tmp").exists())

    def test_budget_metadata_excludes_api_key(self):
        parsed = extractor.parse_rate_limit(
            {"api_key": "masked", "rate_limit": {"credits_remaining": 600, "daily_remaining_usd": 0.06}}
        )
        self.assertEqual(parsed["credits_remaining"], 600)
        self.assertNotIn("api_key", parsed)
        extractor.ensure_budget(parsed, 500)
        with self.assertRaisesRegex(extractor.ExtractionError, "Insufficient"):
            extractor.ensure_budget(parsed, 601)

    def test_retry_429_5xx_and_timeout(self):
        for failure, counter in (
            (HTTPError("url", 429, "rate", {"Retry-After": "0"}, io.BytesIO()), "responses_429"),
            (HTTPError("url", 503, "server", {}, io.BytesIO()), "responses_5xx"),
            (URLError(TimeoutError("timed out")), "timeouts"),
            (TimeoutError("timed out"), "timeouts"),
        ):
            calls = [failure, Response({"results": [], "meta": {}})]

            def opener(_request, timeout):
                self.assertEqual(timeout, extractor.REQUEST_TIMEOUT_SECONDS)
                outcome = calls.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            stats = extractor.new_api_stats()
            extractor.fetch_json({}, opener=opener, sleep=lambda _: None, stats=stats)
            self.assertEqual(stats[counter], 1)
            self.assertEqual(stats["retries"], 1)


if __name__ == "__main__":
    unittest.main()
