"""Concrete psycopg implementations of the frozen persistence ports."""

import json
from dataclasses import asdict
from typing import Any

from contracts.models import ExtractionResult, FieldFeedback, JudgeVerdict
from contracts.ports import FeedbackStore, ResultStore
from .connection import ConnectionFactory
from .mapping import (extraction_from_row, feedback_from_row, feedback_values,
                      promoted_columns, verdict_from_dict, verdict_to_dict)
from .sql import (GET_EXTRACTION_SQL, GET_VERDICT_SQL, INSERT_FEEDBACK_SQL,
                  LIST_FEEDBACK_SQL, UPSERT_EXTRACTION_SQL, UPSERT_VERDICT_SQL,
                  DDL)


def init_tables(connect: ConnectionFactory) -> None:
    """Create the persistence tables and indexes before stores are exposed."""
    statements = (part.strip() for part in DDL.split(";") if part.strip())
    with connect() as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


class _Store:
    def __init__(self, connect: ConnectionFactory) -> None:
        self._connect = connect

    def _execute(self, statement: str, params: tuple[Any, ...], *, fetch: bool = False) -> Any:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement, params)
                return cursor.fetchone() if fetch else None


class LakebaseResultStore(_Store, ResultStore):
    def save_extraction(self, result: ExtractionResult) -> None:
        params = (result.request_id, json.dumps(result.payload), result.model_id,
                  result.latency_ms, json.dumps(asdict(result.token_usage)),
                  result.raw_response_id, result.schema_valid, *promoted_columns(result.payload))
        self._execute(UPSERT_EXTRACTION_SQL, params)

    def save_verdict(self, verdict: JudgeVerdict) -> None:
        self._execute(UPSERT_VERDICT_SQL, (verdict.request_id, json.dumps(verdict_to_dict(verdict))))

    def get_extraction(self, request_id: str) -> ExtractionResult | None:
        row = self._execute(GET_EXTRACTION_SQL, (request_id,), fetch=True)
        return extraction_from_row(row) if row else None

    def get_verdict(self, request_id: str) -> JudgeVerdict | None:
        row = self._execute(GET_VERDICT_SQL, (request_id,), fetch=True)
        return verdict_from_dict(row[0]) if row else None


class LakebaseFeedbackStore(_Store, FeedbackStore):
    def append_feedback(self, feedback: FieldFeedback) -> None:
        values = feedback_values(feedback)
        self._execute(INSERT_FEEDBACK_SQL, (values[0], values[1], json.dumps(values[2]),
                                            json.dumps(values[3]), *values[4:]))

    def list_feedback(self, request_id: str) -> list[FieldFeedback]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(LIST_FEEDBACK_SQL, (request_id,))
                return [feedback_from_row(row) for row in cursor.fetchall()]
