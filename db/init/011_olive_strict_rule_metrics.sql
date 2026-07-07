CREATE OR REPLACE VIEW landcover_olive_strict_rule_metrics_v1 AS
WITH cm AS (
    SELECT
        COUNT(*) FILTER (WHERE eval_class_strict = 'true_positive')::numeric AS tp,
        COUNT(*) FILTER (WHERE eval_class_strict = 'false_positive')::numeric AS fp,
        COUNT(*) FILTER (WHERE eval_class_strict = 'false_negative')::numeric AS fn,
        COUNT(*) FILTER (WHERE eval_class_strict = 'true_negative')::numeric AS tn,
        COUNT(*) FILTER (WHERE eval_class_strict = 'uncertain_pass')::numeric AS uncertain_pass,
        COUNT(*) FILTER (WHERE eval_class_strict = 'uncertain_reject')::numeric AS uncertain_reject
    FROM landcover_olive_visual_training_eval_strict_v1
)
SELECT
    tp,
    fp,
    fn,
    tn,
    uncertain_pass,
    uncertain_reject,

    ROUND(tp / NULLIF(tp + fp, 0), 4) AS precision,
    ROUND(tp / NULLIF(tp + fn, 0), 4) AS recall,
    ROUND(tn / NULLIF(tn + fp, 0), 4) AS specificity,
    ROUND((2 * tp) / NULLIF((2 * tp + fp + fn), 0), 4) AS f1_score,
    ROUND((tp + tn) / NULLIF(tp + fp + fn + tn, 0), 4) AS accuracy,

    ROUND(uncertain_pass / NULLIF(uncertain_pass + uncertain_reject, 0), 4) AS uncertain_pass_rate
FROM cm;


CREATE OR REPLACE VIEW landcover_olive_strict_rule_wilson_ci_v1 AS
WITH m AS (
    SELECT *
    FROM landcover_olive_strict_rule_metrics_v1
),
base AS (
    SELECT
        'precision'::text AS metric,
        tp AS numerator,
        tp + fp AS denominator
    FROM m

    UNION ALL

    SELECT
        'recall'::text AS metric,
        tp AS numerator,
        tp + fn AS denominator
    FROM m

    UNION ALL

    SELECT
        'specificity'::text AS metric,
        tn AS numerator,
        tn + fp AS denominator
    FROM m

    UNION ALL

    SELECT
        'accuracy'::text AS metric,
        tp + tn AS numerator,
        tp + fp + fn + tn AS denominator
    FROM m
),
calc AS (
    SELECT
        metric,
        numerator,
        denominator,
        numerator / NULLIF(denominator, 0) AS estimate,
        1.959963984540054::numeric AS z
    FROM base
    WHERE denominator > 0
)
SELECT
    metric,
    numerator,
    denominator,
    ROUND(estimate, 4) AS estimate,
    ROUND(
        (
            (
                estimate
                + ((z * z) / (2 * denominator))
                - z * SQRT(
                    (
                        estimate * (1 - estimate) / denominator
                        + ((z * z) / (4 * denominator * denominator))
                    )::double precision
                )::numeric
            )
            / (1 + ((z * z) / denominator))
        ),
        4
    ) AS ci95_lower,
    ROUND(
        (
            (
                estimate
                + ((z * z) / (2 * denominator))
                + z * SQRT(
                    (
                        estimate * (1 - estimate) / denominator
                        + ((z * z) / (4 * denominator * denominator))
                    )::double precision
                )::numeric
            )
            / (1 + ((z * z) / denominator))
        ),
        4
    ) AS ci95_upper
FROM calc;