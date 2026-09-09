import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reconciliation"))
sys.path.insert(0, str(ROOT / "warehouse"))
import load_reconciled as reconciler  # noqa: E402
import load_dw as warehouse  # noqa: E402

try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.errors import ForeignKeyViolation
except ImportError:
    psycopg = None


def country_year(
    iso2,
    iso3,
    name,
    year,
    population,
    gdp,
    internet=80.0,
    rd=2.0,
):
    return {
        "country_code_iso3": iso3,
        "country_code_iso2": iso2,
        "country_name": name,
        "region_id": "ECS" if iso2 in {"IT", "DE"} else "NAC",
        "region_name": "Europe & Central Asia"
        if iso2 in {"IT", "DE"}
        else "North America",
        "income_level_id": "HIC",
        "income_level_name": "High income",
        "year": year,
        "population": population,
        "gdp_current_usd": gdp,
        "gdp_per_capita_current_usd": gdp / population if gdp is not None else None,
        "internet_users_pct": internet,
        "rd_expenditure_pct_gdp": rd,
    }


def topic(topic_id, name, score):
    return {
        "id": f"https://openalex.org/{topic_id}",
        "display_name": name,
        "score": score,
        "subfield": {
            "id": "https://openalex.org/subfields/1702",
            "display_name": "Artificial Intelligence",
        },
        "field": {
            "id": "https://openalex.org/fields/17",
            "display_name": "Computer Science",
        },
        "domain": {
            "id": "https://openalex.org/domains/3",
            "display_name": "Physical Sciences",
        },
    }


def institution(institution_id, name, country_code):
    return {
        "id": f"https://openalex.org/{institution_id}",
        "display_name": name,
        "type": "education",
        "country_code": country_code,
    }


def authorship(countries, institutions):
    return {"countries": countries, "institutions": institutions}


def source():
    return {
        "id": "https://openalex.org/S1",
        "display_name": "Journal of Examples",
        "type": "journal",
        "issn_l": "1234-5678",
    }


def work(
    work_id,
    publication_date,
    work_type,
    citation_count,
    topics,
    authorships,
    publication_source,
):
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": None,
        "display_name": f"Example {work_id}",
        "title": f"Example {work_id}",
        "publication_date": publication_date,
        "publication_year": int(publication_date[:4]),
        "type": work_type,
        "language": "en",
        "cited_by_count": citation_count,
        "primary_topic": topics[0],
        "topics": topics,
        "authorships": authorships,
        "primary_location": {"source": publication_source}
        if publication_source is not None
        else None,
        "open_access": {"is_oa": True, "oa_status": "gold"},
    }


@unittest.skipIf(psycopg is None, "psycopg is required for PostgreSQL integration tests")
class WarehouseLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_dsn = os.getenv("TEST_DATABASE_URL", "postgresql:///postgres")
        cls.database_name = f"warehouse_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        try:
            cls.admin_connection = psycopg.connect(cls.admin_dsn, autocommit=True)
            cls.admin_connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cls.database_name))
            )
        except Exception as exc:
            if getattr(cls, "admin_connection", None):
                cls.admin_connection.close()
            raise unittest.SkipTest(
                f"isolated PostgreSQL test database is unavailable: {exc}"
            ) from exc
        cls.database_url = make_conninfo(cls.admin_dsn, dbname=cls.database_name)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "admin_connection", None):
            cls.admin_connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(cls.database_name)
                )
            )
            cls.admin_connection.close()

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self._load_reconciled_fixture()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _load_reconciled_fixture(self):
        topic_one = topic("T1", "Machine Learning", 0.95)
        topic_two = topic("T2", "Computer Vision", 0.75)
        works = [
            work(
                "W1",
                "2022-04-05",
                "article",
                10,
                [topic_one, topic_two],
                [
                    authorship(
                        ["IT", "TW"],
                        [
                            institution("I-IT", "Italian University", "IT"),
                            institution("I-TW", "Taiwan University", "TW"),
                        ],
                    )
                ],
                source(),
            ),
            work(
                "W2",
                "2022-10-01",
                "review",
                4,
                [topic_one],
                [
                    authorship(
                        ["RE"],
                        [institution("I-RE", "Réunion University", "RE")],
                    )
                ],
                None,
            ),
            work(
                "W3",
                "2023-01-15",
                "preprint",
                2,
                [topic_two],
                [
                    authorship(
                        ["US", "DE"],
                        [institution("I-US", "United States University", "US")],
                    )
                ],
                source(),
            ),
        ]
        # Keep a non-rank-1 primary topic to verify that the DW preserves the
        # source relationship explicitly instead of inferring it from rank.
        works[0]["primary_topic"] = topic_two
        world_bank = [
            country_year("IT", "ITA", "Italy", 2022, 60_000_000, 2_000_000_000_000),
            country_year(
                "IT", "ITA", "Italy", 2023, 59_000_000, 2_100_000_000_000, internet=None
            ),
            country_year("US", "USA", "United States", 2023, 335_000_000, 27_000_000_000_000),
            country_year("DE", "DEU", "Germany", 2023, 84_000_000, 4_500_000_000_000),
        ]
        openalex_path = self.base / "openalex.jsonl"
        world_bank_path = self.base / "world_bank.jsonl"
        openalex_path.write_text(
            "".join(json.dumps(row) + "\n" for row in works), encoding="utf-8"
        )
        world_bank_path.write_text(
            "".join(json.dumps(row) + "\n" for row in world_bank), encoding="utf-8"
        )
        reconciler.load_reconciled(
            self.database_url,
            openalex_path=openalex_path,
            world_bank_path=world_bank_path,
        )

    def _reconciled_snapshot(self):
        with psycopg.connect(self.database_url) as connection:
            return {
                table: connection.execute(
                    f"SELECT * FROM reconciled.{table} ORDER BY 1, 2"
                ).fetchall()
                for table in reconciler.LOAD_ORDER
            }

    def test_schema_surrogate_keys_date_and_publication_grain(self):
        counts = warehouse.load_dw(self.database_url)
        self.assertEqual(counts["fact_publication"], 3)
        with psycopg.connect(self.database_url) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'dw'"
                )
            }
            self.assertEqual(tables, set(warehouse.DW_TABLES))
            for table, key in (
                ("dim_year", "year_key"),
                ("dim_country", "country_key"),
                ("dim_topic", "topic_key"),
                ("dim_institution", "institution_key"),
                ("dim_source", "source_key"),
                ("fact_publication", "publication_key"),
                ("fact_country_year", "country_year_key"),
            ):
                row_count, unique_keys, minimum_key = connection.execute(
                    f"SELECT count(*), count(DISTINCT {key}), min({key}) FROM dw.{table}"
                ).fetchone()
                self.assertEqual(row_count, unique_keys)
                self.assertGreater(minimum_key, 0)

            date_row = connection.execute(
                "SELECT date_key, day_of_month, month_number, quarter_number, calendar_year "
                "FROM dw.dim_date WHERE full_date = DATE '2022-04-05'"
            ).fetchone()
            self.assertEqual(date_row, (20220405, 5, 4, 2, 2022))
            publications = connection.execute(
                "SELECT openalex_work_id, publication_type, publication_count, "
                "citation_count, source_key FROM dw.fact_publication "
                "ORDER BY openalex_work_id"
            ).fetchall()
            self.assertEqual([row[0] for row in publications], [
                "https://openalex.org/W1",
                "https://openalex.org/W2",
                "https://openalex.org/W3",
            ])
            self.assertEqual([row[1] for row in publications], ["article", "review", "preprint"])
            self.assertEqual([row[2] for row in publications], [1, 1, 1])
            self.assertEqual([row[3] for row in publications], [10, 4, 2])
            self.assertIsNotNone(publications[0][4])
            self.assertIsNone(publications[1][4])

    def test_dimensions_bridges_country_coverage_and_missing_indicators(self):
        counts = warehouse.load_dw(self.database_url)
        self.assertEqual(counts["dim_country"], 5)
        self.assertEqual(counts["fact_country_year"], 4)
        self.assertEqual(counts["bridge_publication_topic"], 4)
        self.assertEqual(counts["bridge_publication_country"], 5)
        self.assertEqual(counts["bridge_publication_institution"], 4)
        with psycopg.connect(self.database_url) as connection:
            countries = {
                row[0]: row[1:]
                for row in connection.execute(
                    "SELECT country_code_iso2, country_code_iso3, country_name, "
                    "has_world_bank_data FROM dw.dim_country"
                )
            }
            self.assertEqual(countries["RE"], ("REU", "Réunion", False))
            self.assertEqual(countries["TW"], ("TWN", "Taiwan", False))
            self.assertTrue(all(countries[code][-1] for code in ("IT", "US", "DE")))
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM dw.fact_country_year fact "
                    "JOIN dw.dim_country country ON country.country_key = fact.country_key "
                    "WHERE country.country_code_iso2 IN ('RE', 'TW')"
                ).fetchone()[0],
                0,
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT fact.internet_users_pct FROM dw.fact_country_year fact "
                    "JOIN dw.dim_country country ON country.country_key = fact.country_key "
                    "JOIN dw.dim_year year_dim ON year_dim.year_key = fact.year_key "
                    "WHERE country.country_code_iso2 = 'IT' "
                    "AND year_dim.calendar_year = 2023"
                ).fetchone()[0]
            )
            topic_hierarchy = connection.execute(
                "SELECT subfield_name, field_name, domain_name FROM dw.dim_topic "
                "WHERE openalex_topic_id = 'https://openalex.org/T1'"
            ).fetchone()
            self.assertEqual(
                topic_hierarchy,
                ("Artificial Intelligence", "Computer Science", "Physical Sciences"),
            )
            publication_key = connection.execute(
                "SELECT publication_key FROM dw.fact_publication "
                "WHERE openalex_work_id = 'https://openalex.org/W1'"
            ).fetchone()[0]
            topic_weights = connection.execute(
                "SELECT topic_rank, topic_score, is_primary_topic, fractional_weight "
                "FROM dw.bridge_publication_topic WHERE publication_key = %s "
                "ORDER BY topic_rank",
                (publication_key,),
            ).fetchall()
            self.assertEqual([row[0] for row in topic_weights], [1, 2])
            self.assertAlmostEqual(topic_weights[0][1], 0.95)
            self.assertEqual([row[2] for row in topic_weights], [False, True])
            self.assertEqual([float(row[3]) for row in topic_weights], [0.5, 0.5])
            country_codes = connection.execute(
                "SELECT country.country_code_iso2, bridge.fractional_weight "
                "FROM dw.bridge_publication_country bridge "
                "JOIN dw.dim_country country ON country.country_key = bridge.country_key "
                "WHERE bridge.publication_key = %s ORDER BY country.country_code_iso2",
                (publication_key,),
            ).fetchall()
            self.assertEqual(country_codes, [("IT", 0.5), ("TW", 0.5)])
            institution_codes = connection.execute(
                "SELECT institution.country_code_iso2, bridge.fractional_weight "
                "FROM dw.bridge_publication_institution bridge "
                "JOIN dw.dim_institution institution "
                "ON institution.institution_key = bridge.institution_key "
                "WHERE bridge.publication_key = %s ORDER BY institution.country_code_iso2",
                (publication_key,),
            ).fetchall()
            self.assertEqual(institution_codes, [("IT", 0.5), ("TW", 0.5)])

    def test_foreign_keys_reject_orphan_bridge_rows(self):
        warehouse.load_dw(self.database_url)
        with psycopg.connect(self.database_url) as connection:
            with self.assertRaises(ForeignKeyViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO dw.bridge_publication_country "
                    "(publication_key, country_key, fractional_weight) "
                    "VALUES (-1, -1, 1)"
                )

    def test_idempotency_and_reconciled_immutability(self):
        before = self._reconciled_snapshot()
        first_counts = warehouse.load_dw(self.database_url)
        with psycopg.connect(self.database_url) as connection:
            first_keys = connection.execute(
                "SELECT country_key, country_code_iso2 FROM dw.dim_country "
                "ORDER BY country_code_iso2"
            ).fetchall()
        second_counts = warehouse.load_dw(self.database_url)
        with psycopg.connect(self.database_url) as connection:
            second_keys = connection.execute(
                "SELECT country_key, country_code_iso2 FROM dw.dim_country "
                "ORDER BY country_code_iso2"
            ).fetchall()
        after = self._reconciled_snapshot()
        self.assertEqual(first_counts, second_counts)
        self.assertEqual(first_keys, second_keys)
        self.assertEqual(before, after)

    def test_missing_reconciled_contract_is_rejected_before_dw_reset(self):
        with psycopg.connect(self.database_url) as connection:
            connection.execute("DROP SCHEMA reconciled CASCADE")
        with self.assertRaisesRegex(
            warehouse.WarehouseError, "Required reconciled tables or columns are missing"
        ):
            warehouse.load_dw(self.database_url)

    def test_failed_post_load_check_rolls_back_complete_dw_rebuild(self):
        warehouse.load_dw(self.database_url)
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "INSERT INTO dw.dim_source "
                "(openalex_source_id, source_name) VALUES ('sentinel', 'Sentinel')"
            )
        with mock.patch.object(
            warehouse,
            "_run_post_load_checks",
            side_effect=warehouse.WarehouseError("forced warehouse failure"),
        ):
            with self.assertRaisesRegex(warehouse.WarehouseError, "forced warehouse failure"):
                warehouse.load_dw(self.database_url)
        with psycopg.connect(self.database_url) as connection:
            sentinel_count = connection.execute(
                "SELECT count(*) FROM dw.dim_source "
                "WHERE openalex_source_id = 'sentinel'"
            ).fetchone()[0]
        self.assertEqual(sentinel_count, 1)

    def test_olap_smoke_queries_execute_and_normalization_stays_null(self):
        warehouse.load_dw(self.database_url)
        sql_text = (ROOT / "analysis/olap_smoke.sql").read_text(encoding="utf-8")
        statements = [statement.strip() for statement in sql_text.split(";") if statement.strip()]
        self.assertEqual(len(statements), 7)
        with psycopg.connect(self.database_url) as connection:
            results = [connection.execute(statement).fetchall() for statement in statements]

        self.assertEqual(results[0], [(2022, 2, 14), (2023, 1, 2)])
        self.assertEqual({row[0] for row in results[1]}, {"IT", "US", "DE", "RE", "TW"})
        self.assertEqual(
            {row[0]: row[3] for row in results[2]},
            {"https://openalex.org/T1": 2, "https://openalex.org/T2": 2},
        )
        self.assertEqual(results[3], [("education", 4)])
        self.assertEqual(results[4], [("journal", 2), ("<missing>", 1)])
        self.assertEqual(
            {row[0]: row[1] for row in results[5]},
            {"article": 1, "review": 1, "preprint": 1},
        )
        drill_across = {row[0]: row for row in results[6]}
        self.assertEqual(float(drill_across["IT"][3]), 0.5)
        self.assertIsNotNone(drill_across["IT"][6])
        self.assertIsNotNone(drill_across["IT"][7])
        for code in ("RE", "TW"):
            self.assertIsNone(drill_across[code][4])
            self.assertIsNone(drill_across[code][5])
            self.assertIsNone(drill_across[code][6])
            self.assertIsNone(drill_across[code][7])

        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                "UPDATE dw.fact_country_year fact SET population = 0, gdp_current_usd = 0 "
                "FROM dw.dim_country country, dw.dim_year year_dim "
                "WHERE country.country_key = fact.country_key "
                "AND year_dim.year_key = fact.year_key "
                "AND country.country_code_iso2 = 'IT' "
                "AND year_dim.calendar_year = 2022"
            )
            zero_denominator_rows = connection.execute(statements[6]).fetchall()
        italy_2022 = next(
            row
            for row in zero_denominator_rows
            if row[0] == "IT" and row[2] == 2022
        )
        self.assertIsNone(italy_2022[6])
        self.assertIsNone(italy_2022[7])


if __name__ == "__main__":
    unittest.main()
