"""Streamlit entry point for the Global AI Research Observatory."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from dashboard import queries  # noqa: E402
from dashboard.db import DashboardDatabaseError  # noqa: E402
from dashboard.views import (  # noqa: E402
    PAGES,
    build_filters,
    configure_page,
    render_citations,
    render_countries,
    render_evolution,
    render_institutions,
    render_normalized,
    render_socioeconomic,
    render_overview,
    render_sources,
    render_topics,
    run,
)


def main() -> None:
    configure_page()
    st.sidebar.markdown("## Observatory")
    page = st.sidebar.radio("Analysis", PAGES, label_visibility="collapsed")

    check = run(queries.warehouse_check())
    if check.empty or not bool(check.iloc[0].get("publication_ready")) or not bool(
        check.iloc[0].get("country_year_ready")
    ):
        st.error(
            "The `dw` warehouse is not loaded. Run the reconciliation and warehouse "
            "loaders before starting the dashboard."
        )
        st.stop()
    bounds = run(queries.year_bounds())
    if bounds.empty or pd.isna(bounds.iloc[0]["min_year"]):
        st.error("The warehouse contains no publication dates to explore.")
        st.stop()
    minimum_year = int(bounds.iloc[0]["min_year"])
    maximum_year = int(bounds.iloc[0]["max_year"])
    options = run(queries.filter_options())
    filters, counting_method, active_filters = build_filters(
        options, minimum_year, maximum_year
    )

    st.title("Global AI Research Observatory")
    st.caption("Corpus: OpenAlex Primary Topic in the Artificial Intelligence subfield · All associated topics retained.")
    manifest_path = ROOT / "data/raw/openalex_ai_manifest.json"
    if manifest_path.exists():
        import json
        try:
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("actual_total"):
                st.info(
                    "Results are based on a reproducible 50,000-work stratified "
                    "sample of OpenAlex publications whose Primary Topic belongs "
                    "to the Artificial Intelligence subfield.",
                    icon="ℹ️",
                )
            else:
                st.warning(
                    "The local raw generation predates the final direct-sample "
                    "manifest. Rebuild and reload before interpreting results."
                )
        except (ValueError, TypeError, OSError):
            st.warning("Corpus completeness metadata is unavailable.")
    st.markdown(
        '<div class="observatory-subtitle">Interactive Data Warehouse Explorer</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="context-line">{filters.start_year}–{filters.end_year} · '
        f'{active_filters} categorical filter(s) · {counting_method} counting</div>',
        unsafe_allow_html=True,
    )

    renderers = {
        "Overview": lambda: render_overview(filters, counting_method),
        "Research Growth": lambda: render_evolution(filters),
        "Geographic Leadership": lambda: render_countries(
            filters, counting_method
        ),
        "Normalized Leadership": lambda: render_normalized(
            filters, counting_method
        ),
        "Socioeconomic Context": lambda: render_socioeconomic(filters),
        "Topics": lambda: render_topics(filters, counting_method),
        "Institutions": lambda: render_institutions(filters, counting_method),
        "Publication Ecosystem": lambda: render_sources(filters),
        "Citation Impact": lambda: render_citations(filters, counting_method),
    }
    renderers[page]()


try:
    main()
except DashboardDatabaseError as exc:
    st.error(str(exc))
    st.caption(
        "Configure `DATABASE_URL`, ensure PostgreSQL is reachable, and load the `dw` "
        "schema. Connection secrets are not displayed by this application."
    )
    st.stop()
