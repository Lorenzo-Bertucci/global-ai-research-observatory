-- OLAP SESSION 02 — GEOGRAPHIC LEADERSHIP
-- Analytical question:
-- How does country leadership change when moving from
-- Full counting to Fractional counting?

BEGIN TRANSACTION READ ONLY;


-- QUERY 1 — FULL COUNTING BY COUNTRY × YEAR
WITH selected_publications AS (

    SELECT
        publication.publication_key,
        publication.publication_count,
        date_dim.calendar_year

    FROM dw.fact_publication AS publication

    JOIN dw.dim_date AS date_dim
        ON date_dim.date_key = publication.date_key

    WHERE date_dim.calendar_year BETWEEN 2018 AND 2025
),

country_year AS (

    SELECT
        selected.calendar_year,

        country.country_key,
        country.country_code_iso2,
        country.country_code_iso3,

        COALESCE(
            country.country_name,
            country.country_code_iso2
        ) AS country_name,

        country.region_name,
        country.income_level_name,

        SUM(
            selected.publication_count
        )::numeric AS full_publications

    FROM selected_publications AS selected

    JOIN dw.bridge_publication_country AS bridge
        ON bridge.publication_key = selected.publication_key

    JOIN dw.dim_country AS country
        ON country.country_key = bridge.country_key

    GROUP BY
        selected.calendar_year,
        country.country_key,
        country.country_code_iso2,
        country.country_code_iso3,
        country.country_name,
        country.region_name,
        country.income_level_name
),

ranked AS (

    SELECT
        *,

        RANK() OVER (
            PARTITION BY calendar_year
            ORDER BY full_publications DESC
        ) AS full_rank

    FROM country_year
)

SELECT
    calendar_year,

    country_code_iso2,
    country_code_iso3,
    country_name,
    region_name,
    income_level_name,

    full_publications,
    full_rank

FROM ranked

ORDER BY
    calendar_year,
    full_rank,
    country_name;


-- QUERY 2 — FRACTIONAL COUNTING BY COUNTRY × YEAR


WITH selected_publications AS (

    SELECT
        publication.publication_key,
        publication.publication_count,
        date_dim.calendar_year

    FROM dw.fact_publication AS publication

    JOIN dw.dim_date AS date_dim
        ON date_dim.date_key = publication.date_key

    WHERE date_dim.calendar_year BETWEEN 2018 AND 2025
),

country_year AS (

    SELECT
        selected.calendar_year,

        country.country_key,
        country.country_code_iso2,
        country.country_code_iso3,

        COALESCE(
            country.country_name,
            country.country_code_iso2
        ) AS country_name,

        country.region_name,
        country.income_level_name,

        SUM(
            selected.publication_count
            * bridge.fractional_weight
        )::numeric AS fractional_publications

    FROM selected_publications AS selected

    JOIN dw.bridge_publication_country AS bridge
        ON bridge.publication_key = selected.publication_key

    JOIN dw.dim_country AS country
        ON country.country_key = bridge.country_key

    GROUP BY
        selected.calendar_year,
        country.country_key,
        country.country_code_iso2,
        country.country_code_iso3,
        country.country_name,
        country.region_name,
        country.income_level_name
),

ranked AS (

    SELECT
        *,

        RANK() OVER (
            PARTITION BY calendar_year
            ORDER BY fractional_publications DESC
        ) AS fractional_rank

    FROM country_year
)

SELECT
    calendar_year,

    country_code_iso2,
    country_code_iso3,
    country_name,
    region_name,
    income_level_name,

    fractional_publications,
    fractional_rank

FROM ranked

ORDER BY
    calendar_year,
    fractional_rank,
    country_name;


-- QUERY 3 — DIRECT FULL vs FRACTIONAL COMPARISON

WITH selected_publications AS (

    SELECT
        publication.publication_key,
        publication.publication_count,
        date_dim.calendar_year

    FROM dw.fact_publication AS publication

    JOIN dw.dim_date AS date_dim
        ON date_dim.date_key = publication.date_key

    WHERE date_dim.calendar_year BETWEEN 2018 AND 2025
),

country_output AS (

    SELECT
        selected.calendar_year,

        country.country_key,
        country.country_code_iso2,
        country.country_code_iso3,

        COALESCE(
            country.country_name,
            country.country_code_iso2
        ) AS country_name,

        country.region_name,
        country.income_level_name,

        SUM(
            selected.publication_count
        )::numeric AS full_publications,

        SUM(
            selected.publication_count
            * bridge.fractional_weight
        )::numeric AS fractional_publications

    FROM selected_publications AS selected

    JOIN dw.bridge_publication_country AS bridge
        ON bridge.publication_key = selected.publication_key

    JOIN dw.dim_country AS country
        ON country.country_key = bridge.country_key

    GROUP BY
        selected.calendar_year,
        country.country_key,
        country.country_code_iso2,
        country.country_code_iso3,
        country.country_name,
        country.region_name,
        country.income_level_name
),

ranked AS (

    SELECT
        *,

        RANK() OVER (
            PARTITION BY calendar_year
            ORDER BY full_publications DESC
        ) AS full_rank,

        RANK() OVER (
            PARTITION BY calendar_year
            ORDER BY fractional_publications DESC
        ) AS fractional_rank

    FROM country_output
)

SELECT
    calendar_year,

    country_code_iso2,
    country_code_iso3,
    country_name,
    region_name,
    income_level_name,

    full_publications,
    fractional_publications,

    full_rank,
    fractional_rank,

    full_rank - fractional_rank AS rank_shift

FROM ranked

ORDER BY
    calendar_year,
    full_rank,
    country_name;


COMMIT;