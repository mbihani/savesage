import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO

from contracts.models import (ComparisonOutcome, FieldComparison, FieldFeedback,
    FieldScope, JudgeVerdict, MatchMethod)
from db.mapping import (feedback_from_row, feedback_values, promoted_columns,
                        verdict_from_dict, verdict_to_dict)
from db.sql import DDL, UPSERT_EXTRACTION_SQL, current_state_view_sql
from db.config_ws3 import LakebaseSettings
from db.provision import CdfCreateError, ensure_cdf, resolve_database_resource


class LakebaseSqlTests(unittest.TestCase):
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
