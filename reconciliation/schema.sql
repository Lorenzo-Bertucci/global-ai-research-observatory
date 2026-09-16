DROP SCHEMA IF EXISTS reconciled CASCADE;
CREATE SCHEMA reconciled;

CREATE TABLE reconciled.r_country (
    country_code_iso2 TEXT PRIMARY KEY
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

CREATE TABLE reconciled.r_topic (
    topic_id TEXT PRIMARY KEY,
    topic_name TEXT,
    subfield_id TEXT,
    subfield_name TEXT,
    field_id TEXT,
    field_name TEXT,
    domain_id TEXT,
    domain_name TEXT
);

CREATE TABLE reconciled.r_institution (
    institution_id TEXT PRIMARY KEY,
    display_name TEXT,
    institution_type TEXT,
    country_code_iso2 TEXT REFERENCES reconciled.r_country (country_code_iso2)
);

CREATE TABLE reconciled.r_source (
    source_id TEXT PRIMARY KEY,
    display_name TEXT,
    source_type TEXT,
    issn_l TEXT
);

CREATE TABLE reconciled.r_work (
    work_id TEXT PRIMARY KEY,
    doi TEXT,
    title TEXT,
    publication_date DATE,
    publication_year SMALLINT
        CHECK (publication_year IS NULL OR publication_year BETWEEN 1 AND 9999),
    CHECK (
        publication_date IS NULL
        OR publication_year IS NULL
        OR EXTRACT(YEAR FROM publication_date)::SMALLINT = publication_year
    ),
    work_type TEXT,
    language TEXT,
    cited_by_count BIGINT
        CHECK (cited_by_count IS NULL OR cited_by_count >= 0),
    primary_topic_id TEXT REFERENCES reconciled.r_topic (topic_id),
    source_id TEXT REFERENCES reconciled.r_source (source_id),
    is_open_access BOOLEAN
);

CREATE TABLE reconciled.r_work_topic (
    work_id TEXT NOT NULL REFERENCES reconciled.r_work (work_id),
    topic_id TEXT NOT NULL REFERENCES reconciled.r_topic (topic_id),
    topic_rank INTEGER NOT NULL CHECK (topic_rank > 0),
    topic_score DOUBLE PRECISION,
    PRIMARY KEY (work_id, topic_id)
);

CREATE TABLE reconciled.r_work_country (
    work_id TEXT NOT NULL REFERENCES reconciled.r_work (work_id),
    country_code_iso2 TEXT NOT NULL REFERENCES reconciled.r_country (country_code_iso2),
    PRIMARY KEY (work_id, country_code_iso2)
);

CREATE TABLE reconciled.r_work_institution (
    work_id TEXT NOT NULL REFERENCES reconciled.r_work (work_id),
    institution_id TEXT NOT NULL REFERENCES reconciled.r_institution (institution_id),
    PRIMARY KEY (work_id, institution_id)
);

CREATE TABLE reconciled.r_country_year_indicator (
    country_code_iso2 TEXT NOT NULL REFERENCES reconciled.r_country (country_code_iso2),
    year SMALLINT NOT NULL CHECK (year BETWEEN 1 AND 9999),
    population BIGINT,
    gdp_current_usd NUMERIC,
    gdp_per_capita_current_usd NUMERIC,
    internet_users_pct NUMERIC,
    rd_expenditure_pct_gdp NUMERIC,
    PRIMARY KEY (country_code_iso2, year)
);
