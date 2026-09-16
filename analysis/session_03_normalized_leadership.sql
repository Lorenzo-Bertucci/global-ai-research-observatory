-- OLAP SESSION 03 — NORMALIZED LEADERSHIP
-- FRACTIONAL PUBLICATIONS PER MILLION INHABITANTS

-- Analytical question:
-- Which countries show the highest AI research intensity after
-- accounting for population size?

BEGIN TRANSACTION READ ONLY;


-- PARAMETERS

WITH parameters AS (

    SELECT
        2018::smallint AS start_year,
        2025::smallint AS end_year,
        30::numeric AS minimum_full_publications
),


-- STEP 1 — SELECT PUBLICATIONS ACROSS 2018–2025

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
        country.country_key,
        country.country_code_iso2,
        country.country_code_iso3,
        country.country_name,
        country.region_name,
        country.income_level_name,
        country.has_world_bank_data,
        selected.calendar_year
),


-- STEP 3 — DRILL-ACROSS TO WORLD BANK COUNTRY × YEAR DATA

normalized_country_year AS (

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


-- STEP 4 — APPLY MINIMUM PUBLICATION SUPPORT

supported_country_year AS (

    SELECT
        normalized.*

    FROM normalized_country_year AS normalized

    CROSS JOIN parameters AS p

    WHERE normalized.full_publications
          >= p.minimum_full_publications
),


-- STEP 5 — ANNUAL NORMALIZED RANKING

ranked AS (

    SELECT
        supported.*,

        RANK() OVER (
            PARTITION BY calendar_year
            ORDER BY publications_per_million DESC
        ) AS normalized_rank

    FROM supported_country_year AS supported

    WHERE publications_per_million IS NOT NULL
)


-- FINAL RESULT

SELECT
    calendar_year,
    normalized_rank,

    country_code_iso2,
    country_code_iso3,
    country_name,
    region_name,
    income_level_name,

    full_publications,

    ROUND(
        fractional_publications,
        3
    ) AS fractional_publications,

    population,

    ROUND(
        publications_per_million,
        3
    ) AS publications_per_million

FROM ranked

ORDER BY
    calendar_year,
    normalized_rank,
    country_name;


COMMIT;