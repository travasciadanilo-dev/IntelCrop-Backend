BEGIN;

CREATE TABLE IF NOT EXISTS regional_reliability_scores_v4_diagnostic (
    area_id text NOT NULL,
    source_geometry_id uuid NOT NULL,
    spatial_validation_zone text NOT NULL,

    model_version text NOT NULL,
    score_type text NOT NULL,
    view_version text NOT NULL,
    spectral_qc_version text NOT NULL,
    artifact_sha256 text NOT NULL,

    experimental_reliability_score_v4 double precision NOT NULL,
    experimental_reliability_class_v4 text NOT NULL,
    experimental_reliability_label_v4 text NOT NULL,

    model_status text NOT NULL,
    limitations text NOT NULL,
    feature_warning text,

    spectral_flag text NOT NULL,
    usable_for_baseline_spectral boolean NOT NULL,

    log_area_ha double precision NOT NULL,
    log_perimeter_m double precision NOT NULL,
    compactness_raw double precision NOT NULL,
    log_n_points double precision NOT NULL,

    large_polygon_flag smallint NOT NULL,
    small_candidate_flag smallint NOT NULL,
    complex_boundary_flag smallint NOT NULL,

    log_n_observations double precision NOT NULL,

    ndvi_median double precision NOT NULL,
    ndvi_iqr double precision NOT NULL,
    ndvi_stddev double precision NOT NULL,

    evi_median double precision NOT NULL,
    evi_iqr double precision NOT NULL,
    evi_stddev double precision NOT NULL,

    ndmi_median double precision NOT NULL,
    ndmi_iqr double precision NOT NULL,
    ndmi_stddev double precision NOT NULL,

    bsi_median double precision NOT NULL,
    bsi_iqr double precision NOT NULL,
    bsi_stddev double precision NOT NULL,

    created_at timestamp with time zone NOT NULL DEFAULT now(),

    CONSTRAINT regional_reliability_scores_v4_diagnostic_pkey
        PRIMARY KEY (area_id, model_version),

    CONSTRAINT regional_reliability_scores_v4_score_range_chk
        CHECK (
            experimental_reliability_score_v4 >= 0.0
            AND experimental_reliability_score_v4 <= 1.0
        ),

    CONSTRAINT regional_reliability_scores_v4_class_chk
        CHECK (
            experimental_reliability_class_v4 IN (
                'low',
                'compatible',
                'high',
                'very_high'
            )
        ),

    CONSTRAINT regional_reliability_scores_v4_large_flag_chk
        CHECK (large_polygon_flag IN (0, 1)),

    CONSTRAINT regional_reliability_scores_v4_small_flag_chk
        CHECK (small_candidate_flag IN (0, 1)),

    CONSTRAINT regional_reliability_scores_v4_complex_flag_chk
        CHECK (complex_boundary_flag IN (0, 1)),

    CONSTRAINT regional_reliability_scores_v4_spectral_flag_chk
        CHECK (
            spectral_flag IN (
                'strong',
                'moderate',
                'weak'
            )
        )
);

CREATE INDEX IF NOT EXISTS
    idx_regional_reliability_scores_v4_model
ON regional_reliability_scores_v4_diagnostic (
    model_version
);

CREATE INDEX IF NOT EXISTS
    idx_regional_reliability_scores_v4_class
ON regional_reliability_scores_v4_diagnostic (
    experimental_reliability_class_v4
);

CREATE INDEX IF NOT EXISTS
    idx_regional_reliability_scores_v4_score
ON regional_reliability_scores_v4_diagnostic (
    experimental_reliability_score_v4 DESC
);

CREATE INDEX IF NOT EXISTS
    idx_regional_reliability_scores_v4_zone_class
ON regional_reliability_scores_v4_diagnostic (
    spatial_validation_zone,
    experimental_reliability_class_v4
);

CREATE INDEX IF NOT EXISTS
    idx_regional_reliability_scores_v4_source_geometry
ON regional_reliability_scores_v4_diagnostic (
    source_geometry_id
);

CREATE INDEX IF NOT EXISTS
    idx_regional_reliability_scores_v4_spectral_flag
ON regional_reliability_scores_v4_diagnostic (
    spectral_flag,
    usable_for_baseline_spectral
);

COMMENT ON TABLE regional_reliability_scores_v4_diagnostic IS
'Applicazione diagnostica del modello regionale v4 alle aree candidate olivicole. Il modello resta sperimentale e non promosso come modello operativo.';

COMMENT ON COLUMN
regional_reliability_scores_v4_diagnostic.experimental_reliability_score_v4 IS
'Probabilità stimata dal modello per target_visual_v2 = 1.';

COMMENT ON COLUMN
regional_reliability_scores_v4_diagnostic.experimental_reliability_class_v4 IS
'Classe diagnostica derivata dalle soglie v4: low < 0.61, compatible 0.61-0.77, high 0.77-0.82, very_high >= 0.82.';

COMMENT ON COLUMN
regional_reliability_scores_v4_diagnostic.feature_warning IS
'Avvertenza relativa alla qualità o affidabilità delle feature, inclusa la qualità spettrale debole.';

COMMENT ON COLUMN
regional_reliability_scores_v4_diagnostic.artifact_sha256 IS
'Hash SHA-256 del file model.joblib utilizzato per lo scoring.';

COMMIT;
