"""Parameterized PostgreSQL queries at the warehouse's declared grains.

Global bridge filters are correlated ``EXISTS`` semi-joins. Analytical queries
then join only the single bridge they attribute through, so independent
many-to-many relationships can never multiply one another's measures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MISSING_VALUE = "__MISSING__"
MISSING_LABEL = "Missing / unavailable"


@dataclass(frozen=True)
class FilterState:
    start_year: int | None = None
    end_year: int | None = None
    countries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    income_levels: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    subfields: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    institution_types: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    publication_types: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    open_access_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuerySpec:
    sql: str
    params: tuple[Any, ...] = ()


def _nullable_predicate(
    column: str, selected: tuple[str, ...]
) -> tuple[str | None, list[Any]]:
    """Build a parameterized predicate that can explicitly select SQL NULL."""
    if not selected:
        return None, []
    include_missing = MISSING_VALUE in selected
    concrete = [value for value in selected if value != MISSING_VALUE]
    if include_missing and concrete:
        return f"({column} = ANY(%s) OR {column} IS NULL)", [concrete]
    if include_missing:
        return f"{column} IS NULL", []
    return f"{column} = ANY(%s)", [concrete]


def _append_nullable(
    clauses: list[str], params: list[Any], column: str, selected: tuple[str, ...]
) -> None:
    clause, values = _nullable_predicate(column, selected)
    if clause:
        clauses.append(clause)
        params.extend(values)


def _open_access_predicate(selected: tuple[str, ...]) -> tuple[str | None, list[Any]]:
    """Filter the nullable OpenAlex boolean without collapsing NULL into FALSE."""
    if not selected:
        return None, []
    unsupported = set(selected) - {"true", "false", MISSING_VALUE}
    if unsupported:
        raise ValueError("Unsupported Open Access filter value")
    include_missing = MISSING_VALUE in selected
    concrete = [value == "true" for value in selected if value != MISSING_VALUE]
    if include_missing and concrete:
        return "(publication.is_open_access = ANY(%s) OR publication.is_open_access IS NULL)", [concrete]
    if include_missing:
        return "publication.is_open_access IS NULL", []
    return "publication.is_open_access = ANY(%s)", [concrete]


def _country_member_filter(
    filters: FilterState, alias: str = "country"
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if filters.countries:
        clauses.append(f"{alias}.country_code_iso2 = ANY(%s)")
        params.append(list(filters.countries))
    _append_nullable(clauses, params, f"{alias}.region_name", filters.regions)
    _append_nullable(
        clauses, params, f"{alias}.income_level_name", filters.income_levels
    )
    return clauses, params


def _topic_member_filter(
    filters: FilterState, alias: str = "topic"
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if filters.topics:
        clauses.append(f"{alias}.openalex_topic_id = ANY(%s)")
        params.append(list(filters.topics))
    _append_nullable(clauses, params, f"{alias}.subfield_id", filters.subfields)
    _append_nullable(clauses, params, f"{alias}.field_id", filters.fields)
    _append_nullable(clauses, params, f"{alias}.domain_id", filters.domains)
    return clauses, params


def _institution_member_filter(
    filters: FilterState, alias: str = "institution"
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    _append_nullable(
        clauses,
        params,
        f"{alias}.institution_type",
        filters.institution_types,
    )
    return clauses, params


def filtered_publications_cte(filters: FilterState) -> QuerySpec:
    """Return a publication-grain CTE with safe cross-dimension filtering."""
    clauses: list[str] = []
    params: list[Any] = []

    if filters.start_year is not None:
        clauses.append("date_dim.calendar_year >= %s")
        params.append(filters.start_year)
    if filters.end_year is not None:
        clauses.append("date_dim.calendar_year <= %s")
        params.append(filters.end_year)

    country_clauses, country_params = _country_member_filter(filters)
    if country_clauses:
        clauses.append(
            "EXISTS ("
            "SELECT 1 FROM dw.bridge_publication_country country_bridge "
            "JOIN dw.dim_country country "
            "ON country.country_key = country_bridge.country_key "
            "WHERE country_bridge.publication_key = publication.publication_key "
            f"AND {' AND '.join(country_clauses)}"
            ")"
        )
        params.extend(country_params)

    topic_clauses, topic_params = _topic_member_filter(filters)
    if topic_clauses:
        clauses.append(
            "EXISTS ("
            "SELECT 1 FROM dw.bridge_publication_topic topic_bridge "
            "JOIN dw.dim_topic topic ON topic.topic_key = topic_bridge.topic_key "
            "WHERE topic_bridge.publication_key = publication.publication_key "
            f"AND {' AND '.join(topic_clauses)}"
            ")"
        )
        params.extend(topic_params)

    institution_clauses, institution_params = _institution_member_filter(filters)
    if institution_clauses:
        clauses.append(
            "EXISTS ("
            "SELECT 1 FROM dw.bridge_publication_institution institution_bridge "
            "JOIN dw.dim_institution institution "
            "ON institution.institution_key = institution_bridge.institution_key "
            "WHERE institution_bridge.publication_key = publication.publication_key "
            f"AND {' AND '.join(institution_clauses)}"
            ")"
        )
        params.extend(institution_params)

    _append_nullable(clauses, params, "source.source_type", filters.source_types)
    _append_nullable(
        clauses, params, "publication.publication_type", filters.publication_types
    )
    _append_nullable(clauses, params, "publication.language", filters.languages)
    open_access_clause, open_access_params = _open_access_predicate(
        filters.open_access_values
    )
    if open_access_clause:
        clauses.append(open_access_clause)
        params.extend(open_access_params)

    where_sql = " AND\n            ".join(clauses) if clauses else "TRUE"
    return QuerySpec(
        sql=f"""
filtered_publications AS (
    SELECT
        publication.publication_key,
        publication.source_key,
        publication.publication_type,
        publication.language,
        publication.is_open_access,
        publication.publication_count,
        publication.citation_count,
        date_dim.calendar_year,
        date_dim.full_date, date_dim.quarter_number, date_dim.month_number
    FROM dw.fact_publication publication
    LEFT JOIN dw.dim_date date_dim
        ON date_dim.date_key = publication.date_key
    LEFT JOIN dw.dim_source source
        ON source.source_key = publication.source_key
    WHERE {where_sql}
)""".strip(),
        params=tuple(params),
    )


def _with_filtered(filters: FilterState, body: str, params: list[Any] | None = None) -> QuerySpec:
    base = filtered_publications_cte(filters)
    return QuerySpec(
        sql=f"WITH {base.sql}\n{body.strip()}",
        params=base.params + tuple(params or ()),
    )


def warehouse_check() -> QuerySpec:
    return QuerySpec(
        """
        SELECT
            to_regclass('dw.fact_publication') IS NOT NULL AS publication_ready,
            to_regclass('dw.fact_country_year') IS NOT NULL AS country_year_ready
        """
    )


def year_bounds() -> QuerySpec:
    return QuerySpec(
        "SELECT min(calendar_year) AS min_year, max(calendar_year) AS max_year "
        "FROM dw.dim_date"
    )


def filter_options() -> QuerySpec:
    """Fetch all low-volume filter members in one cached round trip."""
    return QuerySpec(
        f"""
        SELECT filter_name, value, label
        FROM (
            SELECT 'country'::text AS filter_name,
                   country_code_iso2::text AS value,
                   concat(coalesce(country_name, country_code_iso2),
                          ' (', country_code_iso2, ')') AS label,
                   coalesce(country_name, country_code_iso2) AS sort_label
            FROM dw.dim_country
            UNION ALL
            SELECT 'field', field_id, max(field_name), max(field_name)
            FROM dw.dim_topic WHERE field_id IS NOT NULL GROUP BY field_id
            UNION ALL
            SELECT 'domain', domain_id, max(domain_name), max(domain_name)
            FROM dw.dim_topic WHERE domain_id IS NOT NULL GROUP BY domain_id
            UNION ALL
            SELECT 'region', region_name, region_name, region_name
            FROM (SELECT DISTINCT region_name FROM dw.dim_country
                  WHERE region_name IS NOT NULL) regions
            UNION ALL
            SELECT 'region', '{MISSING_VALUE}', '{MISSING_LABEL}', 'zzzz'
            WHERE EXISTS (SELECT 1 FROM dw.dim_country WHERE region_name IS NULL)
            UNION ALL
            SELECT 'income_level', income_level_name, income_level_name, income_level_name
            FROM (SELECT DISTINCT income_level_name FROM dw.dim_country
                  WHERE income_level_name IS NOT NULL) incomes
            UNION ALL
            SELECT 'income_level', '{MISSING_VALUE}', '{MISSING_LABEL}', 'zzzz'
            WHERE EXISTS (SELECT 1 FROM dw.dim_country WHERE income_level_name IS NULL)
            UNION ALL
            SELECT 'topic', openalex_topic_id,
                   concat(coalesce(topic_name, openalex_topic_id), ' · ',
                          coalesce(subfield_name, '{MISSING_LABEL}')),
                   coalesce(topic_name, openalex_topic_id)
            FROM dw.dim_topic
            UNION ALL
            SELECT 'subfield', subfield_id,
                   coalesce(subfield_name, subfield_id),
                   coalesce(subfield_name, subfield_id)
            FROM (
                SELECT DISTINCT subfield_id, subfield_name FROM dw.dim_topic
                WHERE subfield_id IS NOT NULL
            ) subfields
            UNION ALL
            SELECT 'subfield', '{MISSING_VALUE}', '{MISSING_LABEL}', 'zzzz'
            WHERE EXISTS (SELECT 1 FROM dw.dim_topic WHERE subfield_id IS NULL)
            UNION ALL
            SELECT 'institution_type', institution_type, institution_type, institution_type
            FROM (SELECT DISTINCT institution_type FROM dw.dim_institution
                  WHERE institution_type IS NOT NULL) institution_types
            UNION ALL
            SELECT 'institution_type', '{MISSING_VALUE}', '{MISSING_LABEL}', 'zzzz'
            WHERE EXISTS (SELECT 1 FROM dw.dim_institution WHERE institution_type IS NULL)
            UNION ALL
            SELECT 'source_type', source_type, source_type, source_type
            FROM (SELECT DISTINCT source_type FROM dw.dim_source
                  WHERE source_type IS NOT NULL) source_types
            UNION ALL
            SELECT 'source_type', '{MISSING_VALUE}', '{MISSING_LABEL}', 'zzzz'
            WHERE EXISTS (
                SELECT 1 FROM dw.fact_publication publication
                LEFT JOIN dw.dim_source source ON source.source_key = publication.source_key
                WHERE source.source_type IS NULL
            )
            UNION ALL
            SELECT 'publication_type', publication_type, publication_type, publication_type
            FROM (SELECT DISTINCT publication_type FROM dw.fact_publication
                  WHERE publication_type IS NOT NULL) publication_types
            UNION ALL
            SELECT 'publication_type', '{MISSING_VALUE}', '{MISSING_LABEL}', 'zzzz'
            WHERE EXISTS (SELECT 1 FROM dw.fact_publication WHERE publication_type IS NULL)
            UNION ALL
            SELECT 'language', language, language, language
            FROM (SELECT DISTINCT language FROM dw.fact_publication
                  WHERE language IS NOT NULL) languages
            UNION ALL
            SELECT 'language', '{MISSING_VALUE}', '{MISSING_LABEL}', 'zzzz'
            WHERE EXISTS (SELECT 1 FROM dw.fact_publication WHERE language IS NULL)
            UNION ALL
            SELECT 'open_access', is_open_access::text,
                   CASE WHEN is_open_access THEN 'Open Access'
                        ELSE 'Not Open Access' END,
                   CASE WHEN is_open_access THEN '1' ELSE '2' END
            FROM (SELECT DISTINCT is_open_access FROM dw.fact_publication
                  WHERE is_open_access IS NOT NULL) access_values
            UNION ALL
            SELECT 'open_access', '{MISSING_VALUE}', '{MISSING_LABEL}', 'zzzz'
            WHERE EXISTS (SELECT 1 FROM dw.fact_publication
                          WHERE is_open_access IS NULL)
        ) options
        ORDER BY filter_name, sort_label, value
        """
    )


def overview_metrics(filters: FilterState) -> QuerySpec:
    base = filtered_publications_cte(filters)
    country_clauses, country_params = _country_member_filter(filters)
    topic_clauses, topic_params = _topic_member_filter(filters)
    institution_clauses, institution_params = _institution_member_filter(filters)
    country_where = " AND ".join(country_clauses) if country_clauses else "TRUE"
    topic_where = " AND ".join(topic_clauses) if topic_clauses else "TRUE"
    institution_where = (
        " AND ".join(institution_clauses) if institution_clauses else "TRUE"
    )
    return QuerySpec(
        f"""
        WITH {base.sql}
        SELECT
            coalesce(sum(publication_count), 0)::numeric AS publications,
            sum(citation_count)::numeric AS cumulative_citations,
            (SELECT count(DISTINCT bridge.country_key)
             FROM filtered_publications selected
             JOIN dw.bridge_publication_country bridge
               ON bridge.publication_key = selected.publication_key
             JOIN dw.dim_country country ON country.country_key = bridge.country_key
             WHERE {country_where}) AS countries,
            (SELECT count(DISTINCT bridge.institution_key)
             FROM filtered_publications selected
             JOIN dw.bridge_publication_institution bridge
               ON bridge.publication_key = selected.publication_key
             JOIN dw.dim_institution institution
               ON institution.institution_key = bridge.institution_key
             WHERE {institution_where}) AS institutions,
            (SELECT count(DISTINCT bridge.topic_key)
             FROM filtered_publications selected
             JOIN dw.bridge_publication_topic bridge
               ON bridge.publication_key = selected.publication_key
             JOIN dw.dim_topic topic ON topic.topic_key = bridge.topic_key
             WHERE {topic_where}) AS topics,
            avg(CASE WHEN is_open_access IS NULL THEN NULL
                     ELSE is_open_access::integer END) * 100 AS open_access_share
        FROM filtered_publications
        """,
        base.params
        + tuple(country_params)
        + tuple(institution_params)
        + tuple(topic_params),
    )


def publications_by_year(filters: FilterState) -> QuerySpec:
    return _with_filtered(
        filters,
        """
        SELECT calendar_year,
               sum(publication_count)::numeric AS publications,
               sum(citation_count)::numeric AS cumulative_citations,
               avg(citation_count)::numeric AS average_citations
        FROM filtered_publications
        GROUP BY calendar_year
        ORDER BY calendar_year
        """,
    )


def open_access_by_year(filters: FilterState) -> QuerySpec:
    return _with_filtered(
        filters,
        f"""
        SELECT calendar_year,
               CASE WHEN is_open_access THEN 'Open Access'
                    WHEN is_open_access IS FALSE THEN 'Not Open Access'
                    ELSE '{MISSING_LABEL}' END AS open_access,
               sum(publication_count)::numeric AS publications
        FROM filtered_publications
        GROUP BY calendar_year, is_open_access
        ORDER BY calendar_year, is_open_access DESC NULLS LAST
        """,
    )


def publication_type_by_year(filters: FilterState) -> QuerySpec:
    return _with_filtered(
        filters,
        f"""
        SELECT calendar_year,
               coalesce(publication_type, '{MISSING_LABEL}') AS publication_type,
               sum(publication_count)::numeric AS publications
        FROM filtered_publications
        GROUP BY calendar_year, coalesce(publication_type, '{MISSING_LABEL}')
        ORDER BY calendar_year, publication_type
        """,
    )


def _count_expression(counting_method: str, bridge_alias: str = "bridge") -> str:
    if counting_method == "Full":
        return "sum(selected.publication_count)::numeric"
    if counting_method == "Fractional":
        return (
            f"sum(selected.publication_count * {bridge_alias}.fractional_weight)::numeric"
        )
    raise ValueError(f"Unsupported counting method: {counting_method}")


def country_output(
    filters: FilterState, counting_method: str, limit: int = 500
) -> QuerySpec:
    base = filtered_publications_cte(filters)
    member_clauses, member_params = _country_member_filter(filters)
    where_sql = " AND ".join(member_clauses) if member_clauses else "TRUE"
    metric = _count_expression(counting_method)
    return QuerySpec(
        f"""
        WITH {base.sql}
        SELECT country.country_code_iso2,
               country.country_code_iso3,
               coalesce(country.country_name, country.country_code_iso2) AS country_name,
               country.region_name,
               country.income_level_name,
               country.has_world_bank_data,
               {metric} AS publications
        FROM filtered_publications selected
        JOIN dw.bridge_publication_country bridge
          ON bridge.publication_key = selected.publication_key
        JOIN dw.dim_country country ON country.country_key = bridge.country_key
        WHERE {where_sql}
        GROUP BY country.country_key, country.country_code_iso2,
                 country.country_code_iso3, country.country_name,
                 country.region_name, country.income_level_name,
                 country.has_world_bank_data
        ORDER BY publications DESC NULLS LAST, country_name
        LIMIT %s
        """,
        base.params + tuple(member_params) + (limit,),
    )


def regional_output(filters: FilterState, counting_method: str) -> QuerySpec:
    result = hierarchy_output(filters, counting_method, "country", "Region", limit=50)
    return QuerySpec(f"SELECT member_name AS region, publications FROM ({result.sql}) result", result.params)


def normalized_country_year(filters: FilterState) -> QuerySpec:
    """Drill across only after fractional publications reach Country x Year."""
    base = filtered_publications_cte(filters)
    member_clauses, member_params = _country_member_filter(filters)
    where_sql = " AND ".join(member_clauses) if member_clauses else "TRUE"
    return QuerySpec(
        f"""
        WITH {base.sql},
        publication_country_year AS (
            SELECT country.country_key,
                   country.country_code_iso2,
                   country.country_code_iso3,
                   coalesce(country.country_name, country.country_code_iso2) AS country_name,
                   country.region_name,
                   country.income_level_name,
                   country.has_world_bank_data,
                   selected.calendar_year,
                   sum(selected.publication_count) AS full_publications,
                   sum(selected.publication_count * bridge.fractional_weight)::numeric
                       AS fractional_publications
            FROM filtered_publications selected
            JOIN dw.bridge_publication_country bridge
              ON bridge.publication_key = selected.publication_key
            JOIN dw.dim_country country ON country.country_key = bridge.country_key
            WHERE {where_sql}
            GROUP BY country.country_key, country.country_code_iso2,
                     country.country_code_iso3, country.country_name,
                     country.region_name, country.income_level_name,
                     country.has_world_bank_data, selected.calendar_year
        )
        SELECT publication.country_code_iso2,
               publication.country_code_iso3,
               publication.country_name,
               publication.region_name,
               publication.income_level_name,
               publication.has_world_bank_data,
               publication.calendar_year,
               publication.fractional_publications,
               indicator.population,
               indicator.gdp_current_usd,
               indicator.gdp_per_capita_current_usd,
               indicator.internet_users_pct,
               indicator.rd_expenditure_pct_gdp,
               CASE WHEN indicator.population IS NULL OR indicator.population = 0
                    THEN NULL
                    ELSE publication.fractional_publications * 1000000::numeric
                         / indicator.population
               END AS publications_per_million,
               CASE WHEN indicator.gdp_current_usd IS NULL OR indicator.gdp_current_usd = 0
                    THEN NULL
                    ELSE publication.fractional_publications * 1000000000::numeric
                         / indicator.gdp_current_usd
               END AS publications_per_billion_gdp,
               publication.full_publications
        FROM publication_country_year publication
        LEFT JOIN dw.fact_country_year indicator
          ON indicator.country_key = publication.country_key
         AND indicator.year = publication.calendar_year
        ORDER BY publication.calendar_year, publication.country_name
        """,
        base.params + tuple(member_params),
    )


_TOPIC_LEVELS = {
    "Topic": ("topic.openalex_topic_id", "topic.topic_name"),
    "Subfield": ("topic.subfield_id", "topic.subfield_name"),
    "Field": ("topic.field_id", "topic.field_name"),
    "Domain": ("topic.domain_id", "topic.domain_name"),
}
TOPIC_LEVEL_NAMES = tuple(_TOPIC_LEVELS)


def topic_output(filters: FilterState, counting_method: str,
                 level: str = "Topic", limit: int = 30) -> QuerySpec:
    return hierarchy_output(filters, counting_method, "topic", level, limit=limit)


def topic_evolution(filters: FilterState, counting_method: str,
                    level: str = "Subfield", limit: int = 8) -> QuerySpec:
    return hierarchy_output(filters, counting_method, "topic", level, limit=limit, evolution=True)


def institution_output(
    filters: FilterState, counting_method: str, limit: int = 100
) -> QuerySpec:
    base = filtered_publications_cte(filters)
    member_clauses, member_params = _institution_member_filter(filters)
    where_sql = " AND ".join(member_clauses) if member_clauses else "TRUE"
    metric = _count_expression(counting_method)
    return QuerySpec(
        f"""
        WITH {base.sql}
        SELECT institution.openalex_institution_id AS institution_id,
               coalesce(institution.institution_name,
                        institution.openalex_institution_id) AS institution_name,
               institution.institution_type,
               institution.country_code_iso2,
               {metric} AS publications
        FROM filtered_publications selected
        JOIN dw.bridge_publication_institution bridge
          ON bridge.publication_key = selected.publication_key
        JOIN dw.dim_institution institution
          ON institution.institution_key = bridge.institution_key
        WHERE {where_sql}
        GROUP BY institution.institution_key, institution.openalex_institution_id,
                 institution.institution_name, institution.institution_type,
                 institution.country_code_iso2
        ORDER BY publications DESC NULLS LAST, institution_name
        LIMIT %s
        """,
        base.params + tuple(member_params) + (limit,),
    )


def institution_type_output(filters: FilterState, counting_method: str) -> QuerySpec:
    result = hierarchy_output(filters, counting_method, "institution", "Type", limit=50)
    return QuerySpec(f"SELECT member_name AS institution_type, publications FROM ({result.sql}) result", result.params)


def institution_evolution(
    filters: FilterState, counting_method: str, institution_ids: tuple[str, ...]
) -> QuerySpec:
    if not institution_ids:
        raise ValueError("At least one institution is required")
    base = filtered_publications_cte(filters)
    member_clauses, member_params = _institution_member_filter(filters)
    member_clauses.append("institution.openalex_institution_id = ANY(%s)")
    member_params.append(list(institution_ids))
    metric = _count_expression(counting_method)
    return QuerySpec(
        f"""
        WITH {base.sql}
        SELECT institution.openalex_institution_id AS institution_id,
               coalesce(institution.institution_name,
                        institution.openalex_institution_id) AS institution_name,
               selected.calendar_year,
               {metric} AS publications
        FROM filtered_publications selected
        JOIN dw.bridge_publication_institution bridge
          ON bridge.publication_key = selected.publication_key
        JOIN dw.dim_institution institution
          ON institution.institution_key = bridge.institution_key
        WHERE {' AND '.join(member_clauses)}
        GROUP BY institution.openalex_institution_id,
                 institution.institution_name, selected.calendar_year
        ORDER BY selected.calendar_year, institution_name
        """,
        base.params + tuple(member_params),
    )


def source_output(filters: FilterState, limit: int = 30) -> QuerySpec:
    return _with_filtered(
        filters,
        f"""
        SELECT coalesce(source.openalex_source_id, '{MISSING_VALUE}') AS source_id,
               coalesce(source.source_name, '{MISSING_LABEL}') AS source_name,
               coalesce(source.source_type, '{MISSING_LABEL}') AS source_type,
               sum(selected.publication_count)::numeric AS publications
        FROM filtered_publications selected
        LEFT JOIN dw.dim_source source ON source.source_key = selected.source_key
        GROUP BY source.openalex_source_id, source.source_name, source.source_type
        ORDER BY publications DESC NULLS LAST, source_name
        LIMIT %s
        """,
        [limit],
    )


def source_type_output(filters: FilterState) -> QuerySpec:
    return _with_filtered(
        filters,
        f"""
        SELECT coalesce(source.source_type, '{MISSING_LABEL}') AS source_type,
               sum(selected.publication_count)::numeric AS publications
        FROM filtered_publications selected
        LEFT JOIN dw.dim_source source ON source.source_key = selected.source_key
        GROUP BY coalesce(source.source_type, '{MISSING_LABEL}')
        ORDER BY publications DESC NULLS LAST, source_type
        """,
    )


def publication_type_output(filters: FilterState) -> QuerySpec:
    return _with_filtered(
        filters,
        f"""
        SELECT coalesce(publication_type, '{MISSING_LABEL}') AS publication_type,
               sum(publication_count)::numeric AS publications
        FROM filtered_publications
        GROUP BY coalesce(publication_type, '{MISSING_LABEL}')
        ORDER BY publications DESC NULLS LAST, publication_type
        """,
    )


def citation_distribution(filters: FilterState) -> QuerySpec:
    return _with_filtered(
        filters,
        """
        SELECT citation_band,
               count(*)::numeric AS publications,
               band_order
        FROM (
            SELECT CASE
                     WHEN citation_count IS NULL THEN 'Unavailable'
                     WHEN citation_count = 0 THEN '0'
                     WHEN citation_count BETWEEN 1 AND 5 THEN '1–5'
                     WHEN citation_count BETWEEN 6 AND 20 THEN '6–20'
                     WHEN citation_count BETWEEN 21 AND 100 THEN '21–100'
                     ELSE '101+'
                   END AS citation_band,
                   CASE
                     WHEN citation_count IS NULL THEN 6
                     WHEN citation_count = 0 THEN 1
                     WHEN citation_count BETWEEN 1 AND 5 THEN 2
                     WHEN citation_count BETWEEN 6 AND 20 THEN 3
                     WHEN citation_count BETWEEN 21 AND 100 THEN 4
                     ELSE 5
                   END AS band_order
            FROM filtered_publications
        ) binned
        GROUP BY citation_band, band_order
        ORDER BY band_order
        """,
    )


def citation_ranking(
    filters: FilterState,
    dimension: str,
    counting_method: str,
    limit: int = 20,
) -> QuerySpec:
    """Attribute citation snapshots through at most one selected dimension."""
    if dimension in {"Publication type", "Source type"}:
        if dimension == "Publication type":
            join_sql = ""
            key_expression = "selected.publication_type"
            label_expression = (
                f"coalesce(selected.publication_type, '{MISSING_LABEL}')"
            )
        else:
            join_sql = (
                "LEFT JOIN dw.dim_source source "
                "ON source.source_key = selected.source_key"
            )
            key_expression = "source.source_type"
            label_expression = f"coalesce(source.source_type, '{MISSING_LABEL}')"
        return _with_filtered(
            filters,
            f"""
            SELECT {label_expression} AS member_name,
                   sum(selected.citation_count)::numeric AS cumulative_citations,
                   avg(selected.citation_count)::numeric AS average_citations,
                   sum(selected.publication_count)::numeric AS publications
            FROM filtered_publications selected
            {join_sql}
            GROUP BY {key_expression}
            ORDER BY cumulative_citations DESC NULLS LAST, member_name
            LIMIT %s
            """,
            [limit],
        )

    bridge_config = {
        "Country": (
            "dw.bridge_publication_country",
            "dw.dim_country",
            "country_key",
            "country",
            "coalesce(country.country_name, country.country_code_iso2)",
            _country_member_filter,
        ),
        "Topic": (
            "dw.bridge_publication_topic",
            "dw.dim_topic",
            "topic_key",
            "topic",
            "coalesce(topic.topic_name, topic.openalex_topic_id)",
            _topic_member_filter,
        ),
        "Institution": (
            "dw.bridge_publication_institution",
            "dw.dim_institution",
            "institution_key",
            "institution",
            "coalesce(institution.institution_name, institution.openalex_institution_id)",
            _institution_member_filter,
        ),
    }
    if dimension not in bridge_config:
        raise ValueError(f"Unsupported citation dimension: {dimension}")
    bridge_table, dimension_table, key, alias, label, filter_builder = bridge_config[
        dimension
    ]
    base = filtered_publications_cte(filters)
    member_clauses, member_params = filter_builder(filters, alias)
    where_sql = " AND ".join(member_clauses) if member_clauses else "TRUE"
    citation_expression = (
        "sum(selected.citation_count)::numeric"
        if counting_method == "Full"
        else "sum(selected.citation_count * bridge.fractional_weight)::numeric"
    )
    publication_expression = _count_expression(counting_method)
    return QuerySpec(
        f"""
        WITH {base.sql}
        SELECT {label} AS member_name,
               {citation_expression} AS cumulative_citations,
               {publication_expression} AS publications
        FROM filtered_publications selected
        JOIN {bridge_table} bridge
          ON bridge.publication_key = selected.publication_key
        JOIN {dimension_table} {alias} ON {alias}.{key} = bridge.{key}
        WHERE {where_sql}
        GROUP BY {alias}.{key}, {label}
        ORDER BY cumulative_citations DESC NULLS LAST, member_name
        LIMIT %s
        """,
        base.params + tuple(member_params) + (limit,),
    )


def hierarchy_output(filters: FilterState, counting_method: str, dimension: str,
                     level: str, limit: int = 30, evolution: bool = False) -> QuerySpec:
    """Reduce to Publication x hierarchy member before full participation counting.

    Fractional roll-ups sum existing leaf weights; they never renormalize after
    filtering. Two topics in one subfield count once in Full mode.
    """
    _count_expression(counting_method)  # Validate the public selector.
    definitions = {
        "topic": (_TOPIC_LEVELS, _topic_member_filter, "topic"),
        "country": ({"Region": ("country.region_name", "country.region_name"),
                     "Income level": ("country.income_level_name", "country.income_level_name")},
                    _country_member_filter, "country"),
        "institution": ({"Type": ("institution.institution_type", "institution.institution_type")},
                        _institution_member_filter, "institution"),
    }
    if dimension not in definitions:
        raise ValueError("Unsupported hierarchy dimension")
    levels, member_filter, alias = definitions[dimension]
    if level not in levels:
        raise ValueError(f"Unsupported {dimension} hierarchy level: {level}")
    key, name = levels[level]
    clauses, params = member_filter(filters)
    base = filtered_publications_cte(filters)
    weight = "1::numeric" if counting_method == "Full" else "sum(bridge.fractional_weight)"
    grouping = ", calendar_year" if evolution else ""
    projection = ", member_year.calendar_year" if evolution else ""
    return QuerySpec(f"""
        WITH {base.sql}, publication_member AS (
            SELECT selected.publication_key, selected.calendar_year,
                   coalesce({key}, '{MISSING_VALUE}') AS member_id,
                   coalesce({name}, {key}, '{MISSING_LABEL}') AS member_name,
                   {weight} AS attributed_publications
            FROM filtered_publications selected
            JOIN dw.bridge_publication_{dimension} bridge USING (publication_key)
            JOIN dw.dim_{dimension} {alias} USING ({dimension}_key)
            WHERE {' AND '.join(clauses) if clauses else 'TRUE'}
            GROUP BY selected.publication_key, selected.calendar_year, {key}, {name}
        ), member_year AS (
            SELECT member_id, member_name{grouping}, sum(attributed_publications) AS publications
            FROM publication_member GROUP BY member_id, member_name{grouping}
        ), leaders AS (
            SELECT member_id FROM member_year GROUP BY member_id
            ORDER BY sum(publications) DESC, member_id LIMIT %s
        )
        SELECT member_year.member_id, member_year.member_name{projection}, member_year.publications
        FROM member_year JOIN leaders USING (member_id)
        ORDER BY {'calendar_year,' if evolution else ''} publications DESC, member_name
    """, base.params + tuple(params) + (limit,))


def temporal_navigation(filters: FilterState, grain: str = "Year") -> QuerySpec:
    """Calendar spine avoids comparing non-adjacent observed years as YoY."""
    if grain not in ("Year", "Quarter", "Month"):
        raise ValueError("Unsupported time grain")
    unit = grain.lower()
    return _with_filtered(filters, f"""
        , bounds AS (
            SELECT min(full_date) AS first_date, max(full_date) AS last_date
            FROM filtered_publications
        ), periods AS (
            SELECT generate_series(date_trunc('{unit}', first_date),
                                   date_trunc('{unit}', last_date), interval '{'3 months' if grain == 'Quarter' else '1 ' + unit}')::date AS period
            FROM bounds
        ), counts AS (
            SELECT date_trunc('{unit}', full_date)::date AS period,
                   sum(publication_count) AS publications
            FROM filtered_publications GROUP BY 1
        ), series AS (
            SELECT period, coalesce(publications, 0) AS publications
            FROM periods LEFT JOIN counts USING (period)
        )
        SELECT period, publications,
               100.0 * (publications - lag(publications) OVER (ORDER BY period))
                   / nullif(lag(publications) OVER (ORDER BY period), 0) AS period_growth_pct
        FROM series ORDER BY period
    """)


def leadership_ranks(filters: FilterState) -> QuerySpec:
    base = normalized_country_year(filters)
    return QuerySpec(f"""
        WITH country_year AS ({base.sql}), ranked AS (
            SELECT *, rank() OVER (PARTITION BY calendar_year ORDER BY fractional_publications DESC) AS absolute_rank,
                   CASE WHEN publications_per_million IS NOT NULL THEN
                       rank() OVER (PARTITION BY calendar_year ORDER BY publications_per_million DESC NULLS LAST)
                   END AS per_million_rank,
                   CASE WHEN publications_per_billion_gdp IS NOT NULL THEN
                       rank() OVER (PARTITION BY calendar_year ORDER BY publications_per_billion_gdp DESC NULLS LAST)
                   END AS per_gdp_rank
            FROM country_year
        )
        SELECT *, absolute_rank - per_million_rank AS population_rank_shift,
                  absolute_rank - per_gdp_rank AS gdp_rank_shift
        FROM ranked ORDER BY calendar_year, absolute_rank, country_code_iso2
    """, base.params)


def socioeconomic_change(filters: FilterState) -> QuerySpec:
    base = normalized_country_year(filters)
    return QuerySpec(f"""
        WITH country_year AS ({base.sql})
        SELECT current.country_name, current.region_name, current.income_level_name,
               current.calendar_year,
               current.fractional_publications - previous.fractional_publications AS output_change,
               current.publications_per_million - previous.publications_per_million AS intensity_change,
               current.gdp_current_usd - previous.gdp_current_usd AS gdp_change,
               current.internet_users_pct - previous.internet_users_pct AS internet_change_pp,
               current.rd_expenditure_pct_gdp - previous.rd_expenditure_pct_gdp AS rd_change_pp
        FROM country_year current
        LEFT JOIN country_year previous ON previous.country_code_iso2 = current.country_code_iso2
             AND previous.calendar_year = current.calendar_year - 1
        ORDER BY current.calendar_year, current.country_name
    """, base.params)


def country_group_capacity(filters: FilterState, hierarchy: str = "Region") -> QuerySpec:
    """Country x Year then independent Region/Income roll-up.

    Denominators include ALL selected WDI economies, even zero-output countries.
    Ratios use matched positive-denominator countries for numerator and
    denominator; coverage columns make exclusions explicit. No temporal sums of
    population and no sums of per-capita/percentage indicators.
    """
    if hierarchy not in ("Region", "Income level"):
        raise ValueError("Unsupported country hierarchy")
    attribute = "region_name" if hierarchy == "Region" else "income_level_name"
    base = filtered_publications_cte(filters)
    clauses, params = _country_member_filter(filters)
    year_clauses = []
    if filters.start_year is not None:
        year_clauses.append("year_coordinate.calendar_year >= %s")
        params.append(filters.start_year)
    if filters.end_year is not None:
        year_clauses.append("year_coordinate.calendar_year <= %s")
        params.append(filters.end_year)
    where = ' AND '.join(clauses + year_clauses) or 'TRUE'
    return QuerySpec(f"""
        WITH {base.sql}, year_coordinates AS (
            SELECT DISTINCT year AS calendar_year FROM dw.fact_country_year
        ), country_output AS (
            SELECT country_key, calendar_year, sum(fractional_weight) AS fractional_publications
            FROM filtered_publications JOIN dw.bridge_publication_country USING (publication_key)
            GROUP BY country_key, calendar_year
        ), country_year AS (
            SELECT country.country_key, country.{attribute} AS member,
                   year_coordinate.calendar_year, coalesce(output.fractional_publications, 0) AS fractional_publications,
                   indicator.population, indicator.gdp_current_usd
            FROM dw.dim_country country CROSS JOIN year_coordinates year_coordinate
            LEFT JOIN country_output output ON output.country_key = country.country_key
                AND output.calendar_year = year_coordinate.calendar_year
            LEFT JOIN dw.fact_country_year indicator ON indicator.country_key = country.country_key
                AND indicator.year = year_coordinate.calendar_year
            WHERE {where}
        ), group_publication AS (
            SELECT country.{attribute} AS member, selected.calendar_year, selected.publication_key
            FROM filtered_publications selected
            JOIN dw.bridge_publication_country bridge USING (publication_key)
            JOIN dw.dim_country country USING (country_key)
            WHERE {' AND '.join(clauses) if clauses else 'TRUE'}
            GROUP BY country.{attribute}, selected.calendar_year, selected.publication_key
        ), full_output AS (
            SELECT member, calendar_year, count(*) AS full_publications
            FROM group_publication GROUP BY member, calendar_year
        )
        SELECT coalesce(c.member, '{MISSING_LABEL}') AS member, c.calendar_year,
               coalesce(f.full_publications, 0) AS full_publications,
               sum(c.fractional_publications) AS fractional_publications,
               sum(c.population) AS population, sum(c.gdp_current_usd) AS gdp_current_usd,
               1000000::numeric * sum(c.fractional_publications) FILTER (WHERE c.population > 0)
                 / nullif(sum(c.population) FILTER (WHERE c.population > 0), 0) AS publications_per_million,
               1000000000::numeric * sum(c.fractional_publications) FILTER (WHERE c.gdp_current_usd > 0)
                 / nullif(sum(c.gdp_current_usd) FILTER (WHERE c.gdp_current_usd > 0), 0) AS publications_per_billion_gdp,
               count(*) AS countries,
               count(*) FILTER (WHERE c.population > 0) AS population_countries,
               count(*) FILTER (WHERE c.gdp_current_usd > 0) AS gdp_countries
        FROM country_year c LEFT JOIN full_output f
          ON f.member IS NOT DISTINCT FROM c.member AND f.calendar_year = c.calendar_year
        GROUP BY c.member, c.calendar_year, f.full_publications
        ORDER BY c.calendar_year, fractional_publications DESC, member
    """, base.params + tuple(params) + tuple(_country_member_filter(filters)[1]))
