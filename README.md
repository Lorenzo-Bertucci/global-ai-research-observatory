# Global AI Research Observatory

## Overview

A university data-management and data-warehousing project for exploring the
international structure of Artificial Intelligence research published from
2018 through 2025. It integrates OpenAlex publication metadata with World Bank
country-year indicators and exposes the result through PostgreSQL OLAP queries
and a Streamlit/Plotly dashboard.

## Analytical goals

- Track the growth and publication ecosystem of AI research.
- Explore Topic → Subfield → Field → Domain thematic structure.
- Compare country, region, income-level, and institutional participation.
- Relate fractional country output to observed socioeconomic conditions.

## Data sources

### OpenAlex corpus methodology

The adopted final corpus definition selects a work when its **Primary Topic**
belongs to the source-native OpenAlex Artificial Intelligence subfield, ID
`1702`. The extractor applies the same boundary independently to each year from
2018 through 2025, allows `article`, `review`, `conference-paper`, `preprint`,
`book`, and `book-chapter`, and excludes retracted works.

Primary Topic controls corpus membership only. Every topic returned by OpenAlex
for a selected work is retained, so the topic bridge contains one primary topic
and all available secondary topics with explicit primary flags.

### Sampling strategy

At the recorded extraction, 1,487,116 OpenAlex works qualified for the corpus.
The canonical dataset is a directly sourced, reproducible, year-stratified
sample of exactly 50,000 works:

1. Query the current qualifying population count separately for every year.
2. Allocate 50,000 observations proportionally across the eight years.
3. Resolve integer allocations with deterministic largest remainder rounding.
4. Request each annual stratum with OpenAlex native `sample` and `seed`, using
   the fixed master seed plus the publication year.
5. Page through the complete annual sample, combine the strata, globally
   deduplicate, and enforce the corpus guardrails.
6. Publish the raw JSONL and concise provenance manifest only after the complete
   sample passes validation.

The methodological constants are intentionally visible near the top of
`extraction/extract_openalex.py`; no separate OpenAlex configuration file is
needed. The manifest records the live population counts, allocations, seeds,
timestamp, API activity, and content hash. OpenAlex evolves, so a rerun may
produce different population counts even though the sampling procedure and seed
strategy remain fixed.

These 50,000 publications are an analytical sample of the qualifying OpenAlex
population, not the entire population. Results describe the observed sample and
should not be presented as unqualified population estimates.

### World Bank

The frozen World Development Indicators extract covers 2018–2025 and retains
real economies rather than World Bank aggregates.

| Code | Measure |
|---|---|
| `SP.POP.TOTL` | Population |
| `NY.GDP.MKTP.CD` | GDP, current USD |
| `NY.GDP.PCAP.CD` | GDP per capita, current USD |
| `IT.NET.USER.ZS` | Internet users, % of population |
| `GB.XPD.RSDV.GD.ZS` | R&D expenditure, % of GDP |

Missing observations remain SQL `NULL`; the project performs no interpolation,
imputation, or zero substitution.

## Architecture

```text
OpenAlex + World Bank
        ↓
  Raw JSON / JSONL
        ↓
 PostgreSQL Reconciliation
        ↓
 PostgreSQL Data Warehouse
        ↓
   OLAP + Dashboard
```

Corpus selection exists only in the OpenAlex extractor. Reconciliation and the
warehouse remain source-independent downstream layers. Their SQL schemas are
the physical sources of truth, and each loader rebuilds only its own schema in
a transaction.

### Reconciled layer

The nine-table reconciled model preserves natural/source keys:

- `r_work`, `r_topic`, `r_work_topic`
- `r_country`, `r_work_country`
- `r_institution`, `r_work_institution`
- `r_source`
- `r_country_year_indicator`

Missing optional relationships create no artificial member. Dates and years,
primary-topic relationships, source identity, and country mappings are checked
before publication.

### Dimensional warehouse

The fact constellation contains two facts, six dimensions, and three bridges:

- Facts: `fact_publication`, `fact_country_year`
- Dimensions: `dim_date`, `dim_year`, `dim_country`, `dim_topic`,
  `dim_institution`, `dim_source`
- Bridges: `bridge_publication_topic`, `bridge_publication_country`,
  `bridge_publication_institution`

`fact_publication` has one row per sampled work. `fact_country_year` has one row
per observed World Bank economy and year. Surrogate keys are confined to the
warehouse, while source identifiers remain unique dimension attributes.

## Analytical semantics

### Full vs fractional counting

Full counting measures the number of distinct publications in which a member
participates. Fractional counting distributes one publication across the leaf
members of a multi-valued relationship. Parent-level full rollups first reduce
to Publication × Parent, so two topics in the same subfield count once at that
subfield. Fractional parent rollups sum the original leaf weights.

### Normalized country metrics and missing data

Country normalization always uses fractional country attribution regardless of
the dashboard counting selector:

- publications per million people;
- publications per billion USD of GDP.

Publication output is first aggregated to Country × Year, then drilled across
to World Bank measures at the same grain. Missing or zero denominators produce
`NULL`. Filters on other multi-valued dimensions use independent publication-key
semi-joins; analytical measures never multiply country, topic, and institution
bridges in one aggregation.

Valid OpenAlex geographies are preserved even when the frozen World Bank source
has no observation. Such members have `has_world_bank_data = FALSE`, no invented
country-year facts, and unavailable normalized metrics.

### Citation counts and additivity

`citation_count` is the cumulative OpenAlex value observed at extraction time,
not citations received during publication year. Comparisons across publication
years therefore have publication-age bias. Population is a level measure; GDP
is treated as a flow; GDP per capita and percentage indicators are unit measures
and are never summed.

## OLAP sessions

`analysis/sessions.py` defines and can execute/export the final twelve sessions;
`analysis/olap_sessions.sql` is the generated standalone PostgreSQL form.

1. Absolute vs Normalized Geographic Leadership
2. R&D Investment vs AI Research Intensity
3. Evolution of AI Research / Post-2022 Analysis
4. Topic Specialization and Thematic Evolution
5. Wealth vs AI Research Intensity
6. Digital Access vs AI Research Intensity
7. Income-Level Research Gap
8. Regional Research Capacity
9. AI Research Growth vs Socioeconomic Change
10. Institutional Leadership
11. Cumulative Citation Impact
12. Publication Ecosystem

The query builders use read-only, parameterized SQL and preserve bridge grain,
NULL semantics, and Full/Fractional behavior. The project does not hard-code
empirical conclusions; those should be selected after inspecting results.

## Dashboard

The Streamlit/Plotly dashboard provides nine views:

1. Overview
2. Research Growth
3. Geographic Leadership
4. Normalized Leadership
5. Socioeconomic Context
6. Topics
7. Institutions
8. Citation Impact
9. Publication Ecosystem

Filters cover time, geography, topic hierarchy, institution/source/publication
types, language, Open Access, and Full/Fractional counting. Queries aggregate in
PostgreSQL and expose compact downloadable result tables.

## Repository structure

```text
.
├── extraction/       OpenAlex and World Bank source extraction
├── data/raw/         Local, Git-ignored raw datasets and manifests
├── reconciliation/   Natural-key reconciled schema and transactional loader
├── warehouse/        Fact-constellation schema and transactional loader
├── analysis/         Twelve OLAP sessions and compact smoke queries
├── dashboard/        Streamlit application, query layer, views, and dependencies
├── tests/            Unit and isolated-PostgreSQL integration tests
├── .streamlit/       Versioned visual/runtime configuration
├── run_dashboard.py  One-command dashboard launcher
└── README.md          Project documentation
```

## Setup

- Python 3.11 or newer
- PostgreSQL 16 or compatible
- dependencies in `dashboard/requirements.txt`

One-time environment setup from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r dashboard/requirements.txt
createdb global_ai_observatory
```

The normal local database is `postgresql:///global_ai_observatory`. The launcher
uses it automatically and re-executes through the repository `.venv` when
available. An existing `DATABASE_URL` is an optional override for another
PostgreSQL connection; passwords must never be committed.

For OpenAlex extraction, provide `OPENALEX_API_KEY` (or `OPEN_ALEX_KEY`) through
the environment or an ignored local `.env` file. No key is written to code,
manifests, or logs. World Bank extraction requires no key.

## Running the pipeline

Raw files are local and Git-ignored. Preserve the manifests with any analysis so
the live-source state and hashes remain traceable.

```bash
python extraction/extract_openalex.py
python extraction/extract_world_bank.py
DATABASE_URL=postgresql:///global_ai_observatory python reconciliation/load_reconciled.py
DATABASE_URL=postgresql:///global_ai_observatory python warehouse/load_dw.py
DATABASE_URL=postgresql:///global_ai_observatory python analysis/sessions.py --export --execute
```

The World Bank source is frozen for this submission and normally does not need
to be re-extracted. Run reconciliation before the warehouse whenever raw data
change. The explicit connection shown here is for pipeline maintenance; normal
dashboard startup supplies the same local default automatically.

## Running the dashboard

No activation, database export, or separate Streamlit command is required for
the finalized local repository:

```bash
python run_dashboard.py
```

The launcher performs a fast package, code, database, year, fact, and bridge
preflight before starting Streamlit. It does not download data, rebuild the
warehouse, or run the full tests. For a non-launching check, use
`python run_dashboard.py --check-only`.

## Running tests

```bash
python -m unittest discover -s tests -v
```

Integration tests create isolated PostgreSQL databases through
`TEST_DATABASE_URL` (default `postgresql:///postgres`). The suite covers final
sampling, atomic failure behavior, reconciliation, dimensional integrity,
idempotency/rollback, bridge-safe query semantics, all OLAP sessions, dashboard
views, and launcher behavior.

## Limitations

- The 50,000 works are a stratified reproducible sample, not the full qualifying
  OpenAlex population.
- OpenAlex classifications, metadata, and citation snapshots evolve after the
  recorded extraction time.
- World Bank missingness is retained, and R&D coverage is particularly sparse.
- Cumulative citations favor older publications because observation time is not
  publication time.
