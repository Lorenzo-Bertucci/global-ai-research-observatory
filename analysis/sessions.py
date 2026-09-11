"""Final OLAP navigation catalog; shares parameterized SQL with the dashboard.

Export readable standalone PostgreSQL using --export; execute using --execute.
No warehouse objects or derived facts are created by these analytical sessions.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import queries as q


@dataclass(frozen=True)
class Session:
    number: int
    title: str
    question: str
    steps: tuple[tuple[str, q.QuerySpec], ...]


def sessions(filters: q.FilterState | None = None) -> tuple[Session, ...]:
    f = filters or q.FilterState(start_year=2018, end_year=2025)
    selected = replace(f, start_year=f.end_year, end_year=f.end_year)
    return (
        Session(1, "Absolute vs normalized geographic leadership",
                "Does leadership change after accounting for country size and economic capacity?",
                (("Full participation: one publication per participating country", q.country_output(f, "Full", 30)),
                 ("Fractional attribution: original country weights", q.country_output(f, "Fractional", 30)),
                 ("Drill to Country x Year, then across WDI; NULL denominators have no rank", q.leadership_ranks(f)))),
        Session(2, "R&D investment vs research intensity",
                "Is reported R&D expenditure associated with fractional publications per million?",
                (("Country x Year pairs: retain NULL R&D values; associations are not causal", q.normalized_country_year(f)),)),
        Session(3, "Research evolution / post-2022",
                "Does observed publication output show acceleration after 2022?",
                (("Roll up to Year; adjacent-period growth, no growth from zero base", q.temporal_navigation(f)),
                 ("Dice 2022–2025 and drill to Quarter", q.temporal_navigation(replace(f, start_year=max(f.start_year or 2018, 2022)), "Quarter")),
                 ("Drill to Month in selected final year", q.temporal_navigation(selected, "Month")),
                 ("Slice by publication type and year", q.publication_type_by_year(f)),
                 ("Slice by Open Access status and year", q.open_access_by_year(f)))),
        Session(4, "Topic specialization and thematic evolution",
                "Which source-native topics characterize the selected corpus over time?",
                (("Roll up Topic to Domain: full appearance, once per publication and Domain", q.topic_output(f, "Full", "Domain", 30)),
                 ("Drill to Subfield x Year: full appearance", q.topic_evolution(f, "Full", "Subfield", 8)),
                 ("Drill to Topic and change measure to fractional topic attribution", q.topic_output(f, "Fractional", "Topic", 30)))),
        Session(5, "Wealth vs research intensity",
                "Is GDP per capita associated with fractional publications per million?",
                (("Slice selected year; Region/Income Level are alternative country attributes", q.normalized_country_year(selected)),)),
        Session(6, "Digital access vs research intensity",
                "Is Internet penetration associated with fractional publications per million?",
                (("Year navigation: retain unavailable pairs; Internet is an observed proxy", q.normalized_country_year(f)),)),
        Session(7, "Income-level research gap",
                "How does country output and normalized intensity vary across income levels over time?",
                (("Country to Income Level x Year; full, fractional and matched-denominator ratios", q.country_group_capacity(f, "Income level")),)),
        Session(8, "Regional research capacity",
                "How does regional output compare with observed population and GDP?",
                (("Country to Region x Year; report denominator coverage; never sum population across years", q.country_group_capacity(f, "Region")),)),
        Session(9, "Research growth vs socioeconomic change",
                "Are adjacent-year changes in intensity associated with socioeconomic changes?",
                (("Country x Year self-join to preceding calendar year; absolute changes avoid tiny-base percentage instability", q.socioeconomic_change(f)),)),
        Session(10, "Institutional leadership",
                "Which institution types and institutions participate in the corpus?",
                (("Roll up Institution Type: full participation once per publication/type", q.institution_type_output(f, "Full")),
                 ("Drill to Institution: full participation", q.institution_output(f, "Full", 30)),
                 ("Change measure to fractional institution attribution", q.institution_output(f, "Fractional", 30)))),
        Session(11, "Cumulative citation impact",
                "How are citation snapshots distributed across publication years and members?",
                (("Year: cumulative snapshots and average per publication; publication-age bias applies", q.publications_by_year(f)),
                 ("Country: full attributed cumulative citations", q.citation_ranking(f, "Country", "Full", 30)),
                 ("Topic: fractionally attributed cumulative citations", q.citation_ranking(f, "Topic", "Fractional", 30)),
                 ("Publication Type: cumulative snapshots; no bridge attribution", q.citation_ranking(f, "Publication type", "Full", 30)))),
        Session(12, "Publication ecosystem",
                "How does the corpus distribute across sources, types, access and language?",
                (("Source Type roll-up; missing source is presentation-only", q.source_type_output(f)),
                 ("Drill to Source", q.source_output(f, 30)),
                 ("Publication Type x Year", q.publication_type_by_year(f)),
                 ("Open Access x Year", q.open_access_by_year(f)),
                 ("Language distribution", q._with_filtered(f, "SELECT language, sum(publication_count) AS publications FROM filtered_publications GROUP BY language ORDER BY publications DESC NULLS LAST, language")))),
    )


def execute(connection) -> list[dict]:
    results = []
    for session in sessions():
        counts = []
        for _, spec in session.steps:
            counts.append(len(connection.execute(spec.sql, spec.params).fetchall()))
        results.append({"session": session.number, "title": session.title,
                        "queries_passed": len(counts), "result_rows": counts})
    return results


def export(connection, path: Path) -> None:
    import psycopg
    output = ["-- Final OLAP analytical sessions. Generated by analysis/sessions.py --export.",
              "-- Shared query definitions: dashboard/queries.py. No stored aggregates.",
              "-- All citation measures are cumulative snapshots at extraction, not citations received in publication year.",
              "-- Full counts participation; fractional uses original leaf weights. Filters use independent EXISTS.",
              "-- Results describe the reproducible year-stratified OpenAlex sample, not the complete population; inspect the raw manifest.",
              "BEGIN TRANSACTION READ ONLY;"]
    with psycopg.ClientCursor(connection) as cursor:
        for session in sessions():
            output.extend([f"\n-- SESSION {session.number}: {session.title}", f"-- Question: {session.question}"])
            for instruction, spec in session.steps:
                output.extend([f"-- Navigation: {instruction}", cursor.mogrify(spec.sql, spec.params).strip() + ";"])
    output.append("COMMIT;\n")
    path.write_text("\n".join(output), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.export and not args.execute:
        parser.error("choose --export and/or --execute")
    if not os.getenv("DATABASE_URL"):
        parser.error("DATABASE_URL is required")
    import json
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], options="-c default_transaction_read_only=on") as connection:
        if args.export:
            export(connection, ROOT / "analysis/olap_sessions.sql")
            print("Exported analysis/olap_sessions.sql")
        if args.execute:
            print(json.dumps(execute(connection), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
