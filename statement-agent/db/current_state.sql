-- CDF is append-only history. These views expose the latest non-deleted row.
-- PostgreSQL WAL LSN plus the connector's within-LSN sort key is authoritative;
-- _timestamp is deliberately not used because batching and clock time can reorder it.
CREATE OR REPLACE VIEW stable_classic_7ppxjq_catalog.savesage.lb_statement_results_history_current AS
WITH ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY request_id ORDER BY `_pg_lsn` DESC, `_sort_by` DESC
  ) AS `_cdf_rank`
  FROM stable_classic_7ppxjq_catalog.savesage.lb_statement_results_history
)
SELECT * EXCEPT (`_cdf_rank`) FROM ranked
WHERE `_cdf_rank` = 1 AND `_pg_change_type` <> 'delete';

CREATE OR REPLACE VIEW stable_classic_7ppxjq_catalog.savesage.lb_field_feedback_history_current AS
WITH ranked AS (
  SELECT *, row_number() OVER (
    PARTITION BY feedback_id ORDER BY `_pg_lsn` DESC, `_sort_by` DESC
  ) AS `_cdf_rank`
  FROM stable_classic_7ppxjq_catalog.savesage.lb_field_feedback_history
)
SELECT * EXCEPT (`_cdf_rank`) FROM ranked
WHERE `_cdf_rank` = 1 AND `_pg_change_type` <> 'delete';
