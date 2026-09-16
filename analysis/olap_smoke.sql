-- Publication trend by calendar year. citation_count is the cumulative citation
-- snapshot observed at extraction time, not citations received during that year.
SELECT
    date_dim.calendar_year,
    SUM(publication.publication_count) AS publication_count,
    SUM(publication.citation_count) AS citation_count
FROM dw.fact_publication publication
JOIN dw.dim_date date_dim ON date_dim.date_key = publication.date_key
GROUP BY date_dim.calendar_year
ORDER BY date_dim.calendar_year;

-- Full-counted publication participation by country.
SELECT
    country.country_code_iso2,
    country.country_name,
    country.region_name,
    country.income_level_name,
    SUM(publication.publication_count) AS publication_count
FROM dw.fact_publication publication
JOIN dw.bridge_publication_country bridge
    ON bridge.publication_key = publication.publication_key
JOIN dw.dim_country country ON country.country_key = bridge.country_key
GROUP BY
    country.country_code_iso2,
    country.country_name,
    country.region_name,
    country.income_level_name
ORDER BY publication_count DESC, country.country_code_iso2;

-- Publications by Topic and its denormalized Subfield hierarchy level.
SELECT
    topic.openalex_topic_id,
    topic.topic_name,
    topic.subfield_name,
    SUM(publication.publication_count) AS publication_count
FROM dw.fact_publication publication
JOIN dw.bridge_publication_topic bridge
    ON bridge.publication_key = publication.publication_key
JOIN dw.dim_topic topic ON topic.topic_key = bridge.topic_key
GROUP BY topic.openalex_topic_id, topic.topic_name, topic.subfield_name
ORDER BY publication_count DESC, topic.openalex_topic_id;

-- Publication participation by institution type: establish Publication x Type
-- before aggregating so two institutions of the same type count only once.
WITH publication_type AS (
    SELECT publication.publication_key, institution.institution_type
    FROM dw.fact_publication publication
    JOIN dw.bridge_publication_institution bridge USING (publication_key)
    JOIN dw.dim_institution institution USING (institution_key)
    GROUP BY publication.publication_key, institution.institution_type
)
SELECT institution_type, count(*) AS publication_count
FROM publication_type GROUP BY institution_type
ORDER BY publication_count DESC, institution_type;

-- Publications by optional primary-source type.
SELECT
    COALESCE(source.source_type, '<missing>') AS source_type,
    SUM(publication.publication_count) AS publication_count
FROM dw.fact_publication publication
LEFT JOIN dw.dim_source source ON source.source_key = publication.source_key
GROUP BY COALESCE(source.source_type, '<missing>')
ORDER BY publication_count DESC, source_type;

-- Publication type is a low-cardinality attribute stored directly in the fact.
SELECT
    publication.publication_type,
    SUM(publication.publication_count) AS publication_count
FROM dw.fact_publication publication
GROUP BY publication.publication_type
ORDER BY publication_count DESC, publication.publication_type;

-- Fractional Country × Year drill-across for normalized metrics. Missing World
-- Bank facts produce NULL metrics.
WITH publication_country_year AS (
    SELECT
        country.country_key,
        country.country_code_iso2,
        country.country_name,
        date_dim.calendar_year,
        SUM(
            publication.publication_count * bridge.fractional_weight
        ) AS publication_count
    FROM dw.fact_publication publication
    JOIN dw.dim_date date_dim ON date_dim.date_key = publication.date_key
    JOIN dw.bridge_publication_country bridge
        ON bridge.publication_key = publication.publication_key
    JOIN dw.dim_country country ON country.country_key = bridge.country_key
    GROUP BY
        country.country_key,
        country.country_code_iso2,
        country.country_name,
        date_dim.calendar_year
)
SELECT
    publication_country_year.country_code_iso2,
    publication_country_year.country_name,
    publication_country_year.calendar_year,
    publication_country_year.publication_count,
    indicator.population,
    indicator.gdp_current_usd,
    CASE
        WHEN indicator.population IS NULL OR indicator.population = 0 THEN NULL
        ELSE publication_country_year.publication_count * 1000000::NUMERIC
            / indicator.population
    END AS publications_per_million_inhabitants,
    CASE
        WHEN indicator.gdp_current_usd IS NULL OR indicator.gdp_current_usd = 0 THEN NULL
        ELSE publication_country_year.publication_count * 1000000000::NUMERIC
            / indicator.gdp_current_usd
    END AS publications_per_billion_usd_gdp
FROM publication_country_year
LEFT JOIN dw.fact_country_year indicator
    ON indicator.country_key = publication_country_year.country_key
    AND indicator.year = publication_country_year.calendar_year
ORDER BY
    publication_country_year.calendar_year,
    publication_country_year.country_code_iso2;
