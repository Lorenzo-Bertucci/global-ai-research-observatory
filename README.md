# Global AI Research Observatory

Global AI Research Observatory is a university Data Warehousing project: a **Data Warehouse for analyzing AI-related scientific research** published between **2018 and 2025**. The repository implements source extraction, a PostgreSQL Reconciled Layer, a dimensional warehouse, and OLAP smoke queries.

## Research objective

The project is designed to analyze:

- the temporal evolution of AI-related scientific output;
- its thematic distribution and growth;
- geographic specialization;
- absolute scientific output and output normalized by demographic and socioeconomic indicators.

## Data sources

- **OpenAlex** supplies works, Topics, citations, authorships, institutions, countries, publication sources, and Open Access metadata.
- **World Bank WDI** supplies annual population, economic, connectivity, and R&D indicators.

The shared analytical period is 2018–2025.

## Current architecture

```text
OpenAlex API + World Bank API
              |
              v
         RAW JSON / JSONL
              |
              v
    Python reconciliation ETL
              |
              v
 PostgreSQL schema: reconciled
              |
              v
  PostgreSQL schema: dw
       Fact Constellation
              |
              v
        OLAP smoke queries
```

Source extraction, source-level validation, the Reconciled Layer, and the multidimensional Data Warehouse infrastructure are implemented. Final empirical OLAP analysis remains pending until the final corpus methodology is confirmed.

## Current OpenAlex development extraction

The current development corpus was extracted with **AI Topic Set version 1.1**, retained as a reproducible provisional method while the final corpus-selection methodology awaits the professor's decision:

- `T10028` — Topic Modeling
- `T11714` — Multimodal Machine Learning Applications

A work is included if at least one Topic in its `topics[]` array belongs to the version 1.1 AI Topic Set. Its methodological validation achieved **98.33% strict precision** and **99.00% broad precision**.

The complete 2018–2025 population contains **283,851 works**. Detailed metadata are retained for **24,000 works**, selected with proportional stratified random sampling by publication year. Integer allocations use the **Largest Remainder Method**, and each yearly stratum uses a fixed deterministic seed.

Population-level yearly counts are stored separately and must be used for aggregate temporal analysis. Raw counts from the 24,000-work sample must not be treated as full-population counts.

This current corpus is not necessarily the final corpus for the Data Warehouse. Corpus selection remains upstream from, and independent of, the Reconciled Layer: the reconciliation ETL treats every supplied OpenAlex work as already selected and contains no AI-classification logic.

Current development-extraction results:

| Check | Result |
|---|---:|
| Population | 283,851 |
| Sample rows | 24,000 |
| Unique IDs | 24,000 |
| Duplicates | 0 |
| Guardrail failures | 0 |

Missing optional metadata in the detailed sample:

| Field | Missing |
|---|---:|
| DOI | 5.3792% |
| Country | 33.0750% |
| Institution | 33.0583% |
| Source | 30.7167% |
| Primary Topic | 0% |

OpenAlex raw files:

- `data/raw/openalex_ai_population.json`: complete population counts by publication year.
- `data/raw/openalex_ai_works.jsonl`: the 24,000 sampled works with detailed source metadata.
- `data/raw/openalex_ai_manifest.json`: filters, sampling allocation, seeds, weights, provenance, extraction counters, and output metadata.

## World Bank extraction

The World Bank dataset contains these five WDI indicators:

- `SP.POP.TOTL` — population;
- `NY.GDP.MKTP.CD` — GDP in current US dollars;
- `NY.GDP.PCAP.CD` — GDP per capita in current US dollars;
- `IT.NET.USER.ZS` — individuals using the Internet as a percentage of population;
- `GB.XPD.RSDV.GD.ZS` — R&D expenditure as a percentage of GDP.

Its grain is **one country × one year** for 2018–2025. A complete skeleton contains **217 real economies** and **1,736 country-year rows**, with **0 duplicate** country-year keys. World Bank aggregates are excluded using the source country metadata.

Missing observations remain JSON `null`; no imputation, interpolation, filling, or zero replacement is applied.

World Bank raw files:

- `data/raw/world_bank_country_year.jsonl`: wide country-year rows containing country metadata and the five indicators.
- `data/raw/world_bank_manifest.json`: indicator lineage, country selection, queries, pagination, provenance, missingness, and extraction counters.

## Data quality

World Bank indicator coverage is:

| Indicator | Coverage |
|---|---:|
| Population | 100.0000% |
| GDP | 94.5276% |
| GDP per capita | 94.5276% |
| Internet users | 74.3088% |
| R&D expenditure | 32.4885% |

R&D is the least complete indicator. Internet coverage is very limited for 2025, and R&D data for 2025 are unavailable. These source-level missing values are retained as `null`.

The current source-level data-quality reports are:

- `validation/results/openalex_data_quality.json`
- `validation/results/world_bank_data_quality.json`

They provide quality evidence, reproducibility metadata, and a debugging reference. They are not pipeline inputs.

## Reconciled Layer

The PostgreSQL schema `reconciled` contains nine relational tables built with stable source keys and no surrogate keys:

| Table | Grain |
|---|---|
| `r_work` | one unique OpenAlex Work |
| `r_topic` | one unique OpenAlex Topic, including its denormalized Subfield–Field–Domain hierarchy |
| `r_work_topic` | one distinct Work–Topic association, with source order and score |
| `r_country` | one reconciled geographic entity required by the integrated sources |
| `r_work_country` | one distinct Work–Country association |
| `r_institution` | one unique OpenAlex Institution |
| `r_work_institution` | one distinct Work–Institution association |
| `r_source` | one unique OpenAlex primary publication source |
| `r_country_year_indicator` | one frozen WDI economy × year skeleton row |

Countries are reconciled strictly by code. World Bank is canonical for socioeconomic country metadata and indicators where available, but its frozen WDI coverage does not perfectly coincide with the geographic entities observed in OpenAlex. Valid OpenAlex-only geographies are preserved in `r_country` with `has_world_bank_data = FALSE`; World Bank-specific attributes and country-year indicators remain unavailable rather than being imputed or geographically remapped. Unknown codes outside the explicit reconciliation policy still cause a readable error.

In the current raw data, the OpenAlex-only cases are `RE` (`REU`, Réunion) and `TW` (`TWN`, Taiwan). They remain available for bibliometric analysis of publications, citations, topics, and institutions. Metrics normalized by population, GDP, or other World Bank indicators are unavailable for them because no corresponding frozen WDI observations exist.

Missing scalar values become SQL `NULL`. Missing optional relationships create no artificial relationship row, and World Bank indicators are never imputed. When a primary Topic is supplied it must also occur in that work's Topic relationships. The ETL reads the JSONL inputs, deduplicates entities and many-to-many relationships, rejects conflicting source metadata, rebuilds only the `reconciled` schema in one transaction, and runs critical post-load checks before commit.

## Data Warehouse

The `dw` schema is a star-oriented Fact Constellation loaded exclusively from `reconciled.*`:

| Table | Grain |
|---|---|
| `fact_publication` | one selected OpenAlex work |
| `fact_country_year` | one frozen WDI economy × year skeleton row |
| `dim_date` | one publication date |
| `dim_year` | one World Bank observation year |
| `dim_country` | one reconciled geographic entity |
| `dim_topic` | one Topic with denormalized Subfield–Field–Domain hierarchy |
| `dim_institution` | one OpenAlex Institution |
| `dim_source` | one OpenAlex primary publication source |
| `bridge_publication_topic` | one Publication–Topic association |
| `bridge_publication_country` | one Publication–Country association |
| `bridge_publication_institution` | one Publication–Institution association |

Dimensions use warehouse surrogate keys while retaining unique source identifiers. `publication_type` remains a low-cardinality attribute in `fact_publication`; a missing source is represented by a nullable foreign key. The Topic bridge explicitly marks the source primary Topic without introducing a separate primary-Topic dimension. Bridge rows contain both membership and a technical fractional weight, so later analyses may choose full counting (ignore the weight) or fractional counting (use the weight) without changing the schema. Fractional counting is the recommended default for country-normalized metrics; full counting remains valid for participation analysis.

`citation_count` is the cumulative citation snapshot associated with a publication at OpenAlex extraction time. It does not represent citations received during the publication year.

Publication dates use `dim_date` with Date–Month–Quarter–Year attributes. Annual socioeconomic observations use the separate `dim_year`; their shared `calendar_year` value supports semantically correct Country × Year drill-across without mapping annual observations to an artificial date.

## Repository structure

```text
.
├── .gitignore
├── README.md
├── config
│   └── openalex_ai_topics.json
├── data
│   └── raw
│       ├── openalex_ai_manifest.json
│       ├── openalex_ai_population.json
│       ├── openalex_ai_works.jsonl
│       ├── world_bank_country_year.jsonl
│       └── world_bank_manifest.json
├── extraction
│   ├── extract_openalex.py
│   └── extract_world_bank.py
├── analysis
│   └── olap_smoke.sql
├── reconciliation
│   ├── load_reconciled.py
│   └── schema.sql
├── warehouse
│   ├── load_dw.py
│   └── schema.sql
├── tests
│   ├── test_extract_openalex.py
│   ├── test_extract_world_bank.py
│   ├── test_reconciled_layer.py
│   └── test_load_dw.py
└── validation
    └── results
        ├── openalex_data_quality.json
        └── world_bank_data_quality.json
```

## How to run

Run the extractors from the repository root:

```bash
python3 extraction/extract_openalex.py
python3 extraction/extract_world_bank.py
```

Both commands perform live API extraction and overwrite their final outputs only after local validation succeeds. OpenAlex reads `OPENALEX_API_KEY` or `OPEN_ALEX_KEY` from the environment, with optional fallback to the ignored local `.env` file. World Bank WDI requires no API key. Supported output and request options are documented by each command's `--help` flag.

The Reconciled Layer requires PostgreSQL and Psycopg 3:

```bash
python3 -m pip install 'psycopg[binary]>=3.2,<4'
export DATABASE_URL='postgresql:///global_ai_observatory'
python3 reconciliation/load_reconciled.py
python3 warehouse/load_dw.py
```

`DATABASE_URL` must identify a database intended for this project. Each loader atomically drops and recreates only its own target schema. The DW loader reads only `reconciled.*`; it does not modify reconciled tables, raw files, or other PostgreSQL schemas. Alternative Reconciled input paths are available through `--openalex` and `--world-bank`.

Validate and summarize source compatibility without connecting to PostgreSQL or performing a load with:

```bash
python3 reconciliation/load_reconciled.py --validate-only
```

After loading the DW, execute the non-final OLAP validation queries with:

```bash
psql "$DATABASE_URL" -f analysis/olap_smoke.sql
```

## Tests

Run the complete offline unit and local-artifact test suite with:

```bash
python3 -m unittest discover -s tests -v
```

Current extractor-test result: **23/23 tests pass**.

The Reconciled Layer integration tests create and remove an isolated PostgreSQL database. They use `TEST_DATABASE_URL` as the administrative connection when set, otherwise `postgresql:///postgres`:

```bash
python3 -m unittest tests/test_reconciled_layer.py -v
python3 -m unittest tests/test_load_dw.py -v
python3 -m unittest discover -s tests -v
```

Current verified result: **10/10 Reconciled Layer tests, 7/7 DW tests, and 40/40 total tests pass**. The seven OLAP smoke queries are executed by the DW integration tests.

## Current project status

```text
Data acquisition: COMPLETE
Reconciled Layer: COMPLETE AND TESTED
DW infrastructure: COMPLETE AND TESTED
OLAP infrastructure: SMOKE-TESTED

World Bank source dataset: FROZEN
Current OpenAlex corpus: NOT NECESSARILY FINAL
Corpus selection and reconciliation: INDEPENDENT
OpenAlex-only geographies: RE (Réunion) and TW (Taiwan), preserved without WDI indicators
Final/full database load: NOT PERFORMED
Final OLAP analysis: PENDING FINAL CORPUS

Next phase:
Professor decision, final OpenAlex corpus, final load, and empirical OLAP exploration
```

## Next step

Freeze the final OpenAlex corpus-selection methodology, rerun the existing RAW → RECONCILED → DW pipeline, and then select the most informative OLAP results for the final presentation. The current sample has been used only for temporary development validation, not as the final empirical dataset.
