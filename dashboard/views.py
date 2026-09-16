"""Global AI Research Observatory — interactive warehouse explorer."""

from __future__ import annotations

import sys
from pathlib import Path


# ``streamlit run dashboard/app.py`` must work from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

from dashboard import queries  # noqa: E402
from dashboard.db import (  # noqa: E402
    database_cache_key,
    fetch_dataframe,
)
from dashboard.ui import (  # noqa: E402
    BLUE,
    GOLD,
    NAVY,
    PALETTE,
    TEAL,
    apply_figure_style,
    compact_table,
    empty_state,
    horizontal_bar,
    line_chart,
    place_legend_below_plot,
    safe_file_stem,
)


def configure_page() -> None:
    st.set_page_config(
        page_title="Global AI Research Observatory",
        page_icon="◈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown(
        """
        <style>
          .block-container {padding-top: 1.55rem; padding-bottom: 3rem; max-width: 1500px;}
          [data-testid="stSidebar"] {
            border-right: 1px solid color-mix(in srgb, currentColor 15%, transparent);
          }
          .st-key-navigation_page .react-aria-ComboBox > [role="group"] {
            background: color-mix(in srgb, currentColor 5%, transparent);
            border: 1px solid color-mix(in srgb, currentColor 20%, transparent);
            border-radius: .55rem;
            overflow: hidden;
            transition: border-color .15s ease, box-shadow .15s ease;
          }
          .st-key-navigation_page .react-aria-ComboBox > [role="group"]:hover {
            border-color: #2E6F9E;
          }
          .st-key-navigation_page .react-aria-ComboBox > [role="group"]:focus-within {
            border-color: #2E6F9E;
            box-shadow: 0 0 0 .12rem rgba(46, 111, 158, .22);
          }
          .st-key-navigation_page .react-aria-ComboBox input,
          .st-key-navigation_page .react-aria-ComboBox button {
            background: transparent !important;
            border: 0 !important;
            color: inherit !important;
          }
          .st-key-reset_filters button,
          .st-key-reset_filters button:hover,
          .st-key-reset_filters button:focus,
          .st-key-reset_filters button:focus-visible,
          .st-key-reset_filters button * {
            color: #FFFFFF !important;
          }
          [data-testid="stMetric"] {
            background: color-mix(in srgb, currentColor 5%, transparent);
            border: 1px solid color-mix(in srgb, currentColor 15%, transparent);
            border-radius: .55rem; padding: .85rem 1rem;
          }
          [data-testid="stMetricValue"] {
            font-size: clamp(1.25rem, 2.2vw, 2rem);
          }
          [data-testid="stMetricLabel"] p {white-space: normal; overflow: visible;}
          [data-testid="stExpander"] {
            border-color: color-mix(in srgb, currentColor 15%, transparent);
          }
          [data-testid="stDataFrame"] {
            border: 1px solid color-mix(in srgb, currentColor 15%, transparent);
          }
          h1, h2, h3 {letter-spacing: -.015em;}
          h1 {font-size: 2rem !important; margin-bottom: .1rem !important;}
          .observatory-subtitle {opacity:.72; font-size:1rem; margin-bottom:.7rem;}
          .context-line {opacity:.72; font-size:.88rem; margin:.1rem 0 1rem;}
          .stDownloadButton button {color:#5D9DCC;}
        </style>
        """,
        unsafe_allow_html=True,
    )


PAGES = (
    "Overview",
    "Research Growth",
    "Geographic Leadership",
    "Normalized Leadership",
    "Socioeconomic Context",
    "Topics",
    "Institutions",
    "Publication Ecosystem",
    "Citation Impact",
)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_query(
    sql_text: str, params: tuple, connection_key: str
) -> pd.DataFrame:
    del connection_key  # It exists only to invalidate cache after a DB change.
    return fetch_dataframe(sql_text, params)


def run(spec: queries.QuerySpec) -> pd.DataFrame:
    return _cached_query(spec.sql, spec.params, database_cache_key())


def numeric(frame: pd.DataFrame, *columns: str) -> pd.DataFrame:
    converted = frame.copy()
    for column in columns:
        if column in converted:
            converted[column] = pd.to_numeric(converted[column], errors="coerce")
    return converted


def apply_publication_support(
    frame: pd.DataFrame, minimum_support: int
) -> pd.DataFrame:
    """Filter after country-year support has been calculated."""
    return frame[frame["full_publications"] >= minimum_support].copy()


def topic_annual_shares(
    evolution: pd.DataFrame, annual_publications: pd.DataFrame
) -> pd.DataFrame:
    """Calculate overlapping topic participation as a share of each year's corpus."""
    annual = annual_publications[
        ["calendar_year", "publications"]
    ].rename(columns={"publications": "annual_publications"})
    result = evolution.merge(annual, on="calendar_year", how="left")
    result["annual_share"] = (
        100 * result["publications"] / result["annual_publications"]
    )
    return result


def source_ranking(
    sources: pd.DataFrame, *, include_missing: bool, top_n: int
) -> pd.DataFrame:
    """Keep source missingness in the data while optionally omitting it from ranking."""
    ranked = sources
    if not include_missing:
        ranked = ranked[ranked["source_id"] != queries.MISSING_VALUE]
    return ranked.head(top_n).copy()


def option_values(options: pd.DataFrame, name: str) -> tuple[list[str], dict[str, str]]:
    selected = options.loc[options["filter_name"] == name, ["value", "label"]]
    values = selected["value"].astype(str).tolist()
    labels = dict(zip(values, selected["label"].astype(str)))
    return values, labels


def multiselect_filter(
    options: pd.DataFrame,
    filter_name: str,
    label: str,
    *,
    help_text: str | None = None,
    container=None,
) -> tuple[str, ...]:
    values, labels = option_values(options, filter_name)
    target = container if container is not None else st.sidebar
    chosen = target.multiselect(
        label,
        values,
        format_func=lambda value: labels.get(value, value),
        help=help_text,
        key=f"filter_{filter_name}",
    )
    return tuple(chosen)


def build_filters(options: pd.DataFrame, minimum_year: int, maximum_year: int):
    st.sidebar.markdown("### Filters")
    categorical_keys = (
        "filter_country",
        "filter_region",
        "filter_income_level",
        "filter_topic",
        "filter_subfield",
        "filter_field",
        "filter_domain",
        "filter_institution_type",
        "filter_source_type",
        "filter_publication_type",
        "filter_language",
        "filter_open_access",
    )

    def reset_global_filters() -> None:
        st.session_state["filter_year_range"] = (minimum_year, maximum_year)
        st.session_state["counting_method"] = "Full"
        for key in categorical_keys:
            st.session_state[key] = []
        st.cache_data.clear()

    st.sidebar.button(
        "Reset filters",
        type="primary",
        width="stretch",
        key="reset_filters",
        on_click=reset_global_filters,
    )
    if minimum_year == maximum_year:
        selected_years = (minimum_year, maximum_year)
        st.sidebar.caption(f"Publication year: {minimum_year}")
    else:
        stored_years = st.session_state.get("filter_year_range")
        if (
            not stored_years
            or stored_years[0] < minimum_year
            or stored_years[1] > maximum_year
        ):
            st.session_state["filter_year_range"] = (minimum_year, maximum_year)
        selected_years = st.sidebar.slider(
            "Publication year range",
            minimum_year,
            maximum_year,
            key="filter_year_range",
        )

    geographic_filters = st.sidebar.expander("Geographic filters")
    countries = multiselect_filter(
        options, "country", "Country", container=geographic_filters
    )
    regions = multiselect_filter(
        options,
        "region",
        "Region",
        help_text="Region and income level are alternative country attributes.",
        container=geographic_filters,
    )
    income_levels = multiselect_filter(
        options, "income_level", "Income level", container=geographic_filters
    )
    thematic_filters = st.sidebar.expander("Thematic filters")
    topics = multiselect_filter(options, "topic", "Topic", container=thematic_filters)
    subfields = multiselect_filter(
        options, "subfield", "Subfield", container=thematic_filters
    )
    fields = multiselect_filter(options, "field", "Field", container=thematic_filters)
    domains = multiselect_filter(
        options, "domain", "Domain", container=thematic_filters
    )

    more_filters = st.sidebar.expander("Additional filters")
    institution_types = multiselect_filter(
        options,
        "institution_type",
        "Institution type",
        container=more_filters,
    )
    source_types = multiselect_filter(
        options, "source_type", "Source type", container=more_filters
    )
    publication_types = multiselect_filter(
        options,
        "publication_type",
        "Publication type",
        container=more_filters,
    )
    languages = multiselect_filter(
        options, "language", "Language", container=more_filters
    )
    open_access_values = multiselect_filter(
        options,
        "open_access",
        "Open Access",
        container=more_filters,
    )

    st.sidebar.markdown("### Attribution")
    counting_method = st.sidebar.radio(
        "Attribution",
        ("Full", "Fractional"),
        horizontal=True,
        help=(
            "Full counting gives every participating country, topic, or institution "
            "one publication. Fractional counting divides one publication across "
            "all members of that relationship."
        ),
        key="counting_method",
    )
    filters = queries.FilterState(
        start_year=int(selected_years[0]),
        end_year=int(selected_years[1]),
        countries=countries,
        regions=regions,
        income_levels=income_levels,
        topics=topics,
        subfields=subfields,
        fields=fields,
        domains=domains,
        institution_types=institution_types,
        source_types=source_types,
        publication_types=publication_types,
        languages=languages,
        open_access_values=open_access_values,
    )
    active_filters = sum(
        bool(value)
        for value in (
            countries,
            regions,
            income_levels,
            topics,
            subfields,
            fields,
            domains,
            institution_types,
            source_types,
            publication_types,
            languages,
            open_access_values,
        )
    )
    st.sidebar.caption(
        "Full counting: full publication participation for each associated member. "
        "Fractional counting: contribution distributed through bridge weights, "
        "which are never rescaled after filtering."
    )
    return filters, counting_method, active_filters


def top_n_control(key: str, default: int = 15) -> int:
    values = (4, 6, 8, 10, 15, 20, 30)
    return st.select_slider("Top N", values, value=default, key=key)


def metric_value(value, *, percent: bool = False) -> str:
    if pd.isna(value):
        return "Unavailable"
    number = float(value)
    return f"{number:,.1f}%" if percent else f"{number:,.0f}"


def render_overview(filters: queries.FilterState, counting_method: str) -> None:
    st.header("Overview")
    st.caption(
        "Explore growth, geographic leadership, socioeconomic differences, thematic "
        "evolution and institutional contributors to the global AI research boom."
    )
    metrics = run(queries.overview_metrics(filters))
    if metrics.empty:
        empty_state()
        return
    row = metrics.iloc[0]
    columns = st.columns(3) + st.columns(3)
    columns[0].metric("Publications", metric_value(row["publications"]))
    columns[1].metric(
        "Cumulative citations",
        metric_value(row["cumulative_citations"]),
        help="Cumulative citation snapshot observed at OpenAlex extraction time.",
    )
    columns[2].metric("Represented countries", metric_value(row["countries"]))
    columns[3].metric("Institutions", metric_value(row["institutions"]))
    columns[4].metric("Topics", metric_value(row["topics"]))
    columns[5].metric("Open-access share", metric_value(row["open_access_share"], percent=True))
    st.caption(
        f"Open-access share: {metric_value(row['open_access_share'], percent=True)}. "
        "This share excludes records whose open-access flag is unavailable."
    )

    trend = numeric(run(queries.publications_by_year(filters)), "publications")
    countries = numeric(
        run(queries.country_output(filters, counting_method, limit=12)), "publications"
    )
    topics = numeric(
        run(queries.topic_output(filters, counting_method, "Topic", 12)),
        "publications",
    )
    publication_types = numeric(
        run(queries.publication_type_output(filters)), "publications"
    )

    left, right = st.columns(2)
    with left:
        if trend.empty:
            empty_state()
        else:
            st.plotly_chart(
                line_chart(
                    trend,
                    x="calendar_year",
                    y="publications",
                    title="Publication evolution",
                    y_title="Publications",
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    with right:
        if countries.empty:
            empty_state()
        else:
            st.plotly_chart(
                horizontal_bar(
                    countries,
                    x="publications",
                    y="country_name",
                    title=f"Leading countries · {counting_method.lower()} count",
                    x_title="Attributed publications",
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    left, right = st.columns(2)
    with left:
        if topics.empty:
            empty_state()
        else:
            st.plotly_chart(
                horizontal_bar(
                    topics,
                    x="publications",
                    y="member_name",
                    title=f"Leading topics · {counting_method.lower()} counting",
                    x_title="Attributed publications",
                    color=TEAL,
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    with right:
        if publication_types.empty:
            empty_state()
        else:
            st.plotly_chart(
                horizontal_bar(
                    publication_types.head(12),
                    x="publications",
                    y="publication_type",
                    title="Publication-type distribution",
                    x_title="Publications",
                    color=GOLD,
                ),
        width="stretch",
                config={"displaylogo": False},
            )

def render_evolution(filters: queries.FilterState) -> None:
    st.header("Research Growth")
    st.caption("How has qualifying AI research evolved since 2018?")
    trend = numeric(
        run(queries.publications_by_year(filters)),
        "publications",
        "cumulative_citations",
        "average_citations",
    )
    if trend.empty:
        empty_state()
        return

    previous = trend["publications"].shift(1)
    adjacent = trend["calendar_year"].diff().eq(1) & previous.gt(0)
    trend["year_over_year_growth"] = ((trend["publications"] / previous - 1) * 100).where(adjacent)
    first = trend.iloc[0]
    last = trend.iloc[-1]
    total_growth = (
        (float(last["publications"]) / float(first["publications"]) - 1) * 100
        if float(first["publications"]) > 0
        else float("nan")
    )
    kpi_left, kpi_mid, kpi_right = st.columns(3)
    kpi_left.metric(
        f"{int(first['calendar_year'])}–{int(last['calendar_year'])}",
        metric_value(total_growth, percent=True),
    )
    kpi_mid.metric(
        f"{int(last['calendar_year'])} year-over-year",
        metric_value(last["year_over_year_growth"], percent=True),
    )
    kpi_right.metric(
        f"{int(last['calendar_year'])} publications",
        metric_value(last["publications"]),
    )
    grain = st.radio("Time detail", ("Year", "Quarter", "Month"), horizontal=True)
    if grain != "Year":
        detail = numeric(run(queries.temporal_navigation(filters, grain)), "publications", "period_growth_pct")
        fig = px.line(detail, x="period", y="publications", markers=True, title=f"Publication output by {grain.lower()}")
        st.plotly_chart(apply_figure_style(fig), width="stretch")
        st.caption(
            "Quarter and month patterns may reflect OpenAlex source-date resolution; "
            "period growth compares adjacent calendar periods."
        )
    figure = make_subplots(specs=[[{"secondary_y": True}]])
    figure.add_trace(
        go.Scatter(
            x=trend["calendar_year"],
            y=trend["publications"],
            name="Publications",
            mode="lines+markers",
            line=dict(color=BLUE, width=3),
            hovertemplate="%{x}: %{y:,.0f} publications<extra></extra>",
        ),
        secondary_y=False,
    )
    figure.add_trace(
        go.Bar(
            x=trend["calendar_year"],
            y=trend["year_over_year_growth"],
            name="Year-over-year growth",
            marker_color="rgba(199,152,69,.45)",
            hovertemplate="%{x}: %{y:.1f}% YoY<extra></extra>",
        ),
        secondary_y=True,
    )
    figure.update_layout(title="Publication evolution and year-over-year growth")
    figure.update_xaxes(dtick=1)
    figure.update_yaxes(title_text="Publications", secondary_y=False)
    figure.update_yaxes(title_text="Growth (%)", secondary_y=True, showgrid=False)
    st.plotly_chart(
        apply_figure_style(figure, 430),
        width="stretch",
        config={"displaylogo": False},
    )
    st.caption(
        "The sample was allocated proportionally by year to the qualifying OpenAlex "
        "population, preserving its recorded temporal structure."
    )

    with st.expander("Supporting trends"):
        citations = trend[["calendar_year", "cumulative_citations", "average_citations"]]
        publication_types = numeric(
            run(queries.publication_type_by_year(filters)), "publications"
        )
        access = numeric(run(queries.open_access_by_year(filters)), "publications")
        left, right = st.columns(2)
        with left:
            citation_metric = st.radio(
                "Citation view",
                ("Cumulative citations", "Average citations per publication"),
                horizontal=True,
                key="evolution_citation_metric",
            )
            citation_column = (
                "cumulative_citations"
                if citation_metric == "Cumulative citations"
                else "average_citations"
            )
            st.plotly_chart(
                line_chart(
                    citations,
                    x="calendar_year",
                    y=citation_column,
                    title=f"{citation_metric} by publication year",
                    y_title=citation_metric,
                    height=390,
                ),
                width="stretch",
                config={"displaylogo": False},
            )
            st.caption(
                "Citation counts are cumulative OpenAlex snapshots observed at "
                "extraction time."
            )
        with right:
            if access.empty:
                empty_state()
            else:
                access_figure = px.area(
                    access,
                    x="calendar_year",
                    y="publications",
                    color="open_access",
                    title="Open Access over time",
                    color_discrete_sequence=PALETTE,
                )
                access_figure.update_layout(xaxis_title=None, yaxis_title="Publications")
                access_figure.update_xaxes(dtick=1)
                st.plotly_chart(
                    apply_figure_style(access_figure, 390),
                    width="stretch",
                    config={"displaylogo": False},
                )

        if not publication_types.empty:
            leaders = (
                publication_types.groupby("publication_type", as_index=False)["publications"]
                .sum()
                .nlargest(6, "publications")["publication_type"]
            )
            display = publication_types[
                publication_types["publication_type"].isin(leaders)
            ]
            st.plotly_chart(
                line_chart(
                    display,
                    x="calendar_year",
                    y="publications",
                    color="publication_type",
                    title="Leading publication types over time",
                    y_title="Publications",
                ),
                width="stretch",
                config={"displaylogo": False},
            )


def render_countries(filters: queries.FilterState, counting_method: str) -> None:
    st.header("Geographic Leadership")
    st.caption(
        "Which countries lead AI research, and how does attribution change the ranking?"
    )
    period_label = f"{filters.start_year}–{filters.end_year}"
    control_left, control_right = st.columns((1, 3))
    with control_left:
        top_n = top_n_control("country_top_n")
    with control_right:
        compare_attribution = st.toggle(
            "Compare attribution methods",
            value=False,
            help=(
                "Show Full and Fractional country rankings for the same period and "
                "Top N selection."
            ),
        )
    st.caption(
        "Full counting = full publication participation for each associated "
        "country. Fractional counting = publication contribution distributed "
        "across associated countries through bridge weights."
    )
    countries = numeric(
        run(queries.country_output(filters, counting_method, limit=500)),
        "publications",
    )
    if countries.empty:
        empty_state()
        return

    def ranking_figure(frame: pd.DataFrame, method: str, height: int = 500):
        return horizontal_bar(
            frame.head(top_n),
            x="publications",
            y="country_name",
            title=f"{method} counting · {period_label}",
            x_title=(
                "Full publication participation"
                if method == "Full"
                else "Fractional publications"
            ),
            color=BLUE if method == "Full" else TEAL,
            hover_data={
                "country_code_iso2": True,
                "region_name": True,
                "income_level_name": True,
                "publications": ":,.2f",
            },
            height=height,
        )

    if compare_attribution:
        full_countries = numeric(
            run(queries.country_output(filters, "Full", limit=500)),
            "publications",
        )
        fractional_countries = numeric(
            run(queries.country_output(filters, "Fractional", limit=500)),
            "publications",
        )
        comparison_max = max(
            full_countries.head(top_n)["publications"].max(),
            fractional_countries.head(top_n)["publications"].max(),
        )
        full_figure = ranking_figure(full_countries, "Full", 540)
        fractional_figure = ranking_figure(fractional_countries, "Fractional", 540)
        full_figure.update_xaxes(range=[0, comparison_max * 1.04])
        fractional_figure.update_xaxes(range=[0, comparison_max * 1.04])
        comparison_columns = st.columns(2)
        with comparison_columns[0]:
            st.plotly_chart(
                full_figure,
                width="stretch",
                config={"displaylogo": False},
            )
        with comparison_columns[1]:
            st.plotly_chart(
                fractional_figure,
                width="stretch",
                config={"displaylogo": False},
            )
        st.caption(
            "Both rankings use the same linear axis range, publication period and Top N."
        )
    else:
        st.plotly_chart(
            ranking_figure(countries, counting_method),
            width="stretch",
            config={"displaylogo": False},
        )

    map_figure = px.choropleth(
        countries,
        locations="country_code_iso3",
        color="publications",
        hover_name="country_name",
        hover_data={
            "country_code_iso3": False,
            "country_code_iso2": True,
            "publications": ":,.2f",
        },
        color_continuous_scale=["#BFD7E8", BLUE, NAVY],
        title=f"Geographic participation · {counting_method} counting · {period_label}",
    )
    map_figure.update_geos(
        showframe=False,
        showcoastlines=True,
        coastlinecolor="rgba(127,127,127,.55)",
        bgcolor="rgba(0,0,0,0)",
    )
    map_figure.update_layout(coloraxis_colorbar_title="Publications")
    st.plotly_chart(
        apply_figure_style(map_figure, 460),
        width="stretch",
        config={"displaylogo": False},
    )
    st.caption(
        "Taiwan (TW/TWN) retains its warehouse identity; the map does not remap it."
    )
    regions = numeric(
        run(queries.regional_output(filters, counting_method)), "publications"
    )
    with st.expander("Regional context and data"):
        if not regions.empty:
            st.plotly_chart(
                horizontal_bar(
                    regions.head(15),
                    x="publications",
                    y="region",
                    title="Country participation grouped by region",
                    x_title="Attributed publications",
                    color=TEAL,
                    height=390,
                ),
                width="stretch",
                config={"displaylogo": False},
            )
            st.caption(
                "Region and income level are alternative country attributes, not "
                "levels of a single hierarchy."
            )
        compact_table(
            countries,
            key="download_country_output",
            file_name="country-publication-output.csv",
            column_config={
                "publications": st.column_config.NumberColumn(format="%.2f"),
                "has_world_bank_data": st.column_config.CheckboxColumn(
                    "World Bank coverage"
                ),
            },
        )


def render_normalized(filters: queries.FilterState, counting_method: str) -> None:
    st.header("Normalized Leadership")
    st.caption("Does geographic leadership change after accounting for national scale?")
    st.caption(
        "Metric attribution: Fractional. Normalized country metrics always use "
        "fractional country attribution, regardless of the global attribution "
        f"selection (currently {counting_method})."
    )
    data = numeric(
        run(queries.normalized_country_year(filters)),
        "full_publications",
        "fractional_publications",
        "population",
        "gdp_current_usd",
        "gdp_per_capita_current_usd",
        "internet_users_pct",
        "rd_expenditure_pct_gdp",
        "publications_per_million",
        "publications_per_billion_gdp",
    )
    if data.empty:
        empty_state()
        return
    available_years = sorted(data["calendar_year"].dropna().astype(int).unique())
    if not available_years:
        empty_state()
        return
    control_left, control_mid, control_support, control_right = st.columns((1, 2, 1.7, 1))
    with control_left:
        default_year = 2023 if 2023 in available_years else available_years[-1]
        analysis_year = st.selectbox(
            "Analysis year",
            available_years,
            index=available_years.index(default_year),
            key="normalized_analysis_year",
        )
    metric_labels = {
        "Fractional publications per million inhabitants": "publications_per_million",
        "Fractional publications per billion USD GDP": "publications_per_billion_gdp",
    }
    metric_titles = {
        "publications_per_million": "Per million inhabitants",
        "publications_per_billion_gdp": "Per billion USD GDP",
    }
    with control_mid:
        metric_label = st.selectbox("Normalized metric", list(metric_labels))
    support_labels = {"All": 0, "≥ 10": 10, "≥ 20": 20, "≥ 30": 30}
    with control_support:
        support_label = st.selectbox(
            "Minimum full publication participation",
            list(support_labels),
            index=3,
            help=(
                "Applied after country-year output is calculated. This limits "
                "small-denominator rankings without changing fractional weights."
            ),
        )
    with control_right:
        top_n = top_n_control("normalized_top_n")
    metric = metric_labels[metric_label]
    minimum_support = support_labels[support_label]
    supported_data = apply_publication_support(data, minimum_support)
    snapshot = supported_data[supported_data["calendar_year"] == analysis_year]
    ranked = snapshot.dropna(subset=[metric]).nlargest(top_n, metric)
    st.caption(
        f"Rankings, scatterplots and comparisons use a minimum of {minimum_support:,} "
        "full publication participations per country-year."
        if minimum_support
        else "No minimum publication-support threshold is applied."
    )
    if metric == "publications_per_billion_gdp":
        st.caption("This metric shows publication output relative to economic scale.")

    left, right = st.columns(2)
    with left:
        if ranked.empty:
            empty_state("No denominator data are available for this metric and year.")
        else:
            st.plotly_chart(
                horizontal_bar(
                    ranked,
                    x=metric,
                    y="country_name",
                    title=f"{metric_titles[metric]} · {analysis_year}",
                    x_title=metric_label,
                    height=480,
                    hover_data={
                        "full_publications": ":,.0f",
                        "fractional_publications": ":,.3f",
                        "population": ":,.0f",
                        "gdp_current_usd": ":,.3g",
                    },
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    with right:
        context_options = {
            "GDP (current USD)": "gdp_current_usd",
            "Population": "population",
            "GDP per capita (current USD)": "gdp_per_capita_current_usd",
            "Internet users (%)": "internet_users_pct",
            "R&D expenditure (% GDP)": "rd_expenditure_pct_gdp",
        }
        context_label = st.selectbox("Context indicator", list(context_options))
        context_column = context_options[context_label]
        scatter_data = snapshot.dropna(
            subset=[context_column, "publications_per_million"]
        )
        if scatter_data.empty:
            empty_state("No paired indicator observations are available for this year.")
        else:
            scale_indicator = context_column in {
                "gdp_current_usd",
                "population",
                "gdp_per_capita_current_usd",
            }
            logarithmic_x = (
                st.toggle(
                    "Logarithmic X-axis",
                    value=True,
                    help="A log scale keeps large economies from compressing the comparison.",
                )
                if scale_indicator
                else False
            )
            scatter = px.scatter(
                scatter_data,
                x=context_column,
                y="publications_per_million",
                hover_name="country_name",
                color="region_name",
                hover_data={
                    "full_publications": ":,.0f",
                    "fractional_publications": ":,.3f",
                },
                title=f"Research intensity and {context_label.lower()} · {analysis_year}",
                color_discrete_sequence=PALETTE,
            )
            scatter.update_layout(
                xaxis_title=(
                    f"{context_label} (log scale)" if logarithmic_x else context_label
                ),
                yaxis_title="Fractional publications per million inhabitants",
                showlegend=False,
            )
            if logarithmic_x:
                scatter.update_xaxes(type="log")
            st.plotly_chart(
                apply_figure_style(scatter, 480),
        width="stretch",
                config={"displaylogo": False},
            )

    country_options = (
        supported_data[["country_code_iso2", "country_name"]]
        .drop_duplicates()
        .sort_values("country_name")
    )
    country_labels = dict(
        zip(country_options["country_code_iso2"], country_options["country_name"])
    )
    defaults = (
        ranked["country_code_iso2"].head(5).tolist()
        if not ranked.empty
        else country_options["country_code_iso2"].head(5).tolist()
    )
    selected_countries = st.multiselect(
        "Countries to compare over time",
        country_options["country_code_iso2"].tolist(),
        default=defaults,
        format_func=lambda code: f"{country_labels.get(code, code)} ({code})",
    )
    evolution = supported_data[
        supported_data["country_code_iso2"].isin(selected_countries)
    ].dropna(subset=[metric])
    if selected_countries and not evolution.empty:
        st.plotly_chart(
            line_chart(
                evolution,
                x="calendar_year",
                y=metric,
                color="country_name",
                title=f"{metric_label} over time",
                y_title=metric_label,
            ),
        width="stretch",
            config={"displaylogo": False},
        )

    ranks = numeric(
        run(queries.leadership_ranks(filters)),
        "full_publications",
        "fractional_publications",
        "publications_per_million",
        "publications_per_billion_gdp",
    )
    ranks = ranks[
        (ranks["calendar_year"] == analysis_year)
        & (ranks["full_publications"] >= minimum_support)
    ].copy()
    ranks["absolute_rank"] = ranks["fractional_publications"].rank(
        method="min", ascending=False
    )
    ranks["per_million_rank"] = ranks["publications_per_million"].rank(
        method="min", ascending=False, na_option="keep"
    )
    ranks["per_gdp_rank"] = ranks["publications_per_billion_gdp"].rank(
        method="min", ascending=False, na_option="keep"
    )
    ranks["population_rank_shift"] = (
        ranks["absolute_rank"] - ranks["per_million_rank"]
    )
    ranks["gdp_rank_shift"] = ranks["absolute_rank"] - ranks["per_gdp_rank"]
    with st.expander("Rank comparison and data"):
        st.subheader("Absolute and normalized ranks")
        compact_table(ranks[["country_name", "full_publications", "fractional_publications", "absolute_rank", "per_million_rank", "per_gdp_rank", "population_rank_shift", "gdp_rank_shift"]], key="rank_download", file_name="leadership-ranks.csv")
        st.caption("Positive rank shift means a higher normalized position. Unavailable denominators have no normalized rank.")

        display_columns = [
            "country_code_iso2",
            "country_name",
            "calendar_year",
            "full_publications",
            "fractional_publications",
            "population",
            "gdp_current_usd",
            "gdp_per_capita_current_usd",
            "internet_users_pct",
            "rd_expenditure_pct_gdp",
            "publications_per_million",
            "publications_per_billion_gdp",
        ]
        compact_table(
            supported_data[display_columns],
            key="download_normalized_country_year",
            file_name="normalized-country-year.csv",
            column_config={
                "full_publications": st.column_config.NumberColumn(format="%.0f"),
                "fractional_publications": st.column_config.NumberColumn(format="%.3f"),
                "gdp_current_usd": st.column_config.NumberColumn(format="$%.3g"),
                "gdp_per_capita_current_usd": st.column_config.NumberColumn(format="$%.2f"),
                "internet_users_pct": st.column_config.NumberColumn(format="%.2f%%"),
                "rd_expenditure_pct_gdp": st.column_config.NumberColumn(format="%.2f%%"),
                "publications_per_million": st.column_config.NumberColumn(format="%.3f"),
                "publications_per_billion_gdp": st.column_config.NumberColumn(format="%.3f"),
            },
            height=390,
        )
    st.caption(
        "World Bank indicators are shown only where observations are available; "
        "missing observations are not imputed. Zero denominators also yield an "
        "unavailable normalized value. TW therefore retains publication output but "
        "has no normalized metrics in this project."
    )


def render_topics(filters: queries.FilterState, counting_method: str) -> None:
    st.header("Topics")
    st.caption("How has the thematic composition of the selected AI corpus evolved?")
    st.caption(
        "Primary Topic determines corpus membership; all associated OpenAlex topics "
        "are retained for thematic analysis."
    )
    controls = st.columns((2, 2, 1))
    with controls[0]:
        level = st.selectbox("Hierarchy level", queries.TOPIC_LEVEL_NAMES)
    with controls[1]:
        trend_measure = st.selectbox(
            "Trend measure",
            ("Share of annual publications", "Publications"),
            help=(
                "Annual share uses the selected Topic attribution divided by "
                "publications in the same year. Full memberships overlap."
            ),
        )
    with controls[2]:
        top_n = top_n_control("topic_top_n", default=4)
    ranking = numeric(
        run(queries.topic_output(filters, counting_method, level, top_n)),
        "publications",
    )
    evolution = numeric(
        run(queries.topic_evolution(filters, counting_method, level, top_n)),
        "publications",
    )
    if evolution.empty:
        empty_state()
    elif trend_measure == "Share of annual publications":
        annual = numeric(
            run(queries.publications_by_year(filters)), "publications"
        )
        evolution = topic_annual_shares(evolution, annual)
        figure = px.line(
            evolution,
            x="calendar_year",
            y="annual_share",
            color="member_name",
            markers=True,
            title=f"Leading {level.lower()}s over time · annual share",
            color_discrete_sequence=PALETTE,
            hover_data={
                "publications": ":,.0f",
                "annual_share": ":.2f",
                "annual_publications": False,
            },
        )
        figure.update_layout(
            xaxis_title=None,
            yaxis_title="Share of annual publications (%)",
        )
        figure.update_xaxes(dtick=1)
        figure.update_yaxes(ticksuffix="%")
        st.plotly_chart(
            place_legend_below_plot(apply_figure_style(figure, 500)),
            width="stretch",
            config={"displaylogo": False},
        )
        if counting_method == "Full":
            st.caption(
                "Shares use full topic publication participation. Topics overlap, "
                "so shares across topics do not necessarily sum to 100%."
            )
        else:
            st.caption(
                "Shares use Topic bridge fractional weights. Weights are not "
                "rescaled after filtering or hierarchy roll-up."
            )
    else:
        st.plotly_chart(
            place_legend_below_plot(
                line_chart(
                    evolution,
                    x="calendar_year",
                    y="publications",
                    color="member_name",
                    title=f"Leading {level.lower()}s over time",
                    y_title=(
                        "Full publication participation"
                        if counting_method == "Full"
                        else "Fractional publications"
                    ),
                    height=500,
                ),
            ),
            width="stretch",
            config={"displaylogo": False},
        )

    with st.expander("Ranking, hierarchy notes and data"):
        if ranking.empty:
            empty_state()
        else:
            st.plotly_chart(
                horizontal_bar(
                    ranking,
                    x="publications",
                    y="member_name",
                    title=f"Leading {level.lower()}s · {counting_method.lower()} count",
                    x_title="Attributed publications",
                    height=500,
                ),
                width="stretch",
                config={"displaylogo": False},
            )
            compact_table(
                ranking,
                key="download_topic_output",
                file_name=f"{safe_file_stem(level)}-output.csv",
                column_config={
                    "publications": st.column_config.NumberColumn(format="%.3f")
                },
            )
        st.write(
            "The warehouse hierarchy is Topic → Subfield → Field → Domain. "
            "Full counting counts each publication for every associated member; "
            "fractional counting uses the Topic bridge weight. No custom AI "
            "macro-area classification is inferred."
        )


def render_institutions(filters: queries.FilterState, counting_method: str) -> None:
    st.header("Institutions")
    st.caption(
        "Which institutions contribute most, and how does fractional attribution "
        "change leadership?"
    )
    st.caption(
        "Full counting shows publication participation. Fractional counting "
        "distributes each publication across all associated institutions."
    )
    top_n = top_n_control("institution_top_n")
    institutions = numeric(
        run(queries.institution_output(filters, counting_method, limit=100)),
        "publications",
    )
    types = numeric(
        run(queries.institution_type_output(filters, counting_method)), "publications"
    )
    if institutions.empty:
        empty_state()
        return
    st.plotly_chart(
        horizontal_bar(
            institutions.head(top_n),
            x="publications",
            y="institution_name",
            title=f"Top {top_n} institutions · {counting_method.lower()} counting",
            x_title=(
                "Full publication participation"
                if counting_method == "Full"
                else "Fractional publications"
            ),
            height=530,
            hover_data={
                "institution_type": True,
                "country_code_iso2": True,
                "publications": ":,.2f",
            },
        ),
        width="stretch",
        config={"displaylogo": False},
    )

    institution_labels = dict(
        zip(institutions["institution_id"], institutions["institution_name"])
    )
    selected = st.multiselect(
        "Institutions to compare over time",
        institutions["institution_id"].tolist(),
        default=institutions["institution_id"].head(3).tolist(),
        format_func=lambda value: institution_labels.get(value, value),
        max_selections=8,
    )
    if selected:
        evolution = numeric(
            run(
                queries.institution_evolution(
                    filters, counting_method, tuple(selected)
                )
            ),
            "publications",
        )
        if not evolution.empty:
            st.plotly_chart(
                line_chart(
                    evolution,
                    x="calendar_year",
                    y="publications",
                    color="institution_name",
                    title="Selected institutions over time",
                    y_title="Attributed publications",
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    with st.expander("Institution types and data"):
        if not types.empty:
            st.plotly_chart(
                horizontal_bar(
                    types,
                    x="publications",
                    y="institution_type",
                    title="Institution types",
                    x_title="Attributed publications",
                    color=TEAL,
                    height=360,
                ),
                width="stretch",
                config={"displaylogo": False},
            )
        compact_table(
            institutions,
            key="download_institution_output",
            file_name="institution-output.csv",
            column_config={
                "publications": st.column_config.NumberColumn(format="%.3f")
            },
        )


def render_sources(filters: queries.FilterState) -> None:
    st.header("Publication Ecosystem")
    st.caption(
        "Explore publication formats, primary sources and access patterns in the "
        "selected corpus."
    )
    st.caption(
        "Source is the optional OpenAlex primary-location source; publication type is "
        "stored directly on the publication fact."
    )
    st.caption(
        "Attribution mode: not applicable to single-valued Source and Publication "
        "Type analyses."
    )
    control_left, control_right = st.columns((1, 2))
    with control_left:
        top_n = top_n_control("source_top_n")
    with control_right:
        include_missing = st.toggle(
            "Include missing source in ranking",
            value=False,
            help="Missing source records remain included in the KPI and data table.",
        )
    sources = numeric(run(queries.source_output(filters, 500)), "publications")
    missing_rows = sources[sources["source_id"] == queries.MISSING_VALUE]
    metrics = run(queries.overview_metrics(filters))
    total_publications = (
        float(metrics.iloc[0]["publications"]) if not metrics.empty else 0
    )
    missing_publications = (
        float(missing_rows.iloc[0]["publications"]) if not missing_rows.empty else 0
    )
    missing_share = (
        100 * missing_publications / total_publications if total_publications else 0
    )
    st.metric(
        "Primary source unavailable",
        f"{missing_share:.1f}%",
        help=f"{missing_publications:,.0f} of {total_publications:,.0f} publications.",
    )
    ranking_sources = source_ranking(
        sources, include_missing=include_missing, top_n=top_n
    )
    source_types = numeric(run(queries.source_type_output(filters)), "publications")
    publication_types = numeric(
        run(queries.publication_type_output(filters)), "publications"
    )
    type_time = numeric(
        run(queries.publication_type_by_year(filters)), "publications"
    )
    left, right = st.columns(2)
    with left:
        if ranking_sources.empty:
            empty_state()
        else:
            st.plotly_chart(
                horizontal_bar(
                    ranking_sources,
                    x="publications",
                    y="source_name",
                    title=f"Top {top_n} primary sources",
                    x_title="Publications",
                    height=500,
                    hover_data={"source_type": True},
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    with right:
        if source_types.empty:
            empty_state()
        else:
            st.plotly_chart(
                horizontal_bar(
                    source_types,
                    x="publications",
                    y="source_type",
                    title="Source-type distribution",
                    x_title="Publications",
                    color=TEAL,
                    height=500,
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    left, right = st.columns(2)
    with left:
        if not publication_types.empty:
            st.plotly_chart(
                horizontal_bar(
                    publication_types.head(15),
                    x="publications",
                    y="publication_type",
                    title="Publication-type distribution",
                    x_title="Publications",
                    color=GOLD,
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    with right:
        if not type_time.empty:
            leaders = (
                type_time.groupby("publication_type", as_index=False)["publications"]
                .sum()
                .nlargest(6, "publications")["publication_type"]
            )
            display = type_time[type_time["publication_type"].isin(leaders)]
            st.plotly_chart(
                line_chart(
                    display,
                    x="calendar_year",
                    y="publications",
                    color="publication_type",
                    title="Leading publication types over time",
                    y_title="Publications",
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    if not sources.empty:
        with st.expander("Source data and CSV export"):
            compact_table(
                sources,
                key="download_source_output",
                file_name="source-output.csv",
                column_config={
                    "publications": st.column_config.NumberColumn(format="%.0f")
                },
            )
    st.caption(
        "“Missing / unavailable” is a presentation label for a nullable source key; "
        "it is not a synthetic warehouse dimension member. The Full/Fractional "
        "selector does not alter these single-valued attributes."
    )


def render_citations(filters: queries.FilterState, counting_method: str) -> None:
    st.header("Citation Impact")
    st.warning(
        "Citation counts are cumulative extraction-time snapshots. Older "
        "publications have had more time to accumulate citations; values are not "
        "citations received during the publication year.",
        icon="⚠️",
    )
    trend = numeric(
        run(queries.publications_by_year(filters)),
        "publications",
        "cumulative_citations",
        "average_citations",
    )
    distribution = numeric(
        run(queries.citation_distribution(filters)), "publications"
    )
    left, right = st.columns(2)
    with left:
        if trend.empty:
            empty_state()
        else:
            citation_view = st.radio(
                "Measure",
                ("Cumulative citations", "Average citations per publication"),
                horizontal=True,
                key="citation_page_measure",
            )
            column = (
                "cumulative_citations"
                if citation_view == "Cumulative citations"
                else "average_citations"
            )
            st.plotly_chart(
                line_chart(
                    trend,
                    x="calendar_year",
                    y=column,
                    title=f"{citation_view} by publication year",
                    y_title=citation_view,
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    with right:
        if distribution.empty:
            empty_state()
        else:
            distribution_figure = px.bar(
                distribution,
                x="citation_band",
                y="publications",
                title="Publication distribution by citation snapshot",
                color_discrete_sequence=[GOLD],
            )
            distribution_figure.update_layout(
                xaxis_title="Cumulative citations", yaxis_title="Publications"
            )
            st.plotly_chart(
                apply_figure_style(distribution_figure),
        width="stretch",
                config={"displaylogo": False},
            )

    controls = st.columns((2, 1))
    with controls[0]:
        dimension = st.selectbox(
            "Citation breakdown",
            ("Country", "Topic", "Institution", "Publication type", "Source type"),
        )
    with controls[1]:
        top_n = top_n_control("citation_top_n")
    ranking = numeric(
        run(queries.citation_ranking(filters, dimension, counting_method, top_n)),
        "cumulative_citations",
        "publications",
        "average_citations",
    )
    if ranking.empty:
        empty_state()
    else:
        st.plotly_chart(
            horizontal_bar(
                ranking,
                x="cumulative_citations",
                y="member_name",
                title=f"Cumulative citations by {dimension.lower()}",
                x_title="Attributed cumulative citations",
                height=500,
            ),
        width="stretch",
            config={"displaylogo": False},
        )
        compact_table(
            ranking,
            key="download_citation_ranking",
            file_name=f"citations-by-{safe_file_stem(dimension)}.csv",
            column_config={
                "cumulative_citations": st.column_config.NumberColumn(format="%.2f"),
                "publications": st.column_config.NumberColumn(format="%.2f"),
                "average_citations": st.column_config.NumberColumn(format="%.2f"),
            },
        )
    if dimension in {"Country", "Topic", "Institution"}:
        st.caption(
            f"In {counting_method.lower()} mode, citation snapshots are "
            + (
                "attributed in full to every participating member."
                if counting_method == "Full"
                else "multiplied by that relationship's bridge weight."
            )
        )
    else:
        st.caption(
            "Publication and source types are single-valued attributes, so the global "
            "counting selector does not change their citation totals."
        )


def render_socioeconomic(filters: queries.FilterState) -> None:
    st.header("Socioeconomic Context")
    st.caption(
        "How are national scale and socioeconomic conditions associated with AI "
        "research intensity? Associations do not establish causality."
    )
    navigation = st.radio(
        "Analytical path",
        (
            "Income-level gap",
            "Wealth",
            "R&D investment",
            "Digital access",
            "Regional capacity",
            "Changes over time",
        ),
        horizontal=True,
    )
    support_labels = {"All": 0, "≥ 10": 10, "≥ 20": 20, "≥ 30": 30}
    support_label = st.selectbox(
        "Minimum full publication participation",
        tuple(support_labels),
        index=3,
        key="socioeconomic_support",
        help=(
            "Applied to country-level socioeconomic comparisons after Country × Year "
            "publication support is calculated; fractional weights are unchanged."
        ),
    )
    minimum_support = support_labels[support_label]

    if navigation == "Income-level gap":
        data = numeric(
            run(queries.country_group_capacity(filters, "Income level")),
            "full_publications",
            "fractional_publications",
            "population",
            "gdp_current_usd",
            "publications_per_million",
            "publications_per_billion_gdp",
        )
        if data.empty:
            empty_state()
            return
        income_order = [
            "High income",
            "Upper middle income",
            "Lower middle income",
            "Low income",
        ]
        known = data[data["member"].isin(income_order)].copy()
        known["income_group"] = known["member"]
        missing_output = data.loc[
            data["member"] == queries.MISSING_LABEL, "fractional_publications"
        ].sum()
        if known.empty:
            empty_state(
                "No known World Bank income classifications match the selected "
                "filters. Missing classifications remain available below."
            )
            with st.expander("Income-level data and CSV export"):
                compact_table(
                    data,
                    key="income_gap_download",
                    file_name="income-level-research-gap.csv",
                )
            return
        annual_totals = known.groupby("calendar_year")[
            "fractional_publications"
        ].transform("sum")
        known["fractional_share"] = (
            100 * known["fractional_publications"] / annual_totals
        )
        left, right = st.columns((1.45, 0.85))
        with left:
            share_figure = px.area(
                known,
                x="calendar_year",
                y="fractional_share",
                color="income_group",
                title="Fractional AI Research Share by World Bank Income Level",
                category_orders={"income_group": income_order},
                color_discrete_sequence=PALETTE,
                hover_data={
                    "fractional_share": ":.1f",
                    "fractional_publications": ":,.1f",
                },
            )
            share_figure.update_layout(
                xaxis_title=None,
                yaxis_title="Share of attributed output (%)",
                hovermode="x unified",
            )
            share_figure.update_xaxes(dtick=1)
            share_figure.update_yaxes(range=[0, 100], ticksuffix="%")
            st.plotly_chart(
                place_legend_below_plot(apply_figure_style(share_figure, 470)),
                width="stretch",
                config={"displaylogo": False},
            )
        with right:
            years = sorted(known["calendar_year"].dropna().astype(int).unique())
            comparison_year = 2024 if 2024 in years else years[-1]
            per_capita = known[
                (known["calendar_year"] == comparison_year)
                & known["publications_per_million"].notna()
            ]
            gap_figure = px.bar(
                per_capita,
                x="income_group",
                y="publications_per_million",
                title=f"{comparison_year} research intensity by income level",
                category_orders={"income_group": income_order},
                color_discrete_sequence=[TEAL],
                hover_data={"publications_per_million": ":.3f"},
            )
            gap_figure.update_layout(
                xaxis_title=None,
                yaxis_title="Fractional publications per million inhabitants",
                showlegend=False,
            )
            st.plotly_chart(
                apply_figure_style(gap_figure, 470),
                width="stretch",
                config={"displaylogo": False},
            )
        st.caption(
            "The share chart uses country-attributed output with a known World Bank "
            "income group. Records lacking an income classification "
            f"({missing_output:,.1f} fractional publications across the selected "
            "years) remain in the data table."
        )
        with st.expander("Income-level data and CSV export"):
            compact_table(
                data,
                key="income_gap_download",
                file_name="income-level-research-gap.csv",
            )
        return

    if navigation == "Regional capacity":
        data = numeric(
            run(queries.country_group_capacity(filters, "Region")),
            "full_publications",
            "fractional_publications",
            "population",
            "gdp_current_usd",
            "publications_per_million",
        )
        if data.empty:
            empty_state()
            return
        measures = {
            "Fractional publications": "fractional_publications",
            "Full publication participation": "full_publications",
            "Fractional publications per million inhabitants": "publications_per_million",
        }
        measure_label = st.selectbox("Measure", tuple(measures))
        st.plotly_chart(
            line_chart(
                data,
                x="calendar_year",
                y=measures[measure_label],
                color="member",
                title="Regional research capacity",
                y_title=measure_label,
            ),
            width="stretch",
        )
        st.caption(
            "Region and income level are alternative country roll-ups. Population "
            "is never summed over time."
        )
        with st.expander("Regional data and CSV export"):
            compact_table(
                data,
                key="regional_capacity_download",
                file_name="regional-capacity.csv",
            )
        return

    if navigation == "Changes over time":
        data = numeric(
            run(queries.socioeconomic_change(filters)),
            "output_change",
            "intensity_change",
            "gdp_change",
            "internet_change_pp",
            "rd_change_pp",
        )
        indicator = st.selectbox(
            "Change measure",
            ("GDP change", "Internet access change", "R&D investment change"),
        )
        x_column = {
            "GDP change": "gdp_change",
            "Internet access change": "internet_change_pp",
            "R&D investment change": "rd_change_pp",
        }[indicator]
        snapshot = data.dropna(subset=[x_column, "output_change"])
        if snapshot.empty:
            empty_state()
        else:
            figure = px.scatter(
                snapshot,
                x=x_column,
                y="output_change",
                hover_name="country_name",
                color="income_level_name",
                title=f"AI research growth and {indicator.lower()}",
                color_discrete_sequence=PALETTE,
            )
            figure.update_layout(
                xaxis_title=indicator,
                yaxis_title="Change in fractional publications",
            )
            st.plotly_chart(apply_figure_style(figure, 500), width="stretch")
        st.caption(
            "Year-over-year differences are paired by country and adjacent year. "
            "Missing indicator pairs are excluded, not imputed."
        )
        return

    indicator_options = {
        "Wealth": (
            "GDP per capita (current USD)",
            "gdp_per_capita_current_usd",
            "GDP per capita and AI research intensity",
        ),
        "R&D investment": (
            "R&D expenditure (% of GDP)",
            "rd_expenditure_pct_gdp",
            "R&D investment and AI research intensity",
        ),
        "Digital access": (
            "Internet users (% of population)",
            "internet_users_pct",
            "Digital access and AI research intensity",
        ),
    }
    indicator_label, indicator, chart_title = indicator_options[navigation]
    data = numeric(
        run(queries.normalized_country_year(filters)),
        "full_publications",
        "fractional_publications",
        "gdp_per_capita_current_usd",
        "internet_users_pct",
        "rd_expenditure_pct_gdp",
        "publications_per_million",
    )
    if data.empty:
        empty_state()
        return
    available_years = sorted(data["calendar_year"].dropna().astype(int).unique())
    default_year = 2024 if 2024 in available_years else available_years[-1]
    analysis_year = st.selectbox(
        "Analysis year",
        available_years,
        index=available_years.index(default_year),
        key=f"{safe_file_stem(navigation)}_analysis_year",
    )
    snapshot = apply_publication_support(
        data[data["calendar_year"] == analysis_year], minimum_support
    )
    paired = snapshot.dropna(
        subset=[indicator, "publications_per_million"]
    ).copy()
    if paired.empty:
        empty_state()
        return
    paired["income_level_name"] = paired["income_level_name"].fillna(
        queries.MISSING_LABEL
    )
    figure = px.scatter(
        paired,
        x=indicator,
        y="publications_per_million",
        hover_name="country_name",
        color="income_level_name",
        title=f"{chart_title} — {analysis_year}",
        color_discrete_sequence=PALETTE,
        hover_data={
            "full_publications": ":,.0f",
            "fractional_publications": ":,.2f",
            "publications_per_million": ":.3f",
        },
    )
    logarithmic_x = navigation == "Wealth"
    figure.update_layout(
        xaxis_title=(
            f"{indicator_label} (log scale)" if logarithmic_x else indicator_label
        ),
        yaxis_title="Fractional publications per million inhabitants",
        legend_title_text="Income level",
    )
    if logarithmic_x:
        figure.update_xaxes(type="log")
    st.plotly_chart(
        apply_figure_style(figure, 500),
        width="stretch",
        config={"displaylogo": False},
    )
    st.caption(
        f"{len(paired)} countries have both measures after the visible publication-"
        "support threshold. Missing indicators are excluded from this chart only; "
        "associations do not establish causality."
    )
    with st.expander("Country data and CSV export"):
        compact_table(
            snapshot,
            key=f"{safe_file_stem(navigation)}_download",
            file_name=f"{safe_file_stem(navigation)}-research-intensity.csv",
        )
