"""Static PostgreSQL DDL/DML and Unity Catalog current-state SQL."""

DDL = """
SELECT pg_advisory_xact_lock(74231103);
CREATE TABLE IF NOT EXISTS statement_results (
    request_id text PRIMARY KEY,
    extraction_payload jsonb,
    extraction_model_id text,
    extraction_latency_ms double precision,
    extraction_token_usage jsonb,
    extraction_raw_response_id text,
    extraction_schema_valid boolean,
    bank text,
    statement_date date,
    card_display_name text,
    last_four_digits text,
    points_earned_this_cycle numeric,
    closing_points numeric,
    verdict_payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS field_feedback (
    feedback_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id text NOT NULL REFERENCES statement_results(request_id),
    field_path text NOT NULL,
    original_value jsonb,
    corrected_value jsonb,
    accepted boolean NOT NULL,
    actor text NOT NULL,
    feedback_timestamp timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS field_feedback_request_id_idx
    ON field_feedback (request_id, feedback_timestamp, feedback_id);
ALTER TABLE statement_results REPLICA IDENTITY FULL;
ALTER TABLE field_feedback REPLICA IDENTITY FULL;
""".strip()

UPSERT_EXTRACTION_SQL = """
INSERT INTO statement_results (
    request_id, extraction_payload, extraction_model_id, extraction_latency_ms,
    extraction_token_usage, extraction_raw_response_id, extraction_schema_valid,
    bank, statement_date, card_display_name, last_four_digits,
    points_earned_this_cycle, closing_points
) VALUES (%s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (request_id) DO UPDATE SET
    extraction_payload = EXCLUDED.extraction_payload,
    extraction_model_id = EXCLUDED.extraction_model_id,
    extraction_latency_ms = EXCLUDED.extraction_latency_ms,
    extraction_token_usage = EXCLUDED.extraction_token_usage,
    extraction_raw_response_id = EXCLUDED.extraction_raw_response_id,
    extraction_schema_valid = EXCLUDED.extraction_schema_valid,
    bank = EXCLUDED.bank, statement_date = EXCLUDED.statement_date,
    card_display_name = EXCLUDED.card_display_name,
    last_four_digits = EXCLUDED.last_four_digits,
    points_earned_this_cycle = EXCLUDED.points_earned_this_cycle,
    closing_points = EXCLUDED.closing_points, updated_at = now()
""".strip()

UPSERT_VERDICT_SQL = """INSERT INTO statement_results (request_id, verdict_payload)
VALUES (%s, %s::jsonb) ON CONFLICT (request_id) DO UPDATE SET
verdict_payload = EXCLUDED.verdict_payload, updated_at = now()"""
GET_EXTRACTION_SQL = """SELECT request_id, extraction_payload, extraction_model_id,
extraction_latency_ms, extraction_token_usage, extraction_raw_response_id,
extraction_schema_valid FROM statement_results
WHERE request_id = %s AND extraction_payload IS NOT NULL"""
GET_VERDICT_SQL = "SELECT verdict_payload FROM statement_results WHERE request_id = %s AND verdict_payload IS NOT NULL"
INSERT_FEEDBACK_SQL = """INSERT INTO field_feedback
(request_id, field_path, original_value, corrected_value, accepted, actor, feedback_timestamp)
VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)"""
LIST_FEEDBACK_SQL = """SELECT request_id, field_path, original_value, corrected_value,
accepted, actor, feedback_timestamp FROM field_feedback WHERE request_id = %s
ORDER BY feedback_timestamp, feedback_id"""


def current_state_view_sql(source_table: str, primary_keys: tuple[str, ...]) -> str:
    """Return Delta SQL reducing CDF history by WAL order, including deletes."""
    if not source_table or not all(part.replace("_", "").isalnum() for part in source_table.split(".")):
        raise ValueError("source_table must be a dot-qualified SQL identifier")
    if not primary_keys or not all(key.replace("_", "").isalnum() for key in primary_keys):
        raise ValueError("primary_keys must contain SQL identifiers")
    partition = ", ".join(f"`{key}`" for key in primary_keys)
    return f"""CREATE OR REPLACE VIEW {source_table}_current AS
WITH ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY {partition}
    ORDER BY `_pg_lsn` DESC, `_sort_by` DESC
  ) AS `_cdf_rank`
  FROM {source_table}
)
SELECT * EXCEPT (`_cdf_rank`)
FROM ranked
WHERE `_cdf_rank` = 1 AND `_pg_change_type` <> 'delete'"""
