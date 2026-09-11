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
          [data-testid="stSidebar"] {border-right: 1px solid rgba(38,55,70,.10);}
          [data-testid="stMetric"] {
            background: #f7f9fb; border: 1px solid rgba(38,55,70,.09);
            border-radius: .55rem; padding: .85rem 1rem;
          }
          [data-testid="stMetricLabel"] {color: #66788a;}
          [data-testid="stMetricValue"] {color: #16324f; font-size: clamp(1.25rem, 2.2vw, 2rem);}
          [data-testid="stMetricLabel"] p {white-space: normal; overflow: visible;}
          h1, h2, h3 {color: #16324f; letter-spacing: -.015em;}
          h1 {font-size: 2rem !important; margin-bottom: .1rem !important;}
          .observatory-subtitle {color:#66788a; font-size:1rem; margin-bottom:.7rem;}
          .context-line {color:#66788a; font-size:.88rem; margin:.1rem 0 1rem;}
          .stDownloadButton button {color:#2e6f9e;}
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
    if minimum_year == maximum_year:
        selected_years = (minimum_year, maximum_year)
        st.sidebar.caption(f"Publication year: {minimum_year}")
    else:
        selected_years = st.sidebar.slider(
            "Publication year range",
            minimum_year,
            maximum_year,
            (minimum_year, maximum_year),
        )

    countries = multiselect_filter(options, "country", "Country")
    regions = multiselect_filter(
        options,
        "region",
        "Region",
        help_text="Region and income level are alternative country attributes.",
    )
    income_levels = multiselect_filter(options, "income_level", "Income level")
    topics = multiselect_filter(options, "topic", "Topic")
    subfields = multiselect_filter(options, "subfield", "Subfield")
    hierarchy_filters = st.sidebar.expander("Topic hierarchy")
    fields = multiselect_filter(options, "field", "Field", container=hierarchy_filters)
    domains = multiselect_filter(options, "domain", "Domain", container=hierarchy_filters)

    more_filters = st.sidebar.expander("More filters")
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
    open_access_statuses = multiselect_filter(
        options,
        "open_access_status",
        "Open access status",
        container=more_filters,
    )

    st.sidebar.markdown("### Attribution")
    counting_method = st.sidebar.radio(
        "Counting method",
        ("Full", "Fractional"),
        horizontal=True,
        help=(
            "Full counting gives every participating country, topic, or institution "
            "one publication. Fractional counting divides one publication across "
            "all members of that relationship."
        ),
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
        open_access_statuses=open_access_statuses,
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
            open_access_statuses,
        )
    )
    if st.sidebar.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()
    st.sidebar.caption("Full: publication participation. Fractional: share across all source members. Weights are never rescaled after filtering.")
    return filters, counting_method, active_filters


def top_n_control(key: str, default: int = 15) -> int:
    values = (10, 15, 20, 30)
    return st.select_slider("Top N", values, value=default, key=key)


def metric_value(value, *, percent: bool = False) -> str:
    if pd.isna(value):
        return "Unavailable"
    number = float(value)
    return f"{number:,.1f}%" if percent else f"{number:,.0f}"


def render_overview(filters: queries.FilterState, counting_method: str) -> None:
    st.header("Overview")
    st.caption(
        "Executive view of the publications currently selected by the global filters."
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
        run(queries.topic_output(filters, counting_method, "Subfield", 12)),
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
                    title=f"Leading subfields · {counting_method.lower()} count",
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

    st.info(
        "The final adopted corpus uses Primary Topic membership in the AI subfield. "
        "Dashboard results reflect the dataset currently loaded in the warehouse.",
        icon="ℹ️",
    )


def render_evolution(filters: queries.FilterState) -> None:
    st.header("Research Growth")
    st.caption("Session 3 · How has output changed from 2018–2025? Explore years, then quarters or months; slice by publication type and access.")
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
    grain = st.radio("Time detail", ("Year", "Quarter", "Month"), horizontal=True)
    if grain != "Year":
        detail = numeric(run(queries.temporal_navigation(filters, grain)), "publications", "period_growth_pct")
        fig = px.line(detail, x="period", y="publications", markers=True, title=f"Publication output by {grain.lower()}")
        st.plotly_chart(apply_figure_style(fig), width="stretch")
        st.caption("Use the year filter to focus on 2022–2025. Period growth compares adjacent calendar periods.")
    figure = make_subplots(specs=[[{"secondary_y": True}]])
    figure.add_trace(
        go.Scatter(
            x=trend["calendar_year"],
            y=trend["publications"],
            name="Publications",
            mode="lines+markers",
            line=dict(color=BLUE, width=3),
        ),
        secondary_y=False,
    )
    figure.add_trace(
        go.Bar(
            x=trend["calendar_year"],
            y=trend["year_over_year_growth"],
            name="Year-over-year growth",
            marker_color="rgba(199,152,69,.45)",
        ),
        secondary_y=True,
    )
    figure.update_layout(title="Publication evolution and year-over-year growth")
    figure.update_yaxes(title_text="Publications", secondary_y=False)
    figure.update_yaxes(title_text="Growth (%)", secondary_y=True, showgrid=False)
    if trend["calendar_year"].min() <= 2022 <= trend["calendar_year"].max():
        figure.add_vline(x=2022, line_dash="dot", line_color="#66788A")
        figure.add_annotation(x=2022, y=1, yref="paper", text="2022", showarrow=False)
    st.plotly_chart(
        apply_figure_style(figure, 430),
        width="stretch",
        config={"displaylogo": False},
    )

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
            "Citation counts are cumulative values observed at OpenAlex extraction "
            "time, not citations received during that calendar year."
        )
    with right:
        if access.empty:
            empty_state()
        else:
            access_figure = px.area(
                access,
                x="calendar_year",
                y="publications",
                color="open_access_status",
                title="Open-access status over time",
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
        display = publication_types[publication_types["publication_type"].isin(leaders)]
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
        f"Publication participation using {counting_method.lower()} country attribution."
    )
    top_n = top_n_control("country_top_n")
    countries = numeric(
        run(queries.country_output(filters, counting_method, limit=500)), "publications"
    )
    if countries.empty:
        empty_state()
        return
    top = countries.head(top_n)
    left, right = st.columns((1, 1.2))
    with left:
        st.plotly_chart(
            horizontal_bar(
                top,
                x="publications",
                y="country_name",
                title=f"Top {top_n} countries",
                x_title="Attributed publications",
                hover_data={
                    "country_code_iso2": True,
                    "region_name": True,
                    "income_level_name": True,
                },
                height=500,
            ),
        width="stretch",
            config={"displaylogo": False},
        )
    with right:
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
            color_continuous_scale=["#E8EFF4", BLUE, NAVY],
            title="Geographic participation",
        )
        map_figure.update_geos(showframe=False, showcoastlines=True, coastlinecolor="#D2DAE1")
        map_figure.update_layout(coloraxis_colorbar_title="Publications")
        st.plotly_chart(
            apply_figure_style(map_figure, 500),
        width="stretch",
            config={"displaylogo": False},
        )
        st.caption(
            "Taiwan (TW/TWN) retains its warehouse identity; the map does not remap it."
        )

    regions = numeric(
        run(queries.regional_output(filters, counting_method)), "publications"
    )
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
            "Region and income level are alternative country attributes, not levels "
            "of a single hierarchy."
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
    st.caption("Session 1 · Does geographic leadership change after accounting for population and economic capacity?")
    st.info(
        "Normalized country metrics use fractional publication attribution regardless "
        f"of the global counting selector (currently {counting_method}).",
        icon="ℹ️",
    )
    data = numeric(
        run(queries.normalized_country_year(filters)),
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
    control_left, control_mid, control_right = st.columns((1, 2, 1))
    with control_left:
        analysis_year = st.selectbox(
            "Analysis year", available_years, index=len(available_years) - 1
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
    with control_right:
        top_n = top_n_control("normalized_top_n")
    metric = metric_labels[metric_label]
    snapshot = data[data["calendar_year"] == analysis_year]
    ranked = snapshot.dropna(subset=[metric]).nlargest(top_n, metric)

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
            scatter = px.scatter(
                scatter_data,
                x=context_column,
                y="publications_per_million",
                hover_name="country_name",
                color="region_name",
                title=f"Research intensity and {context_label.lower()} · {analysis_year}",
                color_discrete_sequence=PALETTE,
            )
            scatter.update_layout(
                xaxis_title=context_label,
                yaxis_title="Fractional publications per million inhabitants",
                showlegend=False,
            )
            st.plotly_chart(
                apply_figure_style(scatter, 480),
        width="stretch",
                config={"displaylogo": False},
            )

    country_options = (
        data[["country_code_iso2", "country_name"]]
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
    evolution = data[data["country_code_iso2"].isin(selected_countries)].dropna(
        subset=[metric]
    )
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

    ranks = run(queries.leadership_ranks(filters))
    ranks = ranks[ranks["calendar_year"] == analysis_year]
    st.subheader("Absolute and normalized ranks")
    compact_table(ranks[["country_name", "full_publications", "fractional_publications", "absolute_rank", "per_million_rank", "per_gdp_rank", "population_rank_shift", "gdp_rank_shift"]], key="rank_download", file_name="leadership-ranks.csv")
    st.caption("Positive rank shift means a higher normalized position. Unavailable denominators have no normalized rank.")

    display_columns = [
        "country_code_iso2",
        "country_name",
        "calendar_year",
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
        data[display_columns],
        key="download_normalized_country_year",
        file_name="normalized-country-year.csv",
        column_config={
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
    st.caption("Session 4 · Navigate Domain → Field → Subfield → Topic. All associated topics are retained, including secondary topics outside AI.")
    controls = st.columns((2, 1))
    with controls[0]:
        level = st.selectbox("Hierarchy level", queries.TOPIC_LEVEL_NAMES)
    with controls[1]:
        top_n = top_n_control("topic_top_n")
    ranking = numeric(
        run(queries.topic_output(filters, counting_method, level, top_n)),
        "publications",
    )
    evolution = numeric(
        run(queries.topic_evolution(filters, counting_method, level, 7)),
        "publications",
    )
    left, right = st.columns(2)
    with left:
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
    with right:
        if evolution.empty:
            empty_state()
        else:
            st.plotly_chart(
                line_chart(
                    evolution,
                    x="calendar_year",
                    y="publications",
                    color="member_name",
                    title=f"Leading {level.lower()}s over time",
                    y_title="Attributed publications",
                    height=500,
                ),
        width="stretch",
                config={"displaylogo": False},
            )
    if not ranking.empty:
        compact_table(
            ranking,
            key="download_topic_output",
            file_name=f"{safe_file_stem(level)}-output.csv",
            column_config={
                "publications": st.column_config.NumberColumn(format="%.3f")
            },
        )
    with st.expander("Topic hierarchy and counting"):
        st.write(
            "The warehouse hierarchy is Topic → Subfield → Field → Domain. Full "
            "counting counts each publication for every associated member; fractional "
            "counting uses the Topic bridge weight so each publication contributes one "
            "unit across all its Topic relationships. No AI macro-area classification "
            "is inferred in the dashboard."
        )


def render_institutions(filters: queries.FilterState, counting_method: str) -> None:
    st.header("Institutions")
    st.caption("Session 10 · Drill from institution type to institutions and their evolution over time.")
    st.caption(f"Institution participation using {counting_method.lower()} attribution.")
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
    left, right = st.columns((1.25, 0.75))
    with left:
        st.plotly_chart(
            horizontal_bar(
                institutions.head(top_n),
                x="publications",
                y="institution_name",
                title=f"Top {top_n} institutions",
                x_title="Attributed publications",
                height=500,
                hover_data={"institution_type": True, "country_code_iso2": True},
            ),
        width="stretch",
            config={"displaylogo": False},
        )
    with right:
        if not types.empty:
            st.plotly_chart(
                horizontal_bar(
                    types,
                    x="publications",
                    y="institution_type",
                    title="Institution types",
                    x_title="Attributed publications",
                    color=TEAL,
                    height=500,
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
    compact_table(
        institutions,
        key="download_institution_output",
        file_name="institution-output.csv",
        column_config={"publications": st.column_config.NumberColumn(format="%.3f")},
    )


def render_sources(filters: queries.FilterState) -> None:
    st.header("Publication Ecosystem")
    st.caption("Session 12 · Explore Source Type → Source, publication type, access status and language. Missing sources remain optional.")
    st.caption(
        "Source is the optional OpenAlex primary-location source; publication type is "
        "stored directly on the publication fact."
    )
    top_n = top_n_control("source_top_n")
    sources = numeric(run(queries.source_output(filters, top_n)), "publications")
    source_types = numeric(run(queries.source_type_output(filters)), "publications")
    publication_types = numeric(
        run(queries.publication_type_output(filters)), "publications"
    )
    type_time = numeric(
        run(queries.publication_type_by_year(filters)), "publications"
    )
    left, right = st.columns(2)
    with left:
        if sources.empty:
            empty_state()
        else:
            st.plotly_chart(
                horizontal_bar(
                    sources,
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
    st.caption("Session 11 · Cumulative citations observed at extraction. Older publications have had more time to accrue citations (publication-age bias).")
    st.caption(
        "Citation count is a cumulative snapshot observed at extraction time. It is "
        "not a count of citations received during the publication year."
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
    st.caption("Sessions 2, 5–9 · Compare research intensity with observed country conditions. Associations do not establish causality.")
    navigation = st.radio("Analytical path", ("Country indicators", "Income-level gap", "Regional capacity", "Changes over time"), horizontal=True)
    if navigation in ("Income-level gap", "Regional capacity"):
        hierarchy = "Income level" if navigation == "Income-level gap" else "Region"
        data = run(queries.country_group_capacity(filters, hierarchy))
        if data.empty:
            empty_state()
            return
        columns = ["full_publications", "fractional_publications", "population", "gdp_current_usd", "publications_per_million", "publications_per_billion_gdp"]
        data = numeric(data, *columns)
        labels = {"Full participation": "full_publications", "Fractional output": "fractional_publications", "Output per million inhabitants": "publications_per_million", "Output per billion USD GDP": "publications_per_billion_gdp"}
        metric = labels[st.selectbox("Measure", list(labels))]
        st.plotly_chart(line_chart(data, x="calendar_year", y=metric, color="member", title=f"{hierarchy} comparison", y_title=metric.replace("_", " ")), width="stretch")
        st.caption("Region and Income Level are alternative country roll-ups. Ratios include only countries with positive observed denominators in both sums. Coverage counts below show exclusions; population is never summed over time.")
        compact_table(data, key="capacity_download", file_name="country-group-capacity.csv")
        return
    if navigation == "Changes over time":
        data = numeric(run(queries.socioeconomic_change(filters)), "output_change", "intensity_change", "gdp_change", "internet_change_pp", "rd_change_pp")
        indicators = {"GDP change (current USD)": ("gdp_change", "output_change"), "Internet usage change (percentage points)": ("internet_change_pp", "intensity_change"), "R&D change (percentage points of GDP)": ("rd_change_pp", "intensity_change")}
        st.caption("Absolute changes avoid unstable percentage growth from tiny publication bases. Comparisons require adjacent calendar years; missing observations remain unavailable.")
    else:
        data = numeric(run(queries.normalized_country_year(filters)), "fractional_publications", "publications_per_million", "gdp_per_capita_current_usd", "internet_users_pct", "rd_expenditure_pct_gdp")
        indicators = {"R&D expenditure (% GDP)": ("rd_expenditure_pct_gdp", "publications_per_million"), "GDP per capita (current USD)": ("gdp_per_capita_current_usd", "publications_per_million"), "Internet users (%)": ("internet_users_pct", "publications_per_million")}
    if data.empty:
        empty_state()
        return
    years = sorted(data.calendar_year.unique())
    # Latest year with paired observations is a useful demo default, not imputation.
    label = st.selectbox("Indicator", list(indicators))
    x, y = indicators[label]
    usable = data.dropna(subset=[x, y])
    default_year = usable.calendar_year.max() if not usable.empty else years[-1]
    year = st.selectbox("Comparison year", years, index=years.index(default_year))
    snapshot = data[data.calendar_year == year]
    paired = snapshot.dropna(subset=[x, y]).copy()
    st.caption(f"{len(paired)} of {len(snapshot)} country observations have both measures; {len(snapshot) - len(paired)} unavailable pairs are omitted from the scatter only.")
    if paired.empty:
        empty_state("No paired observations for this indicator and year.")
    else:
        paired["region_name"] = paired["region_name"].fillna(queries.MISSING_LABEL)
        figure = px.scatter(paired, x=x, y=y, color="region_name", hover_name="country_name", title=f"{label} and research intensity · {year}", color_discrete_sequence=PALETTE)
        figure.update_layout(xaxis_title=label, yaxis_title="Fractional publications per million" if y == "publications_per_million" else y.replace("_", " "), showlegend=False)
        st.plotly_chart(apply_figure_style(figure, 490), width="stretch")
    compact_table(snapshot, key="context_download", file_name="socioeconomic-context.csv")
