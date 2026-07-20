BEGIN;

ALTER TABLE analysis_jobs_v1
ADD COLUMN IF NOT EXISTS feature_matrix_version TEXT;

COMMENT ON COLUMN analysis_jobs_v1.feature_matrix_version IS
'Version of the regional feature matrix associated with the immutable catalog snapshot; NULL when the selected catalog has no registered feature matrix.';

COMMIT;
