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
import load_reconciled as reconciler

try:
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.errors import ForeignKeyViolation, UniqueViolation
except ImportError:
    psycopg = None


def country_year(
    iso2="IT",
    iso3="ITA",
    name="Italy",
    year=2022,
    population=58997201,
    gdp=2100000000000,
    gdp_per_capita=35600.4,
    internet=86.1,
    rd=None,
):
    return {
        "country_code_iso3": iso3,
        "country_code_iso2": iso2,
        "country_name": name,
        "region_id": "ECS",
        "region_name": "Europe & Central Asia",
        "income_level_id": "HIC",
        "income_level_name": "High income",
        "year": year,
        "population": population,
        "gdp_current_usd": gdp,
        "gdp_per_capita_current_usd": gdp_per_capita,
        "internet_users_pct": internet,
        "rd_expenditure_pct_gdp": rd,
    }


def topic(topic_id="T1", name="Machine Learning", score=0.9):
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


def institution(institution_id="I1", name="University of Example", country_code="IT"):
    return {
        "id": f"https://openalex.org/{institution_id}",
        "display_name": name,
        "type": "education",
        "country_code": country_code,
    }


def authorship(countries=None, institutions=None):
    return {
        "countries": [] if countries is None else countries,
        "institutions": [] if institutions is None else institutions,
    }


def source(source_id="S1"):
    return {
        "id": f"https://openalex.org/{source_id}",
        "display_name": "Journal of Examples",
        "type": "journal",
        "issn_l": "1234-5678",
    }


def work(
    work_id="W1",
    topics=None,
    primary_topic=None,
    authorships=None,
    publication_source=None,
    doi="https://doi.org/10.1/example",
    cited_by_count=7,
):
    topics = [topic()] if topics is None else topics
    primary_topic = topics[0] if primary_topic is None and topics else primary_topic
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": doi,
        "display_name": f"Example {work_id}",
        "title": f"Example {work_id}",
        "publication_date": "2022-04-05",
        "publication_year": 2022,
        "type": "article",
        "language": "en",
        "cited_by_count": cited_by_count,
        "primary_topic": primary_topic,
        "topics": topics,
        "authorships": [] if authorships is None else authorships,
        "primary_location": {"source": publication_source}
        if publication_source is not None
        else None,
        "open_access": {"is_oa": True, "oa_status": "gold"},
    }


@unittest.skipIf(psycopg is None, "psycopg is required for PostgreSQL integration tests")
class ReconciledLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_dsn = os.getenv("TEST_DATABASE_URL", "postgresql:///postgres")
        cls.database_name = f"reconciled_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
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

    def tearDown(self):
        self.temporary_directory.cleanup()

    def load(self, works, world_bank=None):
        world_bank = [country_year()] if world_bank is None else world_bank
        openalex_path = self.base / "openalex.jsonl"
        world_bank_path = self.base / "world_bank.jsonl"
        openalex_path.write_text(
            "".join(json.dumps(row) + "\n" for row in works), encoding="utf-8"
        )
        world_bank_path.write_text(
            "".join(json.dumps(row) + "\n" for row in world_bank), encoding="utf-8"
        )
        return reconciler.load_reconciled(
            self.database_url,
            openalex_path=openalex_path,
            world_bank_path=world_bank_path,
        )

    def core_fixture(self):
        first_topic = topic("T1", "Machine Learning", 0.95)
        second_topic = topic("T2", "Computer Vision", 0.75)
        italy = institution()
        no_country = institution("I2", "Independent Lab", None)
        shared_source = source()
        works = [
            work(
                "W1",
                topics=[first_topic, second_topic],
                authorships=[
                    authorship(["IT"], [italy]),
                    authorship(["IT"], [italy]),
                    authorship(["US"], []),
                    authorship([], [no_country]),
                ],
                publication_source=shared_source,
                cited_by_count=42,
            ),
            work(
                "W2",
                topics=[first_topic],
                authorships=[authorship(["IT"], [])],
                publication_source=shared_source,
                doi=None,
            ),
            work("W3", topics=[], primary_topic=None, publication_source=None, doi=None),
        ]
        world_bank = [
            country_year(),
            country_year(
                "US",
                "USA",
                "United States",
                population=333287557,
                gdp=None,
                gdp_per_capita=None,
                internet=None,
                rd=None,
            ),
        ]
        return works, world_bank

    def openalex_only_geography_fixture(self):
        works = [
            work(
                "W1",
                authorships=[
                    authorship(
                        ["TW"],
                        [institution("I-TW", "Taiwan Research Institute", "TW")],
                    )
                ],
            ),
            work(
                "W2",
                authorships=[
                    authorship(
                        ["RE"],
                        [institution("I-RE", "Réunion Research Institute", "RE")],
                    )
                ],
            ),
            work(
                "W3",
                authorships=[authorship(["IT", "US", "DE"], [])],
            ),
        ]
        world_bank = [
            country_year(),
            country_year("US", "USA", "United States"),
            country_year("DE", "DEU", "Germany"),
        ]
        return works, world_bank

    def test_schema_mapping_deduplication_and_missing_values(self):
        works, world_bank = self.core_fixture()
        counts = self.load(works, world_bank)
        self.assertEqual(
            counts,
            {
                "r_country": 2,
                "r_topic": 2,
                "r_institution": 2,
                "r_source": 1,
                "r_work": 3,
                "r_work_topic": 3,
                "r_work_country": 3,
                "r_work_institution": 2,
                "r_country_year_indicator": 2,
            },
        )
        with psycopg.connect(self.database_url) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'reconciled'"
                )
            }
            self.assertEqual(tables, set(reconciler.LOAD_ORDER))
            work_row = connection.execute(
                "SELECT cited_by_count, primary_topic_id, source_id, is_open_access "
                "FROM reconciled.r_work WHERE work_id = %s",
                ("https://openalex.org/W1",),
            ).fetchone()
            self.assertEqual(
                work_row,
                (42, "https://openalex.org/T1", "https://openalex.org/S1", True),
            )
            optional = connection.execute(
                "SELECT doi, source_id, primary_topic_id FROM reconciled.r_work "
                "WHERE work_id = %s",
                ("https://openalex.org/W3",),
            ).fetchone()
            self.assertEqual(optional, (None, None, None))

            topic_rows = connection.execute(
                "SELECT topic_id, topic_rank, topic_score FROM reconciled.r_work_topic "
                "WHERE work_id = %s ORDER BY topic_rank",
                ("https://openalex.org/W1",),
            ).fetchall()
            self.assertEqual([row[:2] for row in topic_rows], [
                ("https://openalex.org/T1", 1),
                ("https://openalex.org/T2", 2),
            ])
            self.assertAlmostEqual(topic_rows[0][2], 0.95)
            self.assertEqual(
                set(
                    connection.execute(
                        "SELECT work_id, country_code_iso2 FROM reconciled.r_work_country"
                    ).fetchall()
                ),
                {
                    ("https://openalex.org/W1", "IT"),
                    ("https://openalex.org/W1", "US"),
                    ("https://openalex.org/W2", "IT"),
                },
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT country_code_iso2 FROM reconciled.r_institution "
                    "WHERE institution_id = %s",
                    ("https://openalex.org/I2",),
                ).fetchone()[0]
            )
            us_indicators = connection.execute(
                "SELECT gdp_current_usd, internet_users_pct, rd_expenditure_pct_gdp "
                "FROM reconciled.r_country_year_indicator WHERE country_code_iso2 = 'US'"
            ).fetchone()
            self.assertEqual(us_indicators, (None, None, None))

    def test_open_access_boolean_preserves_true_false_and_null(self):
        true_work = work("W1")
        false_work = work("W2")
        false_work["open_access"] = {"is_oa": False, "oa_status": "closed"}
        unknown_work = work("W3")
        unknown_work["open_access"] = None
        self.load([true_work, false_work, unknown_work])
        with psycopg.connect(self.database_url) as connection:
            values = connection.execute(
                "SELECT is_open_access FROM reconciled.r_work ORDER BY work_id"
            ).fetchall()
        self.assertEqual(values, [(True,), (False,), (None,)])

    def test_primary_topic_must_be_one_of_the_work_topics(self):
        with self.assertRaisesRegex(
            reconciler.ReconciliationError, "is absent from topics"
        ):
            self.load([work(topics=[topic("T1")], primary_topic=topic("T2"))])

    def test_publication_date_and_year_must_agree(self):
        value = work()
        value["publication_year"] = 2021
        with self.assertRaisesRegex(
            reconciler.ReconciliationError, "Publication date/year mismatch"
        ):
            self.load([value])

    def test_primary_topic_with_missing_topic_list_is_rejected(self):
        value = work()
        value["topics"] = None
        with self.assertRaisesRegex(
            reconciler.ReconciliationError, "is absent from topics"
        ):
            self.load([value])

    def test_openalex_only_geographies_are_preserved_without_indicators(self):
        works, world_bank = self.openalex_only_geography_fixture()
        counts = self.load(works, world_bank)
        self.assertEqual(counts["r_country"], 5)
        self.assertEqual(counts["r_country_year_indicator"], 3)
        with psycopg.connect(self.database_url) as connection:
            countries = connection.execute(
                "SELECT country_code_iso2, country_code_iso3, country_name, "
                "region_id, region_name, income_level_id, income_level_name, "
                "has_world_bank_data FROM reconciled.r_country "
                "ORDER BY country_code_iso2"
            ).fetchall()
            by_code = {row[0]: row[1:] for row in countries}
            self.assertEqual(
                by_code["RE"],
                ("REU", "Réunion", None, None, None, None, False),
            )
            self.assertEqual(
                by_code["TW"],
                ("TWN", "Taiwan", None, None, None, None, False),
            )
            self.assertEqual(by_code["IT"][:2], ("ITA", "Italy"))
            self.assertEqual(by_code["US"][:2], ("USA", "United States"))
            self.assertEqual(by_code["DE"][:2], ("DEU", "Germany"))
            self.assertTrue(all(by_code[code][-1] for code in ("IT", "US", "DE")))
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM reconciled.r_country_year_indicator "
                    "WHERE country_code_iso2 IN ('RE', 'TW')"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                set(
                    connection.execute(
                        "SELECT work_id, country_code_iso2 "
                        "FROM reconciled.r_work_country"
                    ).fetchall()
                ),
                {
                    ("https://openalex.org/W1", "TW"),
                    ("https://openalex.org/W2", "RE"),
                    ("https://openalex.org/W3", "IT"),
                    ("https://openalex.org/W3", "US"),
                    ("https://openalex.org/W3", "DE"),
                },
            )
            self.assertEqual(
                set(
                    connection.execute(
                        "SELECT institution_id, country_code_iso2 "
                        "FROM reconciled.r_institution"
                    ).fetchall()
                ),
                {
                    ("https://openalex.org/I-TW", "TW"),
                    ("https://openalex.org/I-RE", "RE"),
                },
            )
            self.assertNotIn("FR", by_code)
            self.assertNotIn("CN", by_code)

    def test_additional_observed_iso_geographies_keep_identity(self):
        codes = {"GF": "GUF", "GP": "GLP", "MQ": "MTQ", "MS": "MSR"}
        self.load(
            [work("W-new-geographies", authorships=[authorship(list(codes), [])])]
        )
        with psycopg.connect(self.database_url) as connection:
            rows = connection.execute(
                "SELECT country_code_iso2, country_code_iso3, has_world_bank_data "
                "FROM reconciled.r_country WHERE country_code_iso2 = ANY(%s)",
                (list(codes),),
            ).fetchall()
            self.assertEqual({r[0]: r[1] for r in rows}, codes)
            self.assertTrue(all(r[2] is False for r in rows))
            count = connection.execute(
                "SELECT count(*) FROM reconciled.r_country_year_indicator "
                "WHERE country_code_iso2 = ANY(%s)",
                (list(codes),),
            ).fetchone()[0]
            self.assertEqual(count, 0)

    def test_unknown_openalex_country_code_is_rejected_without_data_loss(self):
        self.load([work()])
        bad = work("W2", authorships=[authorship(["ZZ"], [])])
        with self.assertRaisesRegex(
            reconciler.ReconciliationError,
            "not recognized by the OpenAlex-only geography policy: ZZ",
        ):
            self.load([bad])
        with psycopg.connect(self.database_url) as connection:
            work_ids = connection.execute(
                "SELECT work_id FROM reconciled.r_work ORDER BY work_id"
            ).fetchall()
        self.assertEqual(work_ids, [("https://openalex.org/W1",)])

    def test_conflicting_institution_metadata_is_rejected(self):
        first = work(
            "W1", authorships=[authorship(["IT"], [institution("I1", "First Name")])]
        )
        second = work(
            "W2", authorships=[authorship(["IT"], [institution("I1", "Other Name")])]
        )
        with self.assertRaisesRegex(
            reconciler.ReconciliationError, "Conflicting institution"
        ):
            self.load([first, second])

    def test_load_is_idempotent(self):
        works, world_bank = self.openalex_only_geography_fixture()
        first = self.load(works, world_bank)
        second = self.load(works, world_bank)
        self.assertEqual(first, second)
        with psycopg.connect(self.database_url) as connection:
            actual = {
                table: connection.execute(
                    f"SELECT count(*) FROM reconciled.{table}"
                ).fetchone()[0]
                for table in reconciler.LOAD_ORDER
            }
        self.assertEqual(actual, first)

    def test_primary_and_relationship_foreign_keys_are_enforced(self):
        self.load([work()])
        with psycopg.connect(self.database_url) as connection:
            with self.assertRaises(ForeignKeyViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO reconciled.r_work_topic "
                    "(work_id, topic_id, topic_rank) VALUES ('missing', %s, 1)",
                    ("https://openalex.org/T1",),
                )
            with self.assertRaises(ForeignKeyViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO reconciled.r_work_country "
                    "(work_id, country_code_iso2) VALUES (%s, 'ZZ')",
                    ("https://openalex.org/W1",),
                )
            with self.assertRaises(ForeignKeyViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO reconciled.r_work_institution "
                    "(work_id, institution_id) VALUES (%s, 'missing')",
                    ("https://openalex.org/W1",),
                )
            with self.assertRaises(ForeignKeyViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO reconciled.r_country_year_indicator "
                    "(country_code_iso2, year) VALUES ('ZZ', 2022)"
                )
            with self.assertRaises(UniqueViolation), connection.transaction():
                connection.execute(
                    "INSERT INTO reconciled.r_work_topic "
                    "(work_id, topic_id, topic_rank) VALUES (%s, %s, 1)",
                    ("https://openalex.org/W1", "https://openalex.org/T1"),
                )

    def test_database_failure_rolls_back_the_schema_rebuild(self):
        self.load([work("W1")])
        openalex_path = self.base / "replacement.jsonl"
        world_bank_path = self.base / "replacement_wb.jsonl"
        openalex_path.write_text(json.dumps(work("W9")) + "\n", encoding="utf-8")
        world_bank_path.write_text(json.dumps(country_year()) + "\n", encoding="utf-8")
        with mock.patch.object(
            reconciler,
            "_run_post_load_checks",
            side_effect=reconciler.ReconciliationError("forced post-load failure"),
        ):
            with self.assertRaisesRegex(
                reconciler.ReconciliationError, "forced post-load failure"
            ):
                reconciler.load_reconciled(
                    self.database_url,
                    openalex_path=openalex_path,
                    world_bank_path=world_bank_path,
                )
        with psycopg.connect(self.database_url) as connection:
            work_ids = connection.execute(
                "SELECT work_id FROM reconciled.r_work ORDER BY work_id"
            ).fetchall()
        self.assertEqual(work_ids, [("https://openalex.org/W1",)])


if __name__ == "__main__":
    unittest.main()
