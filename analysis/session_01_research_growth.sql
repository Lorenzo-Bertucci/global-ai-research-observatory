-- OLAP SESSION 01 — RESEARCH GROWTH

-- Analytical question:
-- Does observed publication output show acceleration after 2022?


BEGIN TRANSACTION READ ONLY;



-- QUERY 1 — YEARLY RESEARCH GROWTH


WITH yearly_counts AS (
    SELECT
        date_dim.calendar_year,
        SUM(publication.publication_count)::numeric AS publications
    FROM dw.fact_publication AS publication
    JOIN dw.dim_date AS date_dim
        ON date_dim.date_key = publication.date_key
    WHERE date_dim.calendar_year BETWEEN 2018 AND 2025
    GROUP BY date_dim.calendar_year
),
yearly_growth AS (
    SELECT
        calendar_year,
        publications,
        LAG(publications) OVER (
            ORDER BY calendar_year
        ) AS previous_year_publications
    FROM yearly_counts
)
SELECT
    calendar_year,
    publications,
    100.0
        * (publications - previous_year_publications)
        / NULLIF(previous_year_publications, 0)
        AS year_over_year_growth_pct
FROM yearly_growth
ORDER BY calendar_year;


-- QUERY 2 — 2022–2025 QUARTERLY DRILL-DOWN


WITH filtered_publications AS (
    SELECT
        publication.publication_count,
        date_dim.full_date
    FROM dw.fact_publication AS publication
    JOIN dw.dim_date AS date_dim
        ON date_dim.date_key = publication.date_key
    WHERE date_dim.calendar_year BETWEEN 2022 AND 2025
),
bounds AS (
    SELECT
        MIN(full_date) AS first_date,
        MAX(full_date) AS last_date
    FROM filtered_publications
),
periods AS (
    SELECT
        generate_series(
            date_trunc('quarter', first_date),
            date_trunc('quarter', last_date),
            interval '3 months'
        )::date AS period
    FROM bounds
),
counts AS (
    SELECT
        date_trunc('quarter', full_date)::date AS period,
        SUM(publication_count)::numeric AS publications
    FROM filtered_publications
    GROUP BY 1
),
series AS (
    SELECT
        period,
        COALESCE(publications, 0) AS publications
    FROM periods
    LEFT JOIN counts USING (period)
),
growth AS (
    SELECT
        period,
        publications,
        LAG(publications) OVER (
            ORDER BY period
        ) AS previous_period_publications
    FROM series
)
SELECT
    period,
    publications,
    100.0
        * (publications - previous_period_publications)
        / NULLIF(previous_period_publications, 0)
        AS period_growth_pct
FROM growth
ORDER BY period;


-- QUERY 3 — 2025 MONTHLY DRILL-DOWN

WITH filtered_publications AS (
    SELECT
        publication.publication_count,
        date_dim.full_date
    FROM dw.fact_publication AS publication
    JOIN dw.dim_date AS date_dim
        ON date_dim.date_key = publication.date_key
    WHERE date_dim.calendar_year = 2025
),
bounds AS (
    SELECT
        MIN(full_date) AS first_date,
        MAX(full_date) AS last_date
    FROM filtered_publications
),
periods AS (
    SELECT
        generate_series(
            date_trunc('month', first_date),
            date_trunc('month', last_date),
            interval '1 month'
        )::date AS period
    FROM bounds
),
counts AS (
    SELECT
        date_trunc('month', full_date)::date AS period,
        SUM(publication_count)::numeric AS publications
    FROM filtered_publications
    GROUP BY 1
),
series AS (
    SELECT
        period,
        COALESCE(publications, 0) AS publications
    FROM periods
    LEFT JOIN counts USING (period)
),
growth AS (
    SELECT
        period,
        publications,
        LAG(publications) OVER (
            ORDER BY period
        ) AS previous_period_publications
    FROM series
)
SELECT
    period,
    publications,
    100.0
        * (publications - previous_period_publications)
        / NULLIF(previous_period_publications, 0)
        AS period_growth_pct
FROM growth
ORDER BY period;


-- QUERY 4 — PUBLICATION TYPE × YEAR

SELECT
    date_dim.calendar_year,

    COALESCE(
        publication.publication_type,
        'Missing / unavailable'
    ) AS publication_type,

    SUM(
        publication.publication_count
    )::numeric AS publications

FROM dw.fact_publication AS publication

JOIN dw.dim_date AS date_dim
    ON date_dim.date_key = publication.date_key

WHERE date_dim.calendar_year BETWEEN 2018 AND 2025

GROUP BY
    date_dim.calendar_year,
    COALESCE(
        publication.publication_type,
        'Missing / unavailable'
    )

ORDER BY
    date_dim.calendar_year,
    publication_type;


-- QUERY 5 — OPEN ACCESS × YEAR

SELECT
    date_dim.calendar_year,

    CASE
        WHEN publication.is_open_access IS TRUE
            THEN 'Open Access'

        WHEN publication.is_open_access IS FALSE
            THEN 'Not Open Access'

        ELSE 'Missing / unavailable'
    END AS open_access,

    SUM(
        publication.publication_count
    )::numeric AS publications

FROM dw.fact_publication AS publication

JOIN dw.dim_date AS date_dim
    ON date_dim.date_key = publication.date_key

WHERE date_dim.calendar_year BETWEEN 2018 AND 2025

GROUP BY
    date_dim.calendar_year,
    publication.is_open_access

ORDER BY
    date_dim.calendar_year,
    publication.is_open_access DESC NULLS LAST;


COMMIT;