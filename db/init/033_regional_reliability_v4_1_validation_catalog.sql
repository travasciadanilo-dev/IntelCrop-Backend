-- 033_regional_reliability_v4_1_validation_catalog.sql
--
-- Oggetti riproducibili per la validazione regionale v4 e per il
-- catalogo diagnostico v4.1 a tre classi:
--   low, compatible, very_high.
--
-- Questa migrazione NON promuove v4.1 come versione operativa.
-- Il backend continua a usare v3 salvo:
--   AREA_CATALOG_VERSION=v4_1
--
-- Dipendenze:
--   regional_reliability_model_runs
--   regional_reliability_scores_v4_diagnostic
--   olive_candidate_pool_v2
--   area_catalog_entities_v1
--
-- La migrazione crea:
--   regional_reliability_validation_runs
--   regional_reliability_v4_validation_sample
--   regional_reliability_scores_v4_1_diagnostic
--   olive_candidate_pool_v2_reliability_v4_1_diagnostic_v1
--   regional_reliability_model_validation_registry_v1
--   area_catalog_v4_1_diagnostic
--   area_catalog_v4_1_entity_scope

BEGIN;

CREATE TABLE IF NOT EXISTS public.regional_reliability_validation_runs (
    validation_id uuid NOT NULL,
    validation_version text NOT NULL,
    source_model_version text NOT NULL,
    source_score_table text NOT NULL,
    source_validation_table text NOT NULL,
    derived_view text NOT NULL,
    catalog_n integer NOT NULL,
    sample_n integer NOT NULL,
    evaluable_n integer NOT NULL,
    not_evaluable_n integer NOT NULL,
    weighted_evaluable_pct double precision NOT NULL,
    weighted_not_evaluable_pct double precision NOT NULL,
    weighted_positive_total_pct double precision NOT NULL,
    weighted_positive_evaluable_pct double precision NOT NULL,
    weighted_positive_ci95_low double precision,
    weighted_positive_ci95_high double precision,
    weighted_not_evaluable_ci95_low double precision,
    weighted_not_evaluable_ci95_high double precision,
    compatible_high_difference_pp double precision,
    compatible_high_fisher_odds_ratio double precision,
    compatible_high_fisher_p_value double precision,
    class_scheme text NOT NULL,
    monotonic_classes boolean NOT NULL,
    promotion_status text NOT NULL,
    validation_method text NOT NULL,
    methodological_decision text NOT NULL,
    limitations text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS public.regional_reliability_v4_validation_sample (
    review_id uuid NOT NULL,
    area_id text,
    source_geometry_id uuid,
    spatial_validation_zone text,
    experimental_reliability_score_v4 double precision,
    experimental_reliability_class_v4 text,
    spectral_flag text,
    usable_for_baseline_spectral boolean,
    visual_label smallint,
    reviewer_confidence text,
    review_notes text,
    reviewer_name text,
    reviewed_at timestamp with time zone,
    sampled_at timestamp with time zone,
    sample_version text
);

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'regional_reliability_validation_runs_pkey'
          AND conrelid =
            'public.regional_reliability_validation_runs'::regclass
    ) THEN
        ALTER TABLE public.regional_reliability_validation_runs
        ADD CONSTRAINT regional_reliability_validation_runs_pkey
        PRIMARY KEY (validation_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'regional_reliability_validation_runs_validation_version_key'
          AND conrelid =
            'public.regional_reliability_validation_runs'::regclass
    ) THEN
        ALTER TABLE public.regional_reliability_validation_runs
        ADD CONSTRAINT
            regional_reliability_validation_runs_validation_version_key
        UNIQUE (validation_version);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'regional_reliability_validation_runs_status_chk'
          AND conrelid =
            'public.regional_reliability_validation_runs'::regclass
    ) THEN
        ALTER TABLE public.regional_reliability_validation_runs
        ADD CONSTRAINT
            regional_reliability_validation_runs_status_chk
        CHECK (
            promotion_status = ANY (
                ARRAY[
                    'diagnostic'::text,
                    'validated_not_promoted'::text,
                    'promoted'::text,
                    'rejected'::text
                ]
            )
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'regional_reliability_validation_runs_model_fk'
          AND conrelid =
            'public.regional_reliability_validation_runs'::regclass
    ) THEN
        ALTER TABLE public.regional_reliability_validation_runs
        ADD CONSTRAINT
            regional_reliability_validation_runs_model_fk
        FOREIGN KEY (source_model_version)
        REFERENCES public.regional_reliability_model_runs (
            model_version
        )
        ON UPDATE CASCADE
        ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname =
            'regional_reliability_v4_validation_sample_pkey'
          AND conrelid =
            'public.regional_reliability_v4_validation_sample'::regclass
    ) THEN
        ALTER TABLE public.regional_reliability_v4_validation_sample
        ADD CONSTRAINT
            regional_reliability_v4_validation_sample_pkey
        PRIMARY KEY (review_id);
    END IF;
END
$migration$;

CREATE UNIQUE INDEX IF NOT EXISTS
    regional_reliability_v4_validation_sample_area_uidx
ON public.regional_reliability_v4_validation_sample (
    area_id
);

CREATE INDEX IF NOT EXISTS
    regional_reliability_v4_validation_sample_class_idx
ON public.regional_reliability_v4_validation_sample (
    experimental_reliability_class_v4
);

CREATE INDEX IF NOT EXISTS
    regional_reliability_v4_validation_sample_zone_idx
ON public.regional_reliability_v4_validation_sample (
    spatial_validation_zone
);

CREATE INDEX IF NOT EXISTS
    regional_reliability_validation_runs_model_idx
ON public.regional_reliability_validation_runs (
    source_model_version
);

CREATE OR REPLACE VIEW public.regional_reliability_scores_v4_1_diagnostic AS
 SELECT area_id,     source_geometry_id,     spatial_validation_zone,     model_version,     'experimental_regional_reliability_probability_v4_1'::text AS score_type,     view_version,     spectral_qc_version,     artifact_sha256,     experimental_reliability_score_v4 AS experimental_reliability_score_v4_1,         CASE             WHEN experimental_reliability_class_v4 = 'low'::text THEN 'low'::text             WHEN experimental_reliability_class_v4 = ANY (ARRAY['compatible'::text, 'high'::text]) THEN 'compatible'::text             WHEN experimental_reliability_class_v4 = 'very_high'::text THEN 'very_high'::text             ELSE NULL::text         END AS experimental_reliability_class_v4_1,         CASE             WHEN experimental_reliability_class_v4 = 'low'::text THEN 'Bassa compatibilit├á'::text             WHEN experimental_reliability_class_v4 = ANY (ARRAY['compatible'::text, 'high'::text]) THEN 'Compatibilit├á'::text             WHEN experimental_reliability_class_v4 = 'very_high'::text THEN 'Compatibilit├á molto elevata'::text             ELSE NULL::text         END AS experimental_reliability_label_v4_1,     'experimental_validated_not_promoted'::text AS model_status,     concat_ws(' '::text, limitations, 'La classe v4 high ├¿ stata accorpata', 'alla classe compatible nella vista v4.1', 'per assenza di separazione statisticamente', 'supportata nella validazione visuale', '(Fisher p=0.821891).') AS limitations,     feature_warning,     spectral_flag,     usable_for_baseline_spectral,     log_area_ha,     log_perimeter_m,     compactness_raw,     log_n_points,     large_polygon_flag,     small_candidate_flag,     complex_boundary_flag,     log_n_observations,     ndvi_median,     ndvi_iqr,     ndvi_stddev,     evi_median,     evi_iqr,     evi_stddev,     ndmi_median,     ndmi_iqr,     ndmi_stddev,     bsi_median,     bsi_iqr,     bsi_stddev,     experimental_reliability_class_v4 AS source_class_v4,     created_at    FROM regional_reliability_scores_v4_diagnostic;;

CREATE OR REPLACE VIEW public.olive_candidate_pool_v2_reliability_v4_1_diagnostic_v1 AS
 SELECT p.area_id,     p.source_geometry_id,     p.subtype_id,     p.source_layer_version,     p.geom_is_valid,     p.geom_is_empty,     p.n_points,     p.n_parts,     p.approx_centroid_lat,     p.spatial_validation_zone,     p.n_points_bin,     p.n_parts_bin,     p.current_high_confidence_v2,     p.high_confidence_area_ha,     p.high_confidence_compactness,     p.high_confidence_qc_score,     p.high_confidence_qc_class,     p.identity_reference_match,     p.strict_reference_match,     p.visual_label,     p.eval_class,     p.eval_class_strict,     p.binary_visual_label,     p.created_at,     p.area_ha_raw,     p.perimeter_m_raw,     p.compactness_raw,     p.area_bin_raw,     p.candidate_pool_v2,     p.candidate_origin,     p.large_polygon_flag,     p.small_candidate_flag,     p.complex_boundary_flag,     p.geometric_prior_rank,     p.review_priority_score,     p.review_priority_reason,     s.score_type,     s.view_version,     s.experimental_reliability_score_v4_1,     s.experimental_reliability_class_v4_1,     s.experimental_reliability_label_v4_1,     s.model_version AS reliability_model_version,     s.model_status AS reliability_model_status,     s.limitations AS reliability_model_limitations,     s.feature_warning AS reliability_feature_warning,     s.created_at AS reliability_score_created_at,     s.spectral_flag,     s.usable_for_baseline_spectral,     s.spectral_qc_version,     s.artifact_sha256,     s.source_class_v4    FROM olive_candidate_pool_v2_review_priority p      JOIN regional_reliability_scores_v4_1_diagnostic s ON s.area_id = p.area_id AND s.model_version = 'regional_reliability_score_exp_v4_combined_ridge'::text;;

CREATE OR REPLACE VIEW public.regional_reliability_model_validation_registry_v1 AS
 SELECT m.model_version,     m.model_name,     m.feature_matrix_version,     m.source_layer_version,     m.algorithm,     m.penalty,     m.class_weight,     m.training_n,     m.positive_n,     m.negative_n,     m.uncertain_n,     m.n_features,     m.mean_precision,     m.mean_recall,     m.mean_specificity,     m.mean_f1,     m.mean_accuracy,     m.mean_roc_auc,     m.mean_brier_score,     m.calibration_slope,     m.calibration_intercept,     m.status AS model_registry_status,     m.created_at AS model_created_at,     v.validation_id,     v.validation_version,     v.source_score_table,     v.source_validation_table,     v.derived_view,     v.catalog_n,     v.sample_n,     v.evaluable_n,     v.not_evaluable_n,     v.weighted_evaluable_pct,     v.weighted_not_evaluable_pct,     v.weighted_positive_total_pct,     v.weighted_positive_evaluable_pct,     v.weighted_positive_ci95_low,     v.weighted_positive_ci95_high,     v.weighted_not_evaluable_ci95_low,     v.weighted_not_evaluable_ci95_high,     v.compatible_high_difference_pp,     v.compatible_high_fisher_odds_ratio,     v.compatible_high_fisher_p_value,     v.class_scheme,     v.monotonic_classes,     v.promotion_status AS validation_status,     v.validation_method,     v.methodological_decision,     v.limitations AS validation_limitations,     v.created_at AS validated_at    FROM regional_reliability_model_runs m      LEFT JOIN regional_reliability_validation_runs v ON v.source_model_version = m.model_version;;

CREATE OR REPLACE VIEW public.area_catalog_v4_1_diagnostic AS
 WITH catalog AS (          SELECT d.area_id,             g.id AS source_geometry_id,             'calabria'::text AS region_code,             'Calabria'::text AS region_label,             g.subtype_id AS technical_subtype_id,             COALESCE(s.label_it, g.subtype_id) AS technical_subtype_label,                 CASE                     WHEN g.subtype_id = ANY (ARRAY['olive_pure'::text, 'olive_citrus'::text, 'olive_vine'::text]) THEN 'permanent_tree_crop'::text                     ELSE 'agricultural_area'::text                 END AS area_type,                 CASE                     WHEN g.subtype_id = ANY (ARRAY['olive_pure'::text, 'olive_citrus'::text, 'olive_vine'::text]) THEN 'Coltura arborea permanente'::text                     ELSE 'Area agricola'::text                 END AS area_type_label,             d.spatial_validation_zone,             d.candidate_origin,             d.area_ha_raw AS area_ha,             d.area_bin_raw,             d.n_points,             d.n_parts,             d.n_points_bin,             d.n_parts_bin,             d.current_high_confidence_v2,             d.identity_reference_match,             d.strict_reference_match,             d.large_polygon_flag,             d.small_candidate_flag,             d.complex_boundary_flag,             d.experimental_reliability_score_v4_1 AS reliability_score,             d.experimental_reliability_class_v4_1 AS reliability_class,             d.experimental_reliability_label_v4_1 AS reliability_label,                 CASE                     WHEN d.experimental_reliability_class_v4_1 = 'very_high'::text THEN 3                     WHEN d.experimental_reliability_class_v4_1 = 'compatible'::text THEN 2                     WHEN d.experimental_reliability_class_v4_1 = 'low'::text THEN 1                     ELSE 0                 END AS reliability_rank,             d.experimental_reliability_class_v4_1 = 'very_high'::text AS catalog_priority_candidate,                 CASE                     WHEN d.experimental_reliability_class_v4_1 = 'very_high'::text THEN 'Compatibilita molto elevata'::text                     WHEN d.experimental_reliability_class_v4_1 = 'compatible'::text THEN 'Compatibilita diagnostica'::text                     WHEN d.experimental_reliability_class_v4_1 = 'low'::text THEN 'Bassa compatibilita diagnostica'::text                     ELSE 'Classe non disponibile'::text                 END AS catalog_status_label,             d.reliability_model_version,             d.reliability_model_status,             d.reliability_model_limitations,             d.reliability_score_created_at,             'area_catalog_v4_1_diagnostic'::text AS catalog_version,             'validated_not_promoted'::text AS catalog_status,             st_x(st_pointonsurface(g.geom)) AS centroid_lon,             st_y(st_pointonsurface(g.geom)) AS centroid_lat,             st_xmin(st_envelope(g.geom)::box3d) AS bbox_min_lon,             st_ymin(st_envelope(g.geom)::box3d) AS bbox_min_lat,             st_xmax(st_envelope(g.geom)::box3d) AS bbox_max_lon,             st_ymax(st_envelope(g.geom)::box3d) AS bbox_max_lat,             g.geom            FROM olive_candidate_pool_v2_reliability_v4_1_diagnostic_v1 d              JOIN landcover_subtype_geometries g ON g.id::text = d.area_id              LEFT JOIN landcover_subtypes s ON s.id = g.subtype_id         )  SELECT area_id,     source_geometry_id,     region_code,     region_label,     technical_subtype_id,     technical_subtype_label,     area_type,     area_type_label,     spatial_validation_zone,     candidate_origin,     area_ha,     area_bin_raw,     n_points,     n_parts,     n_points_bin,     n_parts_bin,     current_high_confidence_v2,     identity_reference_match,     strict_reference_match,     large_polygon_flag,     small_candidate_flag,     complex_boundary_flag,     reliability_score,     reliability_class,     reliability_label,     reliability_rank,     catalog_priority_candidate,     catalog_status_label,     reliability_model_version,     reliability_model_status,     reliability_model_limitations,     reliability_score_created_at,     catalog_version,     catalog_status,     centroid_lon,     centroid_lat,     bbox_min_lon,     bbox_min_lat,     bbox_max_lon,     bbox_max_lat,     geom    FROM catalog;;

CREATE OR REPLACE VIEW public.area_catalog_v4_1_entity_scope AS
 SELECT e.entity_id,     e.entity_name,     e.entity_type,     e.entity_status,     t.territory_id,     t.territory_name,     t.territory_scope_version,     t.territory_status,     c.area_id,     c.source_geometry_id,     c.region_code,     c.region_label,     c.technical_subtype_id,     c.technical_subtype_label,     c.area_type,     c.area_type_label,     c.spatial_validation_zone,     c.candidate_origin,     c.area_ha,     c.area_bin_raw,     c.n_points,     c.n_parts,     c.n_points_bin,     c.n_parts_bin,     c.current_high_confidence_v2,     c.identity_reference_match,     c.strict_reference_match,     c.large_polygon_flag,     c.small_candidate_flag,     c.complex_boundary_flag,     c.reliability_score,     c.reliability_class,     c.reliability_label,     c.reliability_rank,     c.catalog_priority_candidate,     c.catalog_status_label,     c.reliability_model_version,     c.reliability_model_status,     c.reliability_model_limitations,     c.reliability_score_created_at,     c.catalog_version,     c.catalog_status,     c.centroid_lon,     c.centroid_lat,     c.bbox_min_lon,     c.bbox_min_lat,     c.bbox_max_lon,     c.bbox_max_lat,     c.geom    FROM app_entities_v1 e      JOIN app_entity_territories_v1 t ON t.entity_id = e.entity_id AND t.territory_status = 'active'::text      JOIN area_catalog_v4_1_diagnostic c ON c.geom && t.geom AND st_intersects(c.geom, t.geom)   WHERE e.entity_status = 'active'::text;;

COMMIT;
