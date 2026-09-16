import importlib.util
import os
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard import queries  # noqa: E402
from dashboard import ui  # noqa: E402
from dashboard import views  # noqa: E402
from tests import test_load_dw as dw_tests  # noqa: E402


class DashboardQueryTests(unittest.TestCase):
    def test_dashboard_keeps_complete_flat_navigation(self):
        self.assertEqual(
            views.PAGES,
            (
                "Overview",
                "Research Growth",
                "Geographic Leadership",
                "Normalized Leadership",
                "Socioeconomic Context",
                "Topics",
                "Institutions",
                "Publication Ecosystem",
                "Citation Impact",
            ),
        )
        source = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("Core Analysis", source)
        self.assertNotIn("Additional Perspectives", source)

    def test_removed_global_copy_and_overview_footer_stay_absent(self):
        app_source = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
        view_source = (ROOT / "dashboard" / "views.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(
            "OpenAlex Primary Topic in the Artificial Intelligence subfield",
            app_source,
        )
        self.assertNotIn("Reproducible 50,000-work stratified sample", app_source)
        self.assertNotIn(
            "Dashboard results reflect the dataset currently loaded in the warehouse",
            view_source,
        )

    def test_native_theme_is_the_only_dashboard_theme_control(self):
        config = tomllib.loads(
            (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(config["theme"]["base"], "light")
        source = "\n".join(
            (ROOT / "dashboard" / name).read_text(encoding="utf-8")
            for name in ("app.py", "ui.py", "views.py")
        )
        self.assertNotIn("appearance_mode", source)
        self.assertNotIn('st.sidebar.expander("Appearance")', source)

    def test_plotly_style_does_not_override_native_theme_colors(self):
        import plotly.graph_objects as go

        figure = ui.apply_figure_style(go.Figure())
        self.assertIsNone(figure.layout.font.color)
        self.assertIsNone(figure.layout.title.font.color)
        self.assertIsNone(figure.layout.xaxis.tickfont.color)
        self.assertIsNone(figure.layout.yaxis.title.font.color)
        self.assertEqual(figure.layout.paper_bgcolor, "rgba(0,0,0,0)")

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
            open_access_values=("true",),
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
            "true",
        ):
            self.assertNotIn(user_value, specification.sql)
        self.assertIn(["IT", "TW"], specification.params)
        self.assertIn(["https://openalex.org/T1"], specification.params)
        self.assertIn([True], specification.params)

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

    def test_open_access_filter_uses_nullable_boolean_semantics(self):
        specification = queries.filtered_publications_cte(
            queries.FilterState(
                open_access_values=("false", queries.MISSING_VALUE)
            )
        )
        self.assertIn(
            "(publication.is_open_access = ANY(%s) OR publication.is_open_access IS NULL)",
            specification.sql,
        )
        self.assertIn([False], specification.params)
        with self.assertRaisesRegex(ValueError, "Unsupported Open Access"):
            queries.filtered_publications_cte(
                queries.FilterState(open_access_values=("gold",))
            )

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
            "indicator.year = publication.calendar_year", specification.sql
        )
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
        self.assertNotIn("dim_" + "year", specification.sql)
        self.assertNotIn("year_" + "key", specification.sql)

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
        source_full = queries.citation_ranking(
            queries.FilterState(), "Source type", "Full", 10
        )
        source_fractional = queries.citation_ranking(
            queries.FilterState(), "Source type", "Fractional", 10
        )
        self.assertEqual(source_full, source_fractional)

    def test_topic_trends_support_both_attribution_methods(self):
        full = queries.topic_evolution(
            queries.FilterState(), "Full", "Topic", 8
        )
        fractional = queries.topic_evolution(
            queries.FilterState(), "Fractional", "Topic", 8
        )
        self.assertIn("1::numeric AS attributed_publications", full.sql)
        self.assertIn(
            "sum(bridge.fractional_weight) AS attributed_publications",
            fractional.sql,
        )
        self.assertEqual(full.params[-1], 8)
        self.assertEqual(fractional.params[-1], 8)

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

    def test_presentation_helpers_preserve_analytical_semantics(self):
        import pandas as pd

        countries = pd.DataFrame(
            {"full_publications": [9, 20, 31], "country": ["A", "B", "C"]}
        )
        supported = views.apply_publication_support(countries, 20)
        self.assertEqual(supported["country"].tolist(), ["B", "C"])

        evolution = pd.DataFrame(
            {
                "calendar_year": [2024, 2024],
                "member_name": ["Topic A", "Topic B"],
                "publications": [25.0, 10.0],
            }
        )
        annual = pd.DataFrame(
            {"calendar_year": [2024], "publications": [100.0]}
        )
        shares = views.topic_annual_shares(evolution, annual)
        self.assertEqual(shares["annual_share"].tolist(), [25.0, 10.0])

        sources = pd.DataFrame(
            {
                "source_id": [queries.MISSING_VALUE, "S1", "S2"],
                "publications": [50, 40, 30],
            }
        )
        ranking = views.source_ranking(
            sources, include_missing=False, top_n=2
        )
        self.assertEqual(ranking["source_id"].tolist(), ["S1", "S2"])
        self.assertEqual(len(sources), 3)


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
            methodName="test_schema_degenerate_year_coordinate_and_publication_grain"
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
            open_access_values=("true",),
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

        access_rows = self._rows(
            queries.open_access_by_year(
                queries.FilterState(start_year=2022, end_year=2023)
            )
        )
        self.assertEqual(
            {(row[0], row[1]) for row in access_rows},
            {
                (2022, "Open Access"),
                (2022, "Not Open Access"),
                (2023, queries.MISSING_LABEL),
            },
        )

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
            self.assertEqual(len(application.metric), 6)
            self.assertGreaterEqual(len(application.get("plotly_chart")), 4)
            self.assertEqual(
                {expander.label for expander in application.sidebar.expander},
                {"Geographic filters", "Thematic filters", "Additional filters"},
            )
            navigation = next(
                selector
                for selector in application.sidebar.selectbox
                if selector.label == "Page"
            )
            self.assertEqual(tuple(navigation.options), views.PAGES)
            self.assertFalse(
                any(
                    radio.label == "Appearance"
                    for radio in application.sidebar.radio
                )
            )
            self.assertFalse(
                any(button.label == "Refresh data" for button in application.sidebar.button)
            )
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
                    next(
                        selector
                        for selector in application.sidebar.selectbox
                        if selector.label == "Page"
                    ).set_value(page)
                    application.run()
                    self.assertEqual(list(application.exception), [])
                    if page not in {"Research Growth"}:
                        self.assertGreaterEqual(len(application.download_button), 1)
                    if page == "Geographic Leadership":
                        analysis_year = next(
                            selector
                            for selector in application.selectbox
                            if selector.label == "Analysis year"
                        )
                        self.assertEqual(analysis_year.value, 2023)
                        comparison = next(
                            toggle
                            for toggle in application.toggle
                            if toggle.label == "Compare attribution methods"
                        )
                        comparison.set_value(True).run()
                        self.assertEqual(list(application.exception), [])
                        self.assertGreaterEqual(len(application.get("plotly_chart")), 3)
                    if page == "Normalized Leadership":
                        analysis_year = next(
                            selector
                            for selector in application.selectbox
                            if selector.label == "Analysis year"
                        )
                        self.assertEqual(analysis_year.value, 2023)
                        support = next(
                            selector
                            for selector in application.selectbox
                            if selector.label
                            == "Minimum full publication participation"
                        )
                        self.assertEqual(support.value, "≥ 30")
                    if page == "Socioeconomic Context":
                        path = next(
                            radio
                            for radio in application.radio
                            if radio.label == "Analytical path"
                        )
                        self.assertEqual(path.value, "Income-level gap")
                    if page == "Topics":
                        measure = next(
                            selector
                            for selector in application.selectbox
                            if selector.label == "Trend measure"
                        )
                        self.assertEqual(
                            measure.value, "Share of annual publications"
                        )
                        top_n = next(
                            slider
                            for slider in application.select_slider
                            if slider.label == "Top N"
                        )
                        self.assertEqual(top_n.value, 8)
                    if page == "Publication Ecosystem":
                        missing_toggle = next(
                            toggle
                            for toggle in application.toggle
                            if toggle.label == "Include missing source in ranking"
                        )
                        self.assertFalse(missing_toggle.value)
            next(
                selector
                for selector in application.sidebar.selectbox
                if selector.label == "Page"
            ).set_value("Socioeconomic Context").run()
            for path in (
                "Income-level gap",
                "Wealth",
                "R&D investment",
                "Digital access",
                "Regional capacity",
                "Changes over time",
            ):
                next(r for r in application.radio if r.label == "Analytical path").set_value(path).run()
                self.assertEqual(list(application.exception), [])
            next(
                selector
                for selector in application.sidebar.selectbox
                if selector.label == "Page"
            ).set_value("Research Growth").run()
            supporting = next(
                expander
                for expander in application.expander
                if expander.label == "Supporting trends"
            )
            self.assertFalse(supporting.proto.expanded)
            for grain in ("Quarter", "Month"):
                next(r for r in application.radio if r.label == "Time detail").set_value(grain).run()
                self.assertEqual(list(application.exception), [])
            year_filter = next(
                slider
                for slider in application.sidebar.slider
                if slider.label == "Publication year range"
            )
            year_filter.set_range(2022, 2022).run()
            next(m for m in application.sidebar.multiselect if m.label == "Country").set_value(["IT"])
            next(m for m in application.sidebar.multiselect if m.label == "Publication type").set_value(["preprint"])
            application.run()
            self.assertEqual(list(application.exception), [])
            next(
                selector
                for selector in application.sidebar.selectbox
                if selector.label == "Page"
            ).set_value("Topics").run()
            self.assertEqual(
                next(
                    slider
                    for slider in application.sidebar.slider
                    if slider.label == "Publication year range"
                ).value,
                (2022, 2022),
            )
            next(
                button
                for button in application.sidebar.button
                if button.label == "Reset filters"
            ).click().run()
            self.assertTrue(
                all(not widget.value for widget in application.sidebar.multiselect)
            )
            self.assertEqual(
                application.session_state["filter_year_range"],
                (2022, 2023),
            )
            counting = next(
                radio
                for radio in application.sidebar.radio
                if radio.label == "Attribution"
            )
            self.assertEqual(counting.value, "Full")
            counting.set_value("Fractional").run()
            for page in (
                "Geographic Leadership",
                "Normalized Leadership",
                "Topics",
                "Institutions",
                "Citation Impact",
            ):
                next(
                    selector
                    for selector in application.sidebar.selectbox
                    if selector.label == "Page"
                ).set_value(page).run()
                self.assertEqual(list(application.exception), [])
                if page == "Normalized Leadership":
                    self.assertTrue(
                        any(
                            "Metric attribution: Fractional" in caption.value
                            for caption in application.caption
                        )
                    )
if __name__ == "__main__":
    unittest.main()
