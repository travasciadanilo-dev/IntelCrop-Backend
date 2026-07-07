-- ============================================================
-- 014_regional_reliability_calibration_schema.sql
-- IntelCrop Calabria
--
-- Scopo:
-- predisporre lo schema di versionamento per la calibrazione
-- del regional_reliability_score.
--
-- Non calcola ancora lo score.
-- Registra modello, coefficienti, soglie, metriche CV,
-- bootstrap CI, validazione spaziale e calibrazione.
-- ============================================================


CREATE TABLE IF NOT EXISTS regional_reliability_model_runs (
    model_version text PRIMARY KEY,

    model_name text NOT NULL DEFAULT 'regional_reliability_score',
    feature_matrix_version text NOT NULL DEFAULT 'area_feature_matrix_v1',

    source_layer_version text NOT NULL DEFAULT 'cut_calabria_v1',

    algorithm text NOT NULL DEFAULT 'penalized_logistic_regression',
    penalty text NOT NULL DEFAULT 'l2',
    class_weight text NOT NULL DEFAULT 'balanced',

    training_n integer NOT NULL,
    positive_n integer NOT NULL,
    negative_n integer NOT NULL,
    uncertain_n integer NOT NULL DEFAULT 0,

    n_features integer NOT NULL,

    repeated_cv_folds integer NOT NULL DEFAULT 5,
    repeated_cv_repeats integer NOT NULL DEFAULT 20,
    bootstrap_iterations integer NOT NULL DEFAULT 1000,

    spatial_validation_strategy text NOT NULL DEFAULT 'leave_one_zone_out',

    mean_precision numeric,
    mean_recall numeric,
    mean_specificity numeric,
    mean_f1 numeric,
    mean_accuracy numeric,
    mean_roc_auc numeric,
    mean_brier_score numeric,

    precision_ci95_lower numeric,
    precision_ci95_upper numeric,

    recall_ci95_lower numeric,
    recall_ci95_upper numeric,

    specificity_ci95_lower numeric,
    specificity_ci95_upper numeric,

    f1_ci95_lower numeric,
    f1_ci95_upper numeric,

    accuracy_ci95_lower numeric,
    accuracy_ci95_upper numeric,

    roc_auc_ci95_lower numeric,
    roc_auc_ci95_upper numeric,

    brier_ci95_lower numeric,
    brier_ci95_upper numeric,

    calibration_slope numeric,
    calibration_intercept numeric,

    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'experimental', 'validated', 'operational', 'archived')),

    limitations text NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),

    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);


CREATE TABLE IF NOT EXISTS regional_reliability_model_coefficients (
    id bigserial PRIMARY KEY,

    model_version text NOT NULL REFERENCES regional_reliability_model_runs(model_version)
        ON DELETE CASCADE,

    feature_name text NOT NULL,
    coefficient_value numeric NOT NULL,
    feature_mean numeric,
    feature_std numeric,

    created_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (model_version, feature_name)
);


CREATE TABLE IF NOT EXISTS regional_reliability_model_thresholds (
    id bigserial PRIMARY KEY,

    model_version text NOT NULL REFERENCES regional_reliability_model_runs(model_version)
        ON DELETE CASCADE,

    class_code text NOT NULL,
    class_label_it text NOT NULL,

    min_score numeric NOT NULL CHECK (min_score >= 0 AND min_score <= 1),
    max_score numeric NOT NULL CHECK (max_score >= 0 AND max_score <= 1),

    class_rank integer NOT NULL,

    recommended_use text NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (model_version, class_code),

    CONSTRAINT regional_reliability_threshold_order
        CHECK (min_score <= max_score)
);


CREATE TABLE IF NOT EXISTS regional_reliability_spatial_cv_results (
    id bigserial PRIMARY KEY,

    model_version text NOT NULL REFERENCES regional_reliability_model_runs(model_version)
        ON DELETE CASCADE,

    held_out_zone text NOT NULL,

    n_test integer NOT NULL,
    positive_n integer NOT NULL,
    negative_n integer NOT NULL,

    precision_value numeric,
    recall_value numeric,
    specificity_value numeric,
    f1_score numeric,
    accuracy_value numeric,
    roc_auc numeric,
    brier_score numeric,

    created_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (model_version, held_out_zone)
);


CREATE TABLE IF NOT EXISTS regional_reliability_calibration_bins (
    id bigserial PRIMARY KEY,

    model_version text NOT NULL REFERENCES regional_reliability_model_runs(model_version)
        ON DELETE CASCADE,

    bin_id integer NOT NULL,
    score_min numeric NOT NULL,
    score_max numeric NOT NULL,

    n_samples integer NOT NULL,
    mean_predicted_score numeric,
    observed_positive_rate numeric,

    created_at timestamptz NOT NULL DEFAULT now(),

    UNIQUE (model_version, bin_id)
);


CREATE OR REPLACE VIEW regional_reliability_model_registry_v1 AS
SELECT
    r.model_version,
    r.model_name,
    r.feature_matrix_version,
    r.source_layer_version,
    r.algorithm,
    r.penalty,
    r.class_weight,

    r.training_n,
    r.positive_n,
    r.negative_n,
    r.uncertain_n,
    r.n_features,

    r.mean_precision,
    r.mean_recall,
    r.mean_specificity,
    r.mean_f1,
    r.mean_accuracy,
    r.mean_roc_auc,
    r.mean_brier_score,

    r.calibration_slope,
    r.calibration_intercept,

    r.status,
    r.limitations,
    r.created_at,

    COUNT(c.id) AS n_coefficients,
    COUNT(t.id) AS n_thresholds,
    COUNT(s.id) AS n_spatial_cv_zones,
    COUNT(b.id) AS n_calibration_bins

FROM regional_reliability_model_runs r
LEFT JOIN regional_reliability_model_coefficients c
  ON c.model_version = r.model_version
LEFT JOIN regional_reliability_model_thresholds t
  ON t.model_version = r.model_version
LEFT JOIN regional_reliability_spatial_cv_results s
  ON s.model_version = r.model_version
LEFT JOIN regional_reliability_calibration_bins b
  ON b.model_version = r.model_version
GROUP BY
    r.model_version,
    r.model_name,
    r.feature_matrix_version,
    r.source_layer_version,
    r.algorithm,
    r.penalty,
    r.class_weight,
    r.training_n,
    r.positive_n,
    r.negative_n,
    r.uncertain_n,
    r.n_features,
    r.mean_precision,
    r.mean_recall,
    r.mean_specificity,
    r.mean_f1,
    r.mean_accuracy,
    r.mean_roc_auc,
    r.mean_brier_score,
    r.calibration_slope,
    r.calibration_intercept,
    r.status,
    r.limitations,
    r.created_at;