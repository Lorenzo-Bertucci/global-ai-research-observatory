import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extraction"))
import extract_openalex as extractor  # noqa: E402


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


SELECTED = {"id": "https://openalex.org/T10028", "display_name": "Topic Modeling"}
OTHER = {"id": "https://openalex.org/T99999", "display_name": "Unrelated"}


def work(work_id="W1", topics=None, year=2022, **extra):
    topics = [SELECTED] if topics is None else topics
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
    def test_config_and_filter_are_frozen(self):
        self.assertEqual(extractor.TOPIC_CONFIG["status"], "frozen")
        self.assertEqual(extractor.TOPIC_CONFIG["version"], "1.1")
        self.assertEqual(extractor.AI_TOPIC_SET_FINAL, {"T10028", "T11714"})
        value = extractor.build_filter()
        self.assertIn("topics.id:T10028|T11714", value)
        self.assertIn("from_publication_date:2018-01-01", value)
        self.assertIn("to_publication_date:2025-12-31", value)
        self.assertIn("is_retracted:false", value)
        self.assertNotIn("keywords", value)
        self.assertNotIn("subfield", value)
        self.assertNotIn("search", value)

    def test_population_group_parsing(self):
        payload = {
            "meta": {"count": 30},
            "group_by": [
                {"key": "2018", "key_display_name": "2018", "count": 10},
                {"key": "2019", "key_display_name": "2019", "count": 20},
            ],
        }
        total, counts = extractor.parse_population(payload)
        self.assertEqual(total, 30)
        self.assertEqual(counts[2018], 10)
        self.assertEqual(counts[2025], 0)
        with self.assertRaises(ValueError):
            extractor.parse_population({**payload, "meta": {"count": 31}})

    def test_largest_remainder_allocation(self):
        self.assertEqual(
            extractor.largest_remainder_allocation({2018: 100, 2019: 200, 2020: 700}, 100),
            {2018: 10, 2019: 20, 2020: 70},
        )
        allocation = extractor.largest_remainder_allocation(
            {2018: 1, 2019: 1, 2020: 1}, 2
        )
        self.assertEqual(allocation, {2018: 1, 2019: 1, 2020: 0})
        self.assertEqual(sum(allocation.values()), 2)

    def test_sampling_metadata_and_determinism(self):
        population = {2018: 100, 2019: 200, 2020: 700}
        first = extractor.build_sampling_plan(population, 100)
        second = extractor.build_sampling_plan(population, 100)
        self.assertEqual(first, second)
        self.assertEqual(first[2018]["sampling_probability"], 0.1)
        self.assertEqual(first[2018]["sampling_weight"], 10.0)
        self.assertEqual(first[2018]["seed"], 2026090618)

    def test_guardrail(self):
        self.assertIsNone(extractor.validate_work(work()))
        self.assertEqual(extractor.validate_work(work(topics=[])), "missing_topics")
        self.assertEqual(extractor.validate_work(work(topics=[OTHER])), "selected_topic")
        self.assertEqual(extractor.validate_work(work(year=2019), expected_year=2020), "year_scope")
        self.assertEqual(extractor.validate_work(work(type="dataset")), "type_scope")

    def test_sample_paging(self):
        calls = []
        payloads = {
            1: {"meta": {"count": 3}, "results": [work("W1"), work("W2")]},
            2: {"meta": {"count": 3}, "results": [work("W3")]},
        }

        def fetcher(params):
            calls.append(params)
            return payloads[params["page"]]

        rows = extractor.fetch_year_sample(2022, 3, 77, fetcher)
        self.assertEqual(len(rows), 3)
        self.assertEqual([call["page"] for call in calls], [1, 2])
        self.assertTrue(all(call["sample"] == 3 for call in calls))

    def test_deduplication_and_optional_fields(self):
        plan = {
            2022: {
                "population": 10,
                "population_share": 1.0,
                "target_sample": 2,
                "sampling_probability": 0.2,
                "sampling_weight": 5.0,
                "seed": 77,
            }
        }
        calls = 0

        def fetcher(_params):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"meta": {"count": 2}, "results": [work("W1"), work("W1")]}
            rows = [work("W1"), work("W2")] + [work("W1") for _ in range(8)]
            return {"meta": {"count": 10}, "results": rows}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "works.jsonl.tmp"
            stats, quality = extractor.collect_sample(output, plan, fetcher)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual([extractor.short_id(row["id"]) for row in rows], ["W1", "W2"])
        self.assertEqual(stats["duplicates_removed"], 2)
        self.assertEqual(stats["refill_attempts"], 1)
        self.assertEqual(quality.missing_doi, 2)
        self.assertEqual(quality.missing_source, 2)

    def test_retry_429_5xx_and_timeout(self):
        for failure, counter in (
            (HTTPError("url", 429, "rate", {"Retry-After": "0"}, io.BytesIO(b"")), "responses_429"),
            (HTTPError("url", 503, "server", {}, io.BytesIO(b"")), "responses_5xx"),
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
            payload = extractor.fetch_json(
                {}, opener=opener, sleep=lambda _seconds: None, stats=stats
            )
            self.assertEqual(payload["results"], [])
            self.assertEqual(stats[counter], 1)
            self.assertEqual(stats["retries"], 1)

    def test_atomic_cleanup_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            finals = [base / name for name in ("population.json", "works.jsonl", "manifest.json", "quality.json")]

            def failing_fetcher(_params):
                raise RuntimeError("stop")

            with self.assertRaises(RuntimeError):
                extractor.run_extraction(
                    *finals,
                    fetcher=failing_fetcher,
                    request_stats=extractor.new_api_stats(),
                )
            self.assertFalse(any(path.exists() for path in finals))
            self.assertFalse(any(path.with_name(path.name + ".tmp").exists() for path in finals))

    def test_final_live_artifacts_exist_and_are_consistent(self):
        root = Path(__file__).resolve().parents[1]
        population_path = root / "data/raw/openalex_ai_population.json"
        works_path = root / "data/raw/openalex_ai_works.jsonl"
        manifest_path = root / "data/raw/openalex_ai_manifest.json"
        quality_path = root / "validation/results/openalex_data_quality.json"
        self.assertTrue(all(path.exists() for path in (population_path, works_path, manifest_path, quality_path)))
        population = json.loads(population_path.read_text())
        manifest = json.loads(manifest_path.read_text())
        quality = json.loads(quality_path.read_text())
        with works_path.open(encoding="utf-8") as source:
            works = [json.loads(line) for line in source]
        work_ids = [extractor.short_id(work["id"]) for work in works]
        actual_by_year = {
            str(year): sum(work["publication_year"] == year for work in works)
            for year in extractor.YEARS
        }
        self.assertEqual(population["population_total"], 283_851)
        self.assertEqual(population["population_total"], sum(population["population_by_year"].values()))
        self.assertEqual(manifest["population_total"], population["population_total"])
        self.assertEqual(quality["population_total"], population["population_total"])
        self.assertEqual(len(work_ids), extractor.TARGET_SAMPLE_SIZE)
        self.assertEqual(len(set(work_ids)), extractor.TARGET_SAMPLE_SIZE)
        self.assertEqual(manifest["actual_sample_size"], extractor.TARGET_SAMPLE_SIZE)
        self.assertEqual(manifest["actual_by_year"], actual_by_year)
        self.assertEqual(quality["actual_by_year"], actual_by_year)
        self.assertEqual(
            manifest["missing_by_field"],
            {
                "doi": quality["missing_doi"],
                "primary_topic": quality["missing_primary_topic"],
                "country": quality["missing_country"],
                "institution": quality["missing_institution"],
                "source": quality["missing_source"],
            },
        )
        self.assertEqual(quality["unique_work_ids"], extractor.TARGET_SAMPLE_SIZE)
        self.assertEqual(quality["duplicates"], 0)
        self.assertEqual(quality["guardrail_failures"], 0)
        self.assertTrue(quality["sanity_sample"]["passed"])


if __name__ == "__main__":
    unittest.main()
