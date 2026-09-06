# Global AI Research Observatory

Global AI Research Observatory is a university Data Warehousing project: a **Data Warehouse for analyzing AI-related scientific research** published between **2018 and 2025**. The current repository contains the completed data-acquisition layer for OpenAlex and the World Bank World Development Indicators (WDI).

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
OpenAlex API                         World Bank API
    |                                    |
    v                                    v
extract_openalex.py              extract_world_bank.py
    |                                    |
    v                                    v
OpenAlex raw data                  World Bank raw data
             \                        /
              \                      /
               v                    v
                 NEXT: Reconciled Layer
```

Only source extraction and source-level validation are implemented. The Reconciled Layer and the Data Warehouse are future phases.

## OpenAlex extraction

The corpus is defined by the frozen **AI Topic Set version 1.1**:

- `T10028` — Topic Modeling
- `T11714` — Multimodal Machine Learning Applications

A work is included if at least one Topic in its `topics[]` array belongs to the frozen AI Topic Set. The final methodological validation achieved **98.33% strict precision** and **99.00% broad precision**.

The complete 2018–2025 population contains **283,851 works**. Detailed metadata are retained for **24,000 works**, selected with proportional stratified random sampling by publication year. Integer allocations use the **Largest Remainder Method**, and each yearly stratum uses a fixed deterministic seed.

Population-level yearly counts are stored separately and must be used for aggregate temporal analysis. Raw counts from the 24,000-work sample must not be treated as full-population counts.

Final OpenAlex results:

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

The final source-level data-quality reports are:

- `validation/results/openalex_data_quality.json`
- `validation/results/world_bank_data_quality.json`

They provide quality evidence, reproducibility metadata, and a debugging reference. They are not input datasets for the future Data Warehouse.

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
├── tests
│   ├── test_extract_openalex.py
│   └── test_extract_world_bank.py
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

## Tests

Run the complete offline unit and local-artifact test suite with:

```bash
python3 -m unittest discover -s tests -v
```

Current result: **23/23 tests pass**.

## Current project status

```text
Data acquisition: COMPLETE

OpenAlex: COMPLETE
World Bank: COMPLETE

Next phase:
Reconciled Layer
```

## Next step

Begin the Reconciled Layer by reconciling the two frozen source datasets. No reconciled structures, dimensional schema, SQL, or PostgreSQL implementation exists in this repository yet.
