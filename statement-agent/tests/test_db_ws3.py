import os
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

from contracts.models import (Bank, ComparisonOutcome, ExtractionResult,
    FieldComparison, FieldFeedback, FieldScope, JudgeVerdict, MatchMethod,
    TokenUsage)
from db.mapping import (feedback_from_row, feedback_values, promoted_columns,
                        verdict_from_dict, verdict_to_dict)
from db.sql import DDL, UPSERT_EXTRACTION_SQL, current_state_view_sql
from db.config_ws3 import RDSSettings
from db.stores import LakebaseResultStore, init_tables
from db.connection import RDSConnectionFactory

# db.provision is a standalone Lakebase CLI (not on the runtime path) that
# still imports the legacy LakebaseSettings; skip its tests if that import
# fails (the settings class was removed in favour of RDSSettings).
try:
    from db.provision import CdfCreateError, ensure_cdf, resolve_database_resource
    from db.config_ws3 import LakebaseSettings
    _PROVISION_AVAILABLE = True
except ImportError:
    _PROVISION_AVAILABLE = False


class LakebaseSqlTests(unittest.TestCase):
    def test_save_extraction_binds_request_bank_to_promoted_column(self):
        executed = []

        class CapturingStore(LakebaseResultStore):
            def _execute(self, statement, params, *, fetch=False):
                executed.append((statement, params))

        result = ExtractionResult(
            "req-bank", {"statementDate": "2026-01-02"}, "luna", 12.5,
            TokenUsage(), schema_valid=True,
        )
        CapturingStore(None).save_extraction(result, Bank.ICICI)

        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0][1][7], "ICICI")

    def test_connection_factory_uses_direct_password(self):
        """RDSConnectionFactory connects with a plain password (no token API)."""
        connect_calls = []

        class Psycopg:
            @staticmethod
            def connect(**kwargs):
                connect_calls.append(kwargs)
                return object()

        factory = RDSConnectionFactory(
            host="db.example.com", database="postgres", user="app",
            password="secret",
        )
        with patch.dict("sys.modules", {"psycopg": Psycopg}):
            factory()

        self.assertEqual(connect_calls[0]["password"], "secret")
        self.assertEqual(connect_calls[0]["host"], "db.example.com")
        self.assertEqual(connect_calls[0]["dbname"], "postgres")
        self.assertEqual(connect_calls[0]["user"], "app")
        self.assertEqual(connect_calls[0]["port"], 5432)
        self.assertEqual(connect_calls[0]["sslmode"], "require")

    def test_connection_factory_rejects_empty_host(self):
        with self.assertRaisesRegex(RuntimeError, "host must not be null or empty"):
            RDSConnectionFactory(host="  ", database="db", user="user", password="pw")

    def test_connection_factory_from_env(self):
        """RDSConnectionFactory.from_env reads RDS_* env vars."""
        env = {
            "RDS_HOST": "db.example.com", "RDS_PORT": "5432",
            "RDS_DATABASE": "mydb", "RDS_USER": "app",
            "RDS_PASSWORD": "secret", "RDS_SSLMODE": "require",
        }
        with patch.dict(os.environ, env, clear=False):
            factory = RDSConnectionFactory.from_env()
        self.assertEqual(factory._host, "db.example.com")
        self.assertEqual(factory._port, 5432)
        self.assertEqual(factory._database, "mydb")
        self.assertEqual(factory._user, "app")
        self.assertEqual(factory._password, "secret")
        self.assertEqual(factory._sslmode, "require")

    def test_connection_factory_from_env_missing_var(self):
        """from_env raises RuntimeError when required vars are missing."""
        with patch.dict(os.environ, {
            "RDS_HOST": "", "RDS_DATABASE": "", "RDS_USER": "", "RDS_PASSWORD": "",
        }):
            with self.assertRaisesRegex(RuntimeError, "RDS connection requires"):
                RDSConnectionFactory.from_env()

    def test_connection_factory_from_env_missing_database(self):
        """from_env raises RuntimeError when RDS_DATABASE is missing."""
        env = {
            "RDS_HOST": "db.example.com", "RDS_DATABASE": "",
            "RDS_USER": "app", "RDS_PASSWORD": "secret",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(RuntimeError, "RDS_DATABASE"):
                RDSConnectionFactory.from_env()

    def test_ddl_has_separate_tables_and_replica_identity(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS statement_results", DDL)
        self.assertIn("CREATE TABLE IF NOT EXISTS field_feedback", DDL)
        self.assertEqual(DDL.count("REPLICA IDENTITY FULL"), 2)
        self.assertIn("pg_advisory_xact_lock", DDL)

    def test_dml_uses_placeholders(self):
        self.assertIn("VALUES (%s, %s::jsonb", UPSERT_EXTRACTION_SQL)

    def test_current_view_orders_by_wal_and_filters_delete(self):
        sql = current_state_view_sql("cat.schema.lb_rows_history", ("request_id",))
        self.assertIn("`_pg_lsn` DESC, `_sort_by` DESC", sql)
        self.assertIn("`_pg_change_type` <> 'delete'", sql)
        self.assertNotIn("_timestamp", sql)

    def test_current_view_rejects_identifier_injection(self):
        with self.assertRaises(ValueError):
            current_state_view_sql("history; DROP TABLE x", ("request_id",))

    def test_init_tables_executes_all_ddl_in_one_connection(self):
        executed = []

        class Resource:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return self

            def execute(self, statement):
                executed.append(statement)

            def fetchone(self):
                return (None, None)

        connections = []

        def connect():
            resource = Resource()
            connections.append(resource)
            return resource

        init_tables(connect)
        self.assertEqual(len(connections), 1)
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS statement_results" in s
                            for s in executed))
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS field_feedback" in s
                            for s in executed))

    def test_init_tables_skips_ddl_when_tables_exist(self):
        executed = []

        class Resource:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def cursor(self):
                return self

            def execute(self, statement):
                executed.append(statement)

            def fetchone(self):
                return ("statement_results", "field_feedback")

        init_tables(Resource)
        self.assertEqual(len(executed), 1)
        self.assertIn("to_regclass", executed[0])


class LakebaseMappingTests(unittest.TestCase):
    def test_promoted_columns(self):
        payload = {"bank": "SYNTHETIC BANK", "statementDate": "2026-01-02",
            "cards": [{"cardMeta": {"cardDisplayName": "Synthetic Card", "lastFourDigit": "0000"}}],
            "rewards": {"pointsEarnedThisCycle": "1,250", "closingPoints": 3000}}
        self.assertEqual(promoted_columns(payload),
            ("SYNTHETIC BANK", "2026-01-02", "Synthetic Card", "0000", Decimal("1250"), Decimal("3000")))

    def test_verdict_round_trip(self):
        comparison = FieldComparison("rewards.closingPoints", 10, 9,
            ComparisonOutcome.DISAGREE, FieldScope.SCALAR, MatchMethod.DIRECT)
        verdict = JudgeVerdict("synthetic-request", "synthetic-judge", (comparison,), 4.0)
        self.assertEqual(verdict_from_dict(verdict_to_dict(verdict)), verdict)

    def test_feedback_round_trip_and_path_validation(self):
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        feedback = FieldFeedback("synthetic-request", "transactions.0.amount", 1, 2,
                                 False, "synthetic@example.invalid", timestamp)
        self.assertEqual(feedback_values(feedback)[1], "transactions.0.amount")
        row = (feedback.request_id, feedback.field_path, feedback.original_value,
               feedback.corrected_value, feedback.accepted, feedback.actor, timestamp.isoformat())
        self.assertEqual(feedback_from_row(row), feedback)


class _ApiClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def do(self, verb, path, **kwargs):
        self.calls.append((verb, path, kwargs))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class _Client:
    def __init__(self, responses):
        self.api_client = _ApiClient(responses)


class _NotFound(Exception):
    error_code = "NOT_FOUND"


@unittest.skipUnless(_PROVISION_AVAILABLE, "db.provision requires legacy LakebaseSettings")
class LakebaseProvisioningTests(unittest.TestCase):
    def test_resolves_rest_id_from_sql_database_name(self):
        client = _Client([{"databases": [{"database_id": "databricks-postgres",
            "name": "projects/p/branches/production/databases/databricks-postgres",
            "status": {"postgres_database": "databricks_postgres"}}]}])
        with redirect_stdout(StringIO()):
            database_id = resolve_database_resource(client, LakebaseSettings(), "production")
        self.assertEqual(database_id, "databricks-postgres")

    def test_probe_not_found_then_create_uses_discovered_resource_id(self):
        created = {"response": {"name": "cdf-configs/public"}}
        client = _Client([_NotFound("not configured"), created])
        with redirect_stdout(StringIO()):
            result = ensure_cdf(client, LakebaseSettings(), "production", "databricks-postgres")
        self.assertEqual(result, created)
        create = client.api_client.calls[1]
        self.assertEqual(create[0], "POST")
        self.assertIn("/databases/databricks-postgres/cdf-configs", create[1])
        self.assertEqual(create[2]["query"], {"cdf_config_id": "public"})

    def test_create_failure_is_distinct_from_probe_not_found(self):
        client = _Client([_NotFound("not configured"), _NotFound("bad parent")])
        with redirect_stdout(StringIO()):
            with self.assertRaisesRegex(CdfCreateError, "CDF create failed"):
                ensure_cdf(client, LakebaseSettings(), "production", "wrong-id")


if __name__ == "__main__":
    unittest.main()
