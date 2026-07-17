-- 034_regional_reliability_v4_1_validation_run.sql
--
-- Registra il risultato della validazione visuale stratificata v4.1.
-- Non promuove il modello come catalogo operativo.

BEGIN;

INSERT INTO public.regional_reliability_validation_runs (
    validation_id,
    validation_version,
    source_model_version,
    source_score_table,
    source_validation_table,
    derived_view,
    catalog_n,
    sample_n,
    evaluable_n,
    not_evaluable_n,
    weighted_evaluable_pct,
    weighted_not_evaluable_pct,
    weighted_positive_total_pct,
    weighted_positive_evaluable_pct,
    weighted_positive_ci95_low,
    weighted_positive_ci95_high,
    weighted_not_evaluable_ci95_low,
    weighted_not_evaluable_ci95_high,
    compatible_high_difference_pp,
    compatible_high_fisher_odds_ratio,
    compatible_high_fisher_p_value,
    class_scheme,
    monotonic_classes,
    promotion_status,
    validation_method,
    methodological_decision,
    limitations,
    created_at
)
VALUES (
    '1c149192-e1f5-4fb2-833b-c510d59a8522'::uuid,
    'regional_reliability_v4_1_visual_validation_20260717',
    'regional_reliability_score_exp_v4_combined_ridge',
    'regional_reliability_scores_v4_diagnostic',
    'regional_reliability_v4_validation_sample',
    'regional_reliability_scores_v4_1_diagnostic',
    40261,
    240,
    184,
    56,
    80.01,
    19.99,
    48.82,
    61.02,
    53.30,
    68.59,
    14.22,
    26.22,
    1.39,
    0.8235,
    0.821891,
    'low|compatible|very_high',
    TRUE,
    'validated_not_promoted',
    'Stratified visual validation using 12 strata defined by three spatial validation zones and four original v4 reliability classes; 20 observations per stratum; visual labels 0=incompatible, 1=compatible, 9=not evaluable; regional estimates weighted by catalog stratum size; 95 percent intervals estimated using 20000 stratified bootstrap repetitions.',
    'The original compatible and high classes were merged because their weighted observed positive rates were 60.31 and 61.70 percent, respectively. The weighted difference was 1.39 percentage points and the Fisher exact test did not support separation (odds ratio 0.8235; p=0.821891). The derived v4.1 classification therefore contains low, compatible and very_high classes while preserving the original continuous v4 score.',
    'The visual sample contains 56 not-evaluable records. The estimated catalog-level not-evaluable proportion is 19.99 percent with a bootstrap 95 percent interval of 14.22 to 26.22 percent. Geographic performance is heterogeneous, with stronger observed compatibility in north Calabria and lower estimates in central and south Calabria. The model remains experimental and must not yet be presented as a definitive land-cover classification.',
    '2026-07-17 09:04:48.502263+00'::timestamptz
)
ON CONFLICT (validation_version)
DO UPDATE SET
    source_model_version = EXCLUDED.source_model_version,
    source_score_table = EXCLUDED.source_score_table,
    source_validation_table = EXCLUDED.source_validation_table,
    derived_view = EXCLUDED.derived_view,
    catalog_n = EXCLUDED.catalog_n,
    sample_n = EXCLUDED.sample_n,
    evaluable_n = EXCLUDED.evaluable_n,
    not_evaluable_n = EXCLUDED.not_evaluable_n,
    weighted_evaluable_pct = EXCLUDED.weighted_evaluable_pct,
    weighted_not_evaluable_pct =
        EXCLUDED.weighted_not_evaluable_pct,
    weighted_positive_total_pct =
        EXCLUDED.weighted_positive_total_pct,
    weighted_positive_evaluable_pct =
        EXCLUDED.weighted_positive_evaluable_pct,
    weighted_positive_ci95_low =
        EXCLUDED.weighted_positive_ci95_low,
    weighted_positive_ci95_high =
        EXCLUDED.weighted_positive_ci95_high,
    weighted_not_evaluable_ci95_low =
        EXCLUDED.weighted_not_evaluable_ci95_low,
    weighted_not_evaluable_ci95_high =
        EXCLUDED.weighted_not_evaluable_ci95_high,
    compatible_high_difference_pp =
        EXCLUDED.compatible_high_difference_pp,
    compatible_high_fisher_odds_ratio =
        EXCLUDED.compatible_high_fisher_odds_ratio,
    compatible_high_fisher_p_value =
        EXCLUDED.compatible_high_fisher_p_value,
    class_scheme = EXCLUDED.class_scheme,
    monotonic_classes = EXCLUDED.monotonic_classes,
    promotion_status = EXCLUDED.promotion_status,
    validation_method = EXCLUDED.validation_method,
    methodological_decision = EXCLUDED.methodological_decision,
    limitations = EXCLUDED.limitations;

COMMIT;
