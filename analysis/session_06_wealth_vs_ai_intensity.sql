-- OLAP SESSION 06 — WEALTH vs AI RESEARCH INTENSITY
-- Analytical question:
-- Is national wealth, measured through GDP per capita,
-- associated with AI research intensity?

BEGIN TRANSACTION READ ONLY;


-- PARAMETERS

WITH parameters AS (

    SELECT
        2018::smallint AS start_year,
        2025::smallint AS end_year,
        30::numeric AS minimum_full_publications
),


-- STEP 1 — SELECT PUBLICATIONS FROM 2018 TO 2025

selected_publications AS (

    SELECT
        publication.publication_key,
        publication.publication_count,
        date_dim.calendar_year

    FROM dw.fact_publication AS publication

    JOIN dw.dim_date AS date_dim
        ON date_dim.date_key = publication.date_key

    CROSS JOIN parameters AS p

    WHERE date_dim.calendar_year
          BETWEEN p.start_year AND p.end_year
),


-- STEP 2 — AGGREGATE TO COUNTRY × YEAR

publication_country_year AS (

    SELECT
        country.country_key,
        country.country_code_iso2,
        country.country_code_iso3,

        COALESCE(
            country.country_name,
            country.country_code_iso2
        ) AS country_name,

        country.region_name,
        country.income_level_name,
        country.has_world_bank_data,

        selected.calendar_year,

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
        country.income_level_name,
        country.has_world_bank_data
),


-- STEP 3 — DRILL-ACROSS TO WORLD BANK COUNTRY × YEAR DATA

country_year_context AS (

    SELECT
        publication.country_key,
        publication.country_code_iso2,
        publication.country_code_iso3,
        publication.country_name,
        publication.region_name,
        publication.income_level_name,
        publication.has_world_bank_data,

        publication.calendar_year,

        publication.full_publications,
        publication.fractional_publications,

        indicator.population,
        indicator.gdp_per_capita_current_usd,

        CASE
            WHEN indicator.population IS NULL
                 OR indicator.population = 0
            THEN NULL

            ELSE
                publication.fractional_publications
                * 1000000::numeric
                / indicator.population

        END AS publications_per_million

    FROM publication_country_year AS publication

    LEFT JOIN dw.fact_country_year AS indicator
        ON indicator.country_key = publication.country_key
       AND indicator.year = publication.calendar_year
),


-- STEP 4 — APPLY PUBLICATION-SUPPORT THRESHOLD

supported_country_year AS (

    SELECT
        context.*

    FROM country_year_context AS context

    CROSS JOIN parameters AS p

    WHERE context.full_publications
          >= p.minimum_full_publications
),


-- STEP 5 — KEEP COMPLETE PAIRED OBSERVATIONS

paired_observations AS (

    SELECT
        *

    FROM supported_country_year

    WHERE gdp_per_capita_current_usd IS NOT NULL
      AND publications_per_million IS NOT NULL
),


-- STEP 6 — COUNT COMPLETE COUNTRY PAIRS BY YEAR

paired_with_coverage AS (

    SELECT
        paired.*,

        COUNT(*) OVER (
            PARTITION BY calendar_year
        ) AS complete_country_pairs

    FROM paired_observations AS paired
)


-- FINAL RESULT

SELECT
    calendar_year,

    country_code_iso2,
    country_code_iso3,
    country_name,
    region_name,

    COALESCE(
        income_level_name,
        'Missing / unavailable'
    ) AS income_level_name,

    full_publications,

    ROUND(
        fractional_publications,
        3
    ) AS fractional_publications,

    population,

    ROUND(
        gdp_per_capita_current_usd,
        2
    ) AS gdp_per_capita_current_usd,

    ROUND(
        publications_per_million,
        3
    ) AS publications_per_million,

    complete_country_pairs

FROM paired_with_coverage

ORDER BY
    calendar_year,
    gdp_per_capita_current_usd,
    country_name;


COMMIT;