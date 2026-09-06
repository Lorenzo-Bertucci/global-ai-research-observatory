import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extraction"))
import extract_world_bank as extractor  # noqa: E402


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def country(
    iso3="ITA",
    iso2="IT",
    name="Italy",
    region_id="ECS",
    region_name="Europe & Central Asia",
):
    return {
        "id": iso3,
        "iso2Code": iso2,
        "name": name,
        "region": {"id": region_id, "value": region_name},
        "incomeLevel": {"id": "HIC", "value": "High income"},
    }


def observation(code, value, year=2022, iso3="ITA"):
    return {
        "indicator": {"id": code, "value": code},
        "country": {"id": "IT", "value": "Italy"},
        "countryiso3code": iso3,
        "date": str(year),
        "value": value,
    }


class WorldBankExtractionTests(unittest.TestCase):
    def test_api_v2_list_response_parsing(self):
        metadata, rows = extractor.parse_list_response(
            [{"page": 1, "pages": 1, "per_page": "50", "total": 1}, [{"id": "ITA"}]]
        )
        self.assertEqual(metadata["page"], 1)
        self.assertEqual(rows, [{"id": "ITA"}])
        with self.assertRaises(ValueError):
            extractor.parse_list_response({"not": "a list response"})

    def test_pagination_reads_every_declared_page(self):
        calls = []
        payloads = {
            1: [{"page": 1, "pages": 2, "per_page": "1", "total": 2}, [{"id": "ITA"}]],
            2: [{"page": 2, "pages": 2, "per_page": "1", "total": 2}, [{"id": "FRA"}]],
        }

        def fetcher(endpoint, params):
            calls.append((endpoint, dict(params)))
            return payloads[params["page"]]

        stats = extractor.new_api_stats()
        rows, metadata = extractor.fetch_paginated(
            "https://example.test/country",
            {"format": "json", "per_page": 1},
            fetcher=fetcher,
            stats=stats,
        )
        self.assertEqual([row["id"] for row in rows], ["ITA", "FRA"])
        self.assertEqual([call[1]["page"] for call in calls], [1, 2])
        self.assertEqual(metadata["total"], 2)
        self.assertEqual(stats["pages_fetched"], 2)

    def test_real_country_retained_and_aggregate_excluded(self):
        aggregate_by_id = country("WLD", "1W", "World", "NA", "Aggregates")
        aggregate_by_name = country("HIC", "XD", "High income", "", "Aggregates")
        real, aggregates = extractor.select_real_countries(
            [country(), aggregate_by_id, aggregate_by_name]
        )
        self.assertEqual([row["country_code_iso3"] for row in real], ["ITA"])
        self.assertEqual(len(aggregates), 2)
        self.assertFalse(extractor.is_aggregate_country(country()))

    def test_optional_country_metadata_does_not_crash(self):
        minimal = {"id": "XKX", "iso2Code": "XK", "name": "Kosovo"}
        real, aggregates = extractor.select_real_countries([minimal])
        self.assertEqual(aggregates, [])
        self.assertEqual(real[0]["region_id"], "")
        self.assertEqual(real[0]["income_level_name"], "")

    def test_indicator_pivot_one_row_and_null_preserved(self):
        observations = [
            observation("SP.POP.TOTL", 58997201),
            observation("NY.GDP.MKTP.CD", 2100000000000),
            observation("NY.GDP.PCAP.CD", 35600.4),
            observation("IT.NET.USER.ZS", 86.1),
            observation("GB.XPD.RSDV.GD.ZS", None),
        ]
        normalized, _ = extractor.select_real_countries([country()])
        rows, stats = extractor.pivot_observations(
            normalized, observations, years=[2022]
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["country_code_iso3"], row["year"]), ("ITA", 2022))
        self.assertEqual(row["population"], 58997201)
        self.assertEqual(row["gdp_current_usd"], 2100000000000)
        self.assertIsNone(row["rd_expenditure_pct_gdp"])
        self.assertEqual(stats["indicator_observations_applied"], 5)

    def test_complete_skeleton_unique_grain_and_year_scope(self):
        normalized, _ = extractor.select_real_countries(
            [country(), country("FRA", "FR", "France")]
        )
        rows, _ = extractor.pivot_observations(normalized, [])
        self.assertEqual(len(rows), 2 * len(extractor.YEARS))
        self.assertEqual(extractor.duplicate_country_year_count(rows), 0)
        self.assertEqual({row["year"] for row in rows}, set(range(2018, 2026)))
        self.assertTrue(all(row[field] is None for row in rows for field in extractor.MEASURE_FIELDS))
        self.assertEqual(
            [(row["country_code_iso3"], row["year"]) for row in rows],
            sorted((row["country_code_iso3"], row["year"]) for row in rows),
        )

    def test_duplicate_indicator_observation_is_rejected(self):
        normalized, _ = extractor.select_real_countries([country()])
        duplicate = observation("SP.POP.TOTL", 1)
        with self.assertRaisesRegex(ValueError, "Duplicate indicator observation"):
            extractor.pivot_observations(normalized, [duplicate, duplicate], years=[2022])

    def test_aggregate_observations_are_not_applied(self):
        normalized, _ = extractor.select_real_countries([country()])
        rows, stats = extractor.pivot_observations(
            normalized,
            [observation("SP.POP.TOTL", 8_000_000_000, iso3="WLD")],
            years=[2022],
        )
        self.assertIsNone(rows[0]["population"])
        self.assertEqual(stats["aggregate_or_non_country_observations_excluded"], 1)

    def test_indicator_metadata_is_verified_in_source_two(self):
        records = {}
        for field, code in extractor.INDICATORS.items():
            records[code] = [
                {
                    "id": code,
                    "name": f"Official {code}",
                    "unit": "",
                    "source": {"id": "2", "value": "World Development Indicators"},
                    "sourceNote": "note",
                    "sourceOrganization": "organization",
                }
            ]
        verified = extractor.verify_indicator_metadata(records)
        self.assertEqual(len(verified), 5)
        self.assertEqual(
            {item["output_field"]: item["code"] for item in verified},
            extractor.INDICATORS,
        )
        wrong = dict(records)
        wrong["SP.POP.TOTL"] = [{**records["SP.POP.TOTL"][0], "source": {"id": "11"}}]
        with self.assertRaises(ValueError):
            extractor.verify_indicator_metadata(wrong)

    def test_quality_report_flags_ranges_without_modifying_values(self):
        normalized, _ = extractor.select_real_countries([country()])
        rows, _ = extractor.pivot_observations(
            normalized,
            [
                observation("SP.POP.TOTL", -1, year=2022),
                observation("IT.NET.USER.ZS", 101, year=2022),
            ],
        )
        quality = extractor.analyze_quality(rows, normalized)
        self.assertEqual(quality["value_sanity_checks"]["invalid_range_count"], 2)
        self.assertEqual(
            quality["value_sanity_checks"]["invalid_ranges"]["population"][0]["value"],
            -1,
        )
        self.assertTrue(quality["no_missing_values_imputed"])

    def test_http_retry_and_terminal_error_handling(self):
        for failure in (
            HTTPError("url", 429, "rate", {"Retry-After": "0"}, io.BytesIO(b"")),
            HTTPError("url", 503, "server", {}, io.BytesIO(b"")),
            URLError(TimeoutError("timed out")),
            TimeoutError("timed out"),
        ):
            calls = [failure, Response([{"page": 1}, []])]
            stats = extractor.new_api_stats()

            def opener(_request, timeout):
                self.assertEqual(timeout, extractor.REQUEST_TIMEOUT_SECONDS)
                outcome = calls.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            payload = extractor.fetch_json(
                "https://example.test",
                {},
                opener=opener,
                sleep=lambda _seconds: None,
                stats=stats,
            )
            self.assertEqual(payload[1], [])
            self.assertEqual(stats["retries"], 1)
            self.assertEqual(stats["requests"], 2)

        fatal = HTTPError("url", 404, "missing", {}, io.BytesIO(b"not found"))
        stats = extractor.new_api_stats()
        with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
            extractor.fetch_json(
                "https://example.test",
                {},
                opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(fatal),
                sleep=lambda _seconds: None,
                stats=stats,
            )
        self.assertEqual(len(stats["errors"]), 1)

    def test_atomic_writers_leave_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "manifest.json"
            jsonl_path = root / "data.jsonl"
            extractor.write_json_atomic(json_path, {"ok": True})
            extractor.write_jsonl_atomic(jsonl_path, [{"row": 1}])
            self.assertEqual(json.loads(json_path.read_text())["ok"], True)
            self.assertEqual(json.loads(jsonl_path.read_text())["row"], 1)
            self.assertFalse((root / "manifest.json.tmp").exists())
            self.assertFalse((root / "data.jsonl.tmp").exists())

    def test_final_live_artifacts_exist_and_are_consistent(self):
        output = extractor.DEFAULT_OUTPUT
        manifest_path = extractor.DEFAULT_MANIFEST
        quality_path = extractor.DEFAULT_QUALITY_REPORT
        self.assertTrue(output.is_file())
        self.assertTrue(manifest_path.is_file())
        self.assertTrue(quality_path.is_file())

        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        excluded = set(manifest["excluded_aggregate_codes"])
        keys = [(row["country_code_iso3"], row["year"]) for row in rows]

        self.assertEqual(len(rows), 1_736)
        self.assertEqual(len({row["country_code_iso3"] for row in rows}), 217)
        self.assertEqual(manifest["real_countries_retained"], 217)
        self.assertEqual(quality["country_count"], 217)
        self.assertEqual({row["year"] for row in rows}, set(extractor.YEARS))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(row["country_code_iso3"] for row in rows))
        self.assertTrue(all(row["year"] is not None for row in rows))
        self.assertFalse({row["country_code_iso3"] for row in rows} & excluded)
        self.assertEqual(len(rows), manifest["actual_country_year_rows"])
        self.assertEqual(len(rows), quality["row_count"])
        self.assertEqual(manifest["duplicates"], 0)
        self.assertEqual(quality["duplicate_country_year_count"], 0)
        self.assertTrue(manifest["no_missing_values_imputed"])
        self.assertTrue(quality["no_missing_values_imputed"])


if __name__ == "__main__":
    unittest.main()
