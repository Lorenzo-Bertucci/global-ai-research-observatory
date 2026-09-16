-- OLAP SESSION 04 — INCOME-LEVEL RESEARCH GAP

BEGIN TRANSACTION READ ONLY;


-- PARAMETERS

WITH parameters AS (

    SELECT
        2018::smallint AS start_year,
        2025::smallint AS end_year
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


-- STEP 2 — FRACTIONAL OUTPUT BY COUNTRY × YEAR

country_output AS (

    SELECT
        country.country_key,
        selected.calendar_year,

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
        selected.calendar_year
),


-- STEP 3 — FULL PARTICIPATION BY INCOME LEVEL × YEAR


publication_income_level AS (

    SELECT
        selected.publication_key,
        selected.calendar_year,
        country.income_level_name AS income_level

    FROM selected_publications AS selected

    JOIN dw.bridge_publication_country AS bridge
        ON bridge.publication_key = selected.publication_key

    JOIN dw.dim_country AS country
        ON country.country_key = bridge.country_key

    WHERE country.income_level_name IN (
        'High income',
        'Upper middle income',
        'Lower middle income',
        'Low income'
    )

    GROUP BY
        selected.publication_key,
        selected.calendar_year,
        country.income_level_name
),

full_output AS (

    SELECT
        income_level,
        calendar_year,
        COUNT(*)::numeric AS full_publications

    FROM publication_income_level

    GROUP BY
        income_level,
        calendar_year
),


-- STEP 4 — CREATE COMPLETE YEAR COORDINATES

year_coordinates AS (

    SELECT
        generate_series(
            (SELECT start_year FROM parameters)::integer,
            (SELECT end_year FROM parameters)::integer
        )::integer AS calendar_year
),


-- STEP 5 — COMPLETE COUNTRY × YEAR GRID

country_year AS (

    SELECT
        country.country_key,
        country.country_code_iso2,
        country.country_code_iso3,

        COALESCE(
            country.country_name,
            country.country_code_iso2
        ) AS country_name,

        country.income_level_name AS income_level,

        year_coordinate.calendar_year,

        COALESCE(
            output.fractional_publications,
            0
        )::numeric AS fractional_publications,

        indicator.population

    FROM dw.dim_country AS country

    CROSS JOIN year_coordinates AS year_coordinate

    LEFT JOIN country_output AS output
        ON output.country_key = country.country_key
       AND output.calendar_year = year_coordinate.calendar_year

    LEFT JOIN dw.fact_country_year AS indicator
        ON indicator.country_key = country.country_key
       AND indicator.year = year_coordinate.calendar_year

    WHERE country.income_level_name IN (
        'High income',
        'Upper middle income',
        'Lower middle income',
        'Low income'
    )
),


-- STEP 6 — ROLL-UP COUNTRY -> INCOME LEVEL

income_level_year AS (

    SELECT
        country_year.income_level,
        country_year.calendar_year,

        COALESCE(
            full_output.full_publications,
            0
        )::numeric AS full_publications,

        SUM(
            country_year.fractional_publications
        )::numeric AS fractional_publications,

        SUM(
            country_year.fractional_publications
        ) FILTER (
            WHERE country_year.population > 0
        )::numeric AS matched_fractional_publications,

        SUM(
            country_year.population
        ) FILTER (
            WHERE country_year.population > 0
        )::numeric AS matched_population,

        COUNT(*) AS countries,

        COUNT(*) FILTER (
            WHERE country_year.population > 0
        ) AS population_countries

    FROM country_year

    LEFT JOIN full_output
        ON full_output.income_level = country_year.income_level
       AND full_output.calendar_year = country_year.calendar_year

    GROUP BY
        country_year.income_level,
        country_year.calendar_year,
        full_output.full_publications
),


-- STEP 7 — ANNUAL KNOWN-INCOME FRACTIONAL TOTAL

annual_known_income_totals AS (

    SELECT
        calendar_year,

        SUM(
            fractional_publications
        )::numeric AS annual_known_fractional_publications

    FROM income_level_year

    GROUP BY calendar_year
),


-- STEP 8 — CALCULATE SHARE AND RESEARCH INTENSITY

income_level_metrics AS (

    SELECT
        income.calendar_year,
        income.income_level,

        income.full_publications,
        income.fractional_publications,

        total.annual_known_fractional_publications,

        100.0
            * income.fractional_publications
            / NULLIF(
                total.annual_known_fractional_publications,
                0
            ) AS fractional_share_pct,

        income.matched_fractional_publications,
        income.matched_population,

        CASE
            WHEN income.matched_population IS NULL
                 OR income.matched_population = 0
            THEN NULL

            ELSE
                1000000::numeric
                * income.matched_fractional_publications
                / income.matched_population

        END AS publications_per_million,

        income.countries,
        income.population_countries

    FROM income_level_year AS income

    JOIN annual_known_income_totals AS total
        ON total.calendar_year = income.calendar_year
)


-- FINAL RESULT

SELECT
    calendar_year,
    income_level,

    full_publications,

    ROUND(
        fractional_publications,
        3
    ) AS fractional_publications,

    ROUND(
        annual_known_fractional_publications,
        3
    ) AS annual_known_fractional_publications,

    ROUND(
        fractional_share_pct,
        2
    ) AS fractional_share_pct,

    ROUND(
        matched_fractional_publications,
        3
    ) AS matched_fractional_publications,

    matched_population,

    ROUND(
        publications_per_million,
        3
    ) AS publications_per_million,

    countries,
    population_countries

FROM income_level_metrics

ORDER BY
    calendar_year,

    CASE income_level
        WHEN 'High income' THEN 1
        WHEN 'Upper middle income' THEN 2
        WHEN 'Lower middle income' THEN 3
        WHEN 'Low income' THEN 4
        ELSE 5
    END;


COMMIT;