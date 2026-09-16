-- OLAP SESSION 05 — TOPIC ANNUAL SHARES

-- Analytical question:
-- How has the thematic composition of the observed AI research
-- corpus evolved between 2018 and 2025?

BEGIN TRANSACTION READ ONLY;

-- PARAMETERS

WITH parameters AS (
    SELECT
        2018::smallint AS start_year,
        2025::smallint AS end_year
),

-- STEP 1 — SELECT PUBLICATIONS FROM THE ANALYSIS PERIOD

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

-- STEP 2 — PUBLICATION × TOPIC × YEAR

publication_topic AS (
    SELECT
        selected.publication_key,
        selected.calendar_year,

        COALESCE(
            topic.openalex_topic_id,
            '__MISSING__'
        ) AS topic_id,

        COALESCE(
            topic.topic_name,
            topic.openalex_topic_id,
            'Missing / unavailable'
        ) AS topic_name

    FROM selected_publications AS selected

    JOIN dw.bridge_publication_topic AS bridge
        ON bridge.publication_key = selected.publication_key

    JOIN dw.dim_topic AS topic
        ON topic.topic_key = bridge.topic_key

    GROUP BY
        selected.publication_key,
        selected.calendar_year,
        topic.openalex_topic_id,
        topic.topic_name
),

-- STEP 3 — AGGREGATE TO TOPIC × YEAR

topic_year AS (
    SELECT
        topic_id,
        topic_name,
        calendar_year,

        COUNT(*)::numeric AS topic_publications

    FROM publication_topic

    GROUP BY
        topic_id,
        topic_name,
        calendar_year
),

-- STEP 4 — CUMULATIVE TOPIC PARTICIPATION OVER THE FULL PERIOD

topic_period_totals AS (
    SELECT
        topic_id,
        topic_name,

        SUM(
            topic_publications
        )::numeric AS cumulative_publications

    FROM topic_year

    WHERE topic_id <> '__MISSING__'

    GROUP BY
        topic_id,
        topic_name
),

-- STEP 5 — SELECT THE TOP 4 TOPICS FOR THE FULL PERIOD

selected_topics AS (
    SELECT
        topic_id,
        topic_name,
        cumulative_publications

    FROM topic_period_totals

    ORDER BY
        cumulative_publications DESC,
        topic_id

    LIMIT 4
),

-- STEP 6 — ANNUAL PUBLICATION TOTALS

annual_publications AS (
    SELECT
        calendar_year,

        SUM(
            publication_count
        )::numeric AS annual_publications

    FROM selected_publications

    GROUP BY calendar_year
),

-- STEP 7 — CALCULATE NON-EXCLUSIVE ANNUAL SHARES
-- FOR THE FIXED TOP-4 TOPIC SET

topic_annual_shares AS (
    SELECT
        topic.calendar_year,
        topic.topic_id,
        topic.topic_name,

        selected.cumulative_publications,

        topic.topic_publications,
        annual.annual_publications,

        100.0
            * topic.topic_publications
            / NULLIF(
                annual.annual_publications,
                0
            ) AS annual_share_pct

    FROM topic_year AS topic

    JOIN selected_topics AS selected
        ON selected.topic_id = topic.topic_id

    JOIN annual_publications AS annual
        ON annual.calendar_year = topic.calendar_year
)

-- FINAL RESULT

SELECT
    calendar_year,

    topic_id,
    topic_name,

    cumulative_publications,
    topic_publications,
    annual_publications,

    ROUND(
        annual_share_pct,
        2
    ) AS annual_share_pct

FROM topic_annual_shares

ORDER BY
    calendar_year,
    cumulative_publications DESC,
    topic_name;

COMMIT;