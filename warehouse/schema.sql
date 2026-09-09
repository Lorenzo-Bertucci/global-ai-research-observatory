DROP SCHEMA IF EXISTS dw CASCADE;
CREATE SCHEMA dw;

CREATE TABLE dw.dim_date (
    date_key INTEGER PRIMARY KEY CHECK (date_key > 0),
    full_date DATE NOT NULL UNIQUE,
    day_of_month SMALLINT NOT NULL CHECK (day_of_month BETWEEN 1 AND 31),
    month_number SMALLINT NOT NULL CHECK (month_number BETWEEN 1 AND 12),
    quarter_number SMALLINT NOT NULL CHECK (quarter_number BETWEEN 1 AND 4),
    calendar_year SMALLINT NOT NULL CHECK (calendar_year BETWEEN 1 AND 9999)
);

CREATE TABLE dw.dim_year (
    year_key INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    calendar_year SMALLINT NOT NULL UNIQUE CHECK (calendar_year BETWEEN 1 AND 9999)
);

CREATE TABLE dw.dim_country (
    country_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    country_code_iso2 TEXT NOT NULL UNIQUE
        CHECK (country_code_iso2 ~ '^[A-Z]{2}$'),
    country_code_iso3 TEXT NOT NULL UNIQUE
        CHECK (country_code_iso3 ~ '^[A-Z]{3}$'),
    country_name TEXT,
    region_id TEXT,
    region_name TEXT,
    income_level_id TEXT,
    income_level_name TEXT,
    has_world_bank_data BOOLEAN NOT NULL
);

CREATE TABLE dw.dim_topic (
    topic_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    openalex_topic_id TEXT NOT NULL UNIQUE,
    topic_name TEXT,
    subfield_id TEXT,
    subfield_name TEXT,
    field_id TEXT,
    field_name TEXT,
    domain_id TEXT,
    domain_name TEXT
);

CREATE TABLE dw.dim_institution (
    institution_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    openalex_institution_id TEXT NOT NULL UNIQUE,
    institution_name TEXT,
    institution_type TEXT,
    country_code_iso2 TEXT
        CHECK (country_code_iso2 IS NULL OR country_code_iso2 ~ '^[A-Z]{2}$')
);

CREATE TABLE dw.dim_source (
    source_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    openalex_source_id TEXT NOT NULL UNIQUE,
    source_name TEXT,
    source_type TEXT,
    issn_l TEXT
);

CREATE TABLE dw.fact_publication (
    publication_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    openalex_work_id TEXT NOT NULL UNIQUE,
    date_key INTEGER REFERENCES dw.dim_date (date_key),
    source_key BIGINT REFERENCES dw.dim_source (source_key),
    publication_type TEXT,
    language TEXT,
    is_open_access BOOLEAN,
    open_access_status TEXT,
    publication_count SMALLINT NOT NULL DEFAULT 1 CHECK (publication_count = 1),
    citation_count BIGINT CHECK (citation_count IS NULL OR citation_count >= 0)
);

CREATE TABLE dw.fact_country_year (
    country_year_key BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    country_key BIGINT NOT NULL REFERENCES dw.dim_country (country_key),
    year_key INTEGER NOT NULL REFERENCES dw.dim_year (year_key),
    population BIGINT,
    gdp_current_usd NUMERIC,
    gdp_per_capita_current_usd NUMERIC,
    internet_users_pct NUMERIC,
    rd_expenditure_pct_gdp NUMERIC,
    UNIQUE (country_key, year_key)
);

CREATE TABLE dw.bridge_publication_topic (
    publication_key BIGINT NOT NULL REFERENCES dw.fact_publication (publication_key),
    topic_key BIGINT NOT NULL REFERENCES dw.dim_topic (topic_key),
    topic_rank INTEGER NOT NULL CHECK (topic_rank > 0),
    topic_score DOUBLE PRECISION,
    is_primary_topic BOOLEAN NOT NULL,
    fractional_weight NUMERIC NOT NULL
        CHECK (fractional_weight > 0 AND fractional_weight <= 1),
    PRIMARY KEY (publication_key, topic_key)
);

CREATE TABLE dw.bridge_publication_country (
    publication_key BIGINT NOT NULL REFERENCES dw.fact_publication (publication_key),
    country_key BIGINT NOT NULL REFERENCES dw.dim_country (country_key),
    fractional_weight NUMERIC NOT NULL
        CHECK (fractional_weight > 0 AND fractional_weight <= 1),
    PRIMARY KEY (publication_key, country_key)
);

CREATE TABLE dw.bridge_publication_institution (
    publication_key BIGINT NOT NULL REFERENCES dw.fact_publication (publication_key),
    institution_key BIGINT NOT NULL REFERENCES dw.dim_institution (institution_key),
    fractional_weight NUMERIC NOT NULL
        CHECK (fractional_weight > 0 AND fractional_weight <= 1),
    PRIMARY KEY (publication_key, institution_key)
);

CREATE INDEX dim_date_year_idx ON dw.dim_date (calendar_year);
CREATE INDEX fact_publication_date_idx ON dw.fact_publication (date_key);
CREATE INDEX fact_country_year_year_idx ON dw.fact_country_year (year_key);
CREATE INDEX bridge_publication_topic_topic_idx
    ON dw.bridge_publication_topic (topic_key);
CREATE INDEX bridge_publication_country_country_idx
    ON dw.bridge_publication_country (country_key);
CREATE INDEX bridge_publication_institution_institution_idx
    ON dw.bridge_publication_institution (institution_key);
