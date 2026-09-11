import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard import queries  # noqa: E402
from tests import test_load_dw as dw_tests  # noqa: E402


class DashboardQueryTests(unittest.TestCase):
    def comprehensive_filters(self):
        return queries.FilterState(
            start_year=2020,
            end_year=2024,
            countries=("IT", "TW"),
            regions=("Europe & Central Asia",),
            income_levels=("High income",),
            topics=("https://openalex.org/T1",),
            subfields=("https://openalex.org/subfields/1702",),
            institution_types=("education",),
            source_types=("journal",),
            publication_types=("article",),
            languages=("en",),
            open_access_statuses=("gold",),
        )

    def test_global_bridge_filters_are_independent_exists_semijoins(self):
        specification = queries.filtered_publications_cte(
            self.comprehensive_filters()
        )
        self.assertEqual(specification.sql.count("EXISTS ("), 3)
        self.assertEqual(
            specification.sql.count("FROM dw.bridge_publication_country"), 1
        )
        self.assertEqual(
            specification.sql.count("FROM dw.bridge_publication_topic"), 1
        )
        self.assertEqual(
            specification.sql.count("FROM dw.bridge_publication_institution"), 1
        )
        for user_value in (
            "Europe & Central Asia",
            "High income",
            "https://openalex.org/T1",
            "education",
            "journal",
            "article",
            "gold",
        ):
            self.assertNotIn(user_value, specification.sql)
        self.assertIn(["IT", "TW"], specification.params)
        self.assertIn(["https://openalex.org/T1"], specification.params)

    def test_nullable_filter_can_select_missing_and_concrete_values(self):
        specification = queries.filtered_publications_cte(
            queries.FilterState(
                source_types=(queries.MISSING_VALUE, "journal"),
                publication_types=(queries.MISSING_VALUE,),
            )
        )
        self.assertIn(
            "(source.source_type = ANY(%s) OR source.source_type IS NULL)",
            specification.sql,
        )
        self.assertIn("publication.publication_type IS NULL", specification.sql)
        self.assertIn(["journal"], specification.params)

    def test_full_and_fractional_country_counting_use_declared_measure(self):
        full = queries.country_output(queries.FilterState(), "Full", 10)
        fractional = queries.country_output(
            queries.FilterState(), "Fractional", 10
        )
        self.assertIn("sum(selected.publication_count)::numeric", full.sql)
        self.assertNotIn(
            "publication_count * bridge.fractional_weight", full.sql
        )
        self.assertIn(
            "sum(selected.publication_count * bridge.fractional_weight)::numeric",
            fractional.sql,
        )
        self.assertEqual(full.params[-1], 10)
        self.assertEqual(fractional.params[-1], 10)

    def test_analytical_query_joins_only_its_attribution_bridge(self):
        specification = queries.country_output(
            self.comprehensive_filters(), "Fractional", 20
        )
        outer_query = specification.sql.rsplit(
            "FROM filtered_publications selected", 1
        )[1]
        self.assertIn("JOIN dw.bridge_publication_country bridge", outer_query)
        self.assertNotIn("JOIN dw.bridge_publication_topic", outer_query)
        self.assertNotIn("JOIN dw.bridge_publication_institution", outer_query)

    def test_normalization_is_fractional_preaggregated_and_null_safe(self):
        specification = queries.normalized_country_year(
            queries.FilterState(countries=("RE", "TW"))
        )
        self.assertIn("publication_country_year AS", specification.sql)
        self.assertIn(
            "sum(selected.publication_count * bridge.fractional_weight)::numeric",
            specification.sql,
        )
        self.assertIn("LEFT JOIN dw.fact_country_year indicator", specification.sql)
        self.assertIn(
            "indicator.population IS NULL OR indicator.population = 0",
            specification.sql,
        )
        self.assertIn(
            "indicator.gdp_current_usd IS NULL OR indicator.gdp_current_usd = 0",
            specification.sql,
        )
        self.assertNotIn("coalesce(indicator.population", specification.sql.lower())
        self.assertNotIn("coalesce(indicator.gdp_current_usd", specification.sql.lower())
        self.assertIn(["RE", "TW"], specification.params)
        self.assertNotIn("'FR'", specification.sql)
        self.assertNotIn("'CN'", specification.sql)

    def test_topic_level_and_citation_dimension_are_strictly_whitelisted(self):
        with self.assertRaisesRegex(ValueError, "Unsupported topic hierarchy"):
            queries.topic_output(
                queries.FilterState(), "Full", "topic_name; DROP SCHEMA dw", 10
            )
        with self.assertRaisesRegex(ValueError, "Unsupported citation dimension"):
            queries.citation_ranking(
                queries.FilterState(), "arbitrary SQL", "Full", 10
            )

    def test_single_valued_dimensions_ignore_attribution_selector(self):
        full = queries.citation_ranking(
            queries.FilterState(), "Publication type", "Full", 10
        )
        fractional = queries.citation_ranking(
            queries.FilterState(), "Publication type", "Fractional", 10
        )
        self.assertEqual(full, fractional)
        self.assertNotIn("fractional_weight", full.sql)

    def test_citation_bridge_attribution_matches_counting_method(self):
        full = queries.citation_ranking(
            queries.FilterState(), "Topic", "Full", 10
        )
        fractional = queries.citation_ranking(
            queries.FilterState(), "Topic", "Fractional", 10
        )
        self.assertIn("sum(selected.citation_count)::numeric", full.sql)
        self.assertIn(
            "sum(selected.citation_count * bridge.fractional_weight)::numeric",
            fractional.sql,
        )


@unittest.skipIf(dw_tests.psycopg is None, "psycopg is required for PostgreSQL tests")
class DashboardDatabaseQueryTests(unittest.TestCase):
    """Execute dashboard SQL against the existing RE/TW warehouse fixture."""

    @classmethod
    def setUpClass(cls):
        # Reuse the project's isolated disposable database lifecycle and fixture
        # without inheriting its tests or creating a second fixture definition.
        dw_tests.WarehouseLoadTests.setUpClass()
        cls.database_url = dw_tests.WarehouseLoadTests.database_url

    @classmethod
    def tearDownClass(cls):
        dw_tests.WarehouseLoadTests.tearDownClass()

    def setUp(self):
        self.fixture = dw_tests.WarehouseLoadTests(
            methodName="test_schema_surrogate_keys_date_and_publication_grain"
        )
        self.fixture.setUp()
        dw_tests.warehouse.load_dw(self.database_url)

    def tearDown(self):
        self.fixture.tearDown()

    def _rows(self, specification):
        with dw_tests.psycopg.connect(self.database_url) as connection:
            return connection.execute(
                specification.sql, specification.params
            ).fetchall()

    def test_all_dashboard_query_families_execute_at_their_intended_grain(self):
        filtered = queries.FilterState(
            start_year=2022,
            end_year=2023,
            countries=("IT",),
            topics=("https://openalex.org/T1",),
            institution_types=("education",),
            source_types=("journal",),
            publication_types=("article",),
            languages=("en",),
            open_access_statuses=("gold",),
        )
        specifications = [
            queries.warehouse_check(),
            queries.year_bounds(),
            queries.filter_options(),
            queries.overview_metrics(filtered),
            queries.publications_by_year(filtered),
            queries.open_access_by_year(filtered),
            queries.publication_type_by_year(filtered),
            queries.country_output(filtered, "Full", 10),
            queries.country_output(filtered, "Fractional", 10),
            queries.regional_output(filtered, "Fractional"),
            queries.normalized_country_year(filtered),
            queries.topic_output(filtered, "Full", "Topic", 10),
            queries.topic_evolution(filtered, "Fractional", "Subfield", 5),
            queries.institution_output(filtered, "Full", 10),
            queries.institution_type_output(filtered, "Fractional"),
            queries.institution_evolution(
                filtered, "Full", ("https://openalex.org/I-IT",)
            ),
            queries.source_output(filtered, 10),
            queries.source_type_output(filtered),
            queries.publication_type_output(filtered),
            queries.citation_distribution(filtered),
        ]
        specifications.extend(
            queries.citation_ranking(filtered, dimension, "Fractional", 10)
            for dimension in (
                "Country",
                "Topic",
                "Institution",
                "Publication type",
                "Source type",
            )
        )
        results = [self._rows(specification) for specification in specifications]
        self.assertTrue(all(rows for rows in results))

        overview = self._rows(queries.overview_metrics(filtered))[0]
        self.assertEqual(overview[0], 1)
        italy = self._rows(queries.country_output(filtered, "Fractional", 10))
        self.assertEqual(len(italy), 1)
        self.assertEqual(italy[0][0], "IT")
        self.assertAlmostEqual(float(italy[0][-1]), 0.5)

        normalized = self._rows(
            queries.normalized_country_year(
                queries.FilterState(start_year=2022, end_year=2023)
            )
        )
        by_code = {row[0]: row for row in normalized}
        for code in ("RE", "TW"):
            self.assertFalse(by_code[code][5])
            self.assertIsNone(by_code[code][8])
            self.assertIsNone(by_code[code][9])
            self.assertIsNone(by_code[code][13])
            self.assertIsNone(by_code[code][14])

    def test_rollups_count_each_publication_once_per_parent(self):
        f = queries.FilterState(start_year=2022, end_year=2023)
        full = self._rows(queries.topic_output(f, "Full", "Subfield"))
        fractional = self._rows(queries.topic_output(f, "Fractional", "Subfield"))
        self.assertEqual(float(full[0][-1]), 3)
        self.assertEqual(float(fractional[0][-1]), 3)
        types = self._rows(queries.institution_type_output(f, "Full"))
        self.assertEqual(float(types[0][-1]), 3)
        filtered = queries.FilterState(countries=("IT",), topics=("https://openalex.org/T1",), institution_types=("education",))
        self.assertEqual(float(self._rows(queries.country_output(filtered, "Full"))[0][-1]), 1)
        self.assertEqual(float(self._rows(queries.country_output(filtered, "Fractional"))[0][-1]), .5)

    def test_group_capacity_uses_matched_country_year_denominators(self):
        spec = queries.country_group_capacity(queries.FilterState(start_year=2023, end_year=2023), "Region")
        rows = self._rows(spec)
        europe = next(r for r in rows if r[0] == "Europe & Central Asia")
        # Italy has no 2023 publications but its population belongs in the region denominator.
        self.assertEqual(float(europe[4]), 143000000)
        self.assertAlmostEqual(float(europe[6]), .5 * 1000000 / 143000000)
        missing = next(r for r in rows if r[0] == queries.MISSING_LABEL)
        self.assertIsNone(missing[6])
        self.assertIsNone(missing[7])
        filtered = self._rows(queries.country_group_capacity(queries.FilterState(start_year=2022, end_year=2023, countries=("IT",), topics=("https://openalex.org/T1",)), "Income level"))
        self.assertEqual(len(filtered), 2)

    def test_zero_denominators_and_missing_ranks(self):
        with dw_tests.psycopg.connect(self.database_url) as connection:
            connection.execute("UPDATE dw.fact_country_year SET population=0, gdp_current_usd=0 WHERE country_key=(SELECT country_key FROM dw.dim_country WHERE country_code_iso2='IT')")
        rows = self._rows(queries.leadership_ranks(queries.FilterState()))
        italy = next(r for r in rows if r[0] == "IT")
        self.assertIsNone(italy[13])
        self.assertIsNone(italy[14])
        self.assertIsNone(italy[17])
        self.assertIsNone(italy[18])

    def test_final_sessions_execute_and_temporal_empty_state(self):
        from analysis.sessions import sessions
        for session in sessions(queries.FilterState(start_year=2022, end_year=2023)):
            for _, spec in session.steps:
                with self.subTest(session=session.number):
                    self._rows(spec)
        self.assertEqual(self._rows(queries.temporal_navigation(queries.FilterState(countries=("ZZ",)), "Quarter")), [])
        with self.assertRaises(ValueError):
            queries.temporal_navigation(queries.FilterState(), "unsafe")

    def test_launcher_rejects_incomplete_year_coverage(self):
        from run_dashboard import PreflightError, preflight
        with self.assertRaisesRegex(PreflightError, "2018"):
            preflight(self.database_url)

    @unittest.skipIf(
        importlib.util.find_spec("streamlit") is None,
        "streamlit is required for the UI smoke test",
    )
    def test_streamlit_pages_render_without_runtime_exceptions(self):
        from streamlit.testing.v1 import AppTest

        with mock.patch.dict(
            os.environ, {"DATABASE_URL": self.database_url}, clear=False
        ):
            application = AppTest.from_file(
                str(ROOT / "dashboard" / "app.py"), default_timeout=20
            ).run()
            self.assertEqual(list(application.exception), [])
            self.assertTrue(any(title.value for title in application.title))
            self.assertGreaterEqual(len(application.metric), 5)
            for page in (
                "Research Growth",
                "Geographic Leadership",
                "Normalized Leadership",
                "Socioeconomic Context",
                "Topics",
                "Institutions",
                "Publication Ecosystem",
                "Citation Impact",
            ):
                with self.subTest(page=page):
                    application.sidebar.radio[0].set_value(page)
                    application.run()
                    self.assertEqual(list(application.exception), [])
            application.sidebar.radio[0].set_value("Socioeconomic Context").run()
            for path in ("Income-level gap", "Regional capacity", "Changes over time", "Country indicators"):
                next(r for r in application.radio if r.label == "Analytical path").set_value(path).run()
                self.assertEqual(list(application.exception), [])
            application.sidebar.radio[0].set_value("Research Growth").run()
            for grain in ("Quarter", "Month"):
                next(r for r in application.radio if r.label == "Time detail").set_value(grain).run()
                self.assertEqual(list(application.exception), [])
            next(m for m in application.sidebar.multiselect if m.label == "Country").set_value(["IT"])
            next(m for m in application.sidebar.multiselect if m.label == "Publication type").set_value(["preprint"])
            application.run()
            self.assertEqual(list(application.exception), [])


if __name__ == "__main__":
    unittest.main()
