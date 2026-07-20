from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict


class PriorityArea(BaseModel):
    class_id: int = Field(alias="class")
    label: str
    color: str
    area_ha: float
    percent: float

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow"
    )


class AnalysisStatus(BaseModel):
    code: str
    label: str
    severity: str
    color: str
    description: str
    recommended_action: str

    model_config = ConfigDict(extra="allow")


class AnalysisReliability(BaseModel):
    level: str
    label: str
    reasons: List[str]
    note: str

    model_config = ConfigDict(extra="allow")


class FieldSignificance(BaseModel):
    applicable: bool
    p_value: Optional[float] = None
    significant: Optional[bool] = None
    expected_false_positive_rate: Optional[float] = None
    observed_rate: Optional[float] = None
    n_anomalous_pixels: Optional[int] = None
    n_anomalous_effective_pixels: Optional[int] = None
    n_total_pixels: Optional[int] = None
    n_effective_pixels: Optional[int] = None
    spatial_independence_assumed: Optional[bool] = None
    note: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class MultivariateNormalityFlag(BaseModel):
    reliable: Optional[bool] = None
    flagged_components: List[str] = []
    skewness: List[Optional[float]] = []
    excess_kurtosis: List[Optional[float]] = []
    note: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class VDIResult(BaseModel):
    score: Optional[float] = None
    class_name: Optional[str] = Field(default=None, alias="class")
    window_days: Optional[int] = None
    r_squared: Optional[float] = None
    confidence: Optional[str] = None
    t_statistic: Optional[float] = None
    n_observations: Optional[int] = None
    n_effective: Optional[float] = None
    lag1_autocorrelation: Optional[float] = None
    note: Optional[str] = None

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow"
    )


class MapLayerLegendItem(BaseModel):
    class_id: Optional[int] = Field(default=None, alias="class")
    label: str
    color: str

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow"
    )


class MapLayerLegendLabels(BaseModel):
    low: Optional[str] = None
    high: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class MapLayer(BaseModel):
    name: str
    type: str
    url: str
    opacity: Optional[float] = None
    group: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    palette: Optional[List[str]] = None
    legend: Optional[List[MapLayerLegendItem]] = None
    legendLabels: Optional[MapLayerLegendLabels] = None

    model_config = ConfigDict(extra="allow")


class MapSnapshots(BaseModel):
    priority: Optional[str] = None
    evi: Optional[str] = None
    ndmi: Optional[str] = None
    ndre: Optional[str] = None
    ndvi: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class AgronomicContext(BaseModel):
    ordinary_percent: float
    high_performance_percent: float
    priority_percent: float
    priority_area_ha: float
    emerging_percent: float
    confirmed_percent: float
    persistent_percent: float
    confirmed_priority_percent: float
    attention_level: str
    vdi_class: Optional[str] = None
    vdi_score: Optional[float] = None

    model_config = ConfigDict(extra="allow")


class AnomalyThreshold(BaseModel):
    mahalanobis_threshold: Optional[float] = None
    threshold: Optional[float] = None
    mahalanobis_alpha: Optional[float] = None
    alpha: Optional[float] = None
    df: Optional[int] = None
    method: Optional[str] = None
    note: Optional[str] = None
    warning: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class DataQuality(BaseModel):
    temporally_consistent: Optional[bool] = None
    last3_gap_days: Optional[int] = None
    last3_gap_warning_days: Optional[int] = None
    valid_observations: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class ContractValidation(BaseModel):
    valid: Optional[bool] = None
    mode: Optional[str] = None
    error_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class AnalysisProfile(BaseModel):
    profile: Optional[str] = None
    compute_trend_data: Optional[bool] = None
    compute_field_significance: Optional[bool] = None
    compute_normality_diagnostics: Optional[bool] = None
    compute_vdi: Optional[bool] = None
    generate_response_map_layer: Optional[bool] = None
    generate_index_map_layers: Optional[bool] = None
    generate_map_snapshots: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class LandcoverSubtypeMatch(BaseModel):
    subtype: str
    subtype_label_it: str
    subtype_confidence: str
    subtype_layer_version: str
    coverage_ratio: float
    coverage_percent: float
    matched_subtypes: List[Dict[str, Any]] = []
    note: Optional[str] = None

    landcover_qc_version: Optional[str] = None
    landcover_qc_class: Optional[str] = None
    usable_for_baseline: bool = False
    matching_layer: Optional[str] = None

    baseline_version: Optional[str] = None
    baseline_layer: Optional[str] = None
    baseline_v1_match: bool = False
    baseline_v1_coverage_ratio: Optional[float] = None
    baseline_v1_coverage_percent: Optional[float] = None
    baseline_v1: Optional[Dict[str, Any]] = None

    strict_baseline_version: Optional[str] = None
    strict_baseline_layer: Optional[str] = None
    strict_baseline_v1_match: bool = False
    strict_baseline_v1_coverage_ratio: Optional[float] = None
    strict_baseline_v1_coverage_percent: Optional[float] = None
    strict_baseline_v1: Optional[Dict[str, Any]] = None
    usable_for_strict_baseline: bool = False

    model_config = ConfigDict(extra="allow")


class AnalysisResultContract(BaseModel):
    totalArea: float
    lastImageDate: Optional[str] = None

    priorityAreas: List[PriorityArea]

    analysisStatus: AnalysisStatus
    analysisReliability: AnalysisReliability
    fieldSignificance: FieldSignificance
    multivariateNormalityFlag: MultivariateNormalityFlag

    vdi: Optional[VDIResult] = None
    vdiData: Optional[List[Dict[str, Any]]] = None
    trendData: Optional[List[Dict[str, Any]]] = None

    agronomicContext: AgronomicContext
    anomalyThreshold: Optional[AnomalyThreshold] = None
    dataQuality: Optional[DataQuality] = None

    landcoverSubtype: Optional[LandcoverSubtypeMatch] = None

    mapLayers: Optional[Dict[str, MapLayer]] = None
    mapSnapshots: Optional[MapSnapshots] = None

    contractValidation: Optional[ContractValidation] = None
    analysisProfile: Optional[AnalysisProfile] = None

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow"
    )


# ================================================================
# MODELLI PER JOB ASINCRONI (FASE 2)
# ================================================================

class CatalogScreeningSummary(BaseModel):
    selected_area_count: int
    snapshot_area_count: int
    total_area_ha: float
    priority_area_count: int
    mean_reliability_score: Optional[float] = None
    reliability_class_counts: dict[str, int]

    model_config = ConfigDict(extra="allow")


class CatalogSpectralQuality(BaseModel):
    spectral_status: Optional[str] = None
    spectral_flag: Optional[str] = None
    n_observations: Optional[int] = None
    usable_for_baseline: Optional[bool] = None
    complete_features: Optional[bool] = None
    exclusion_reason: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class CatalogSpectralIndices(BaseModel):
    ndvi_median: Optional[float] = None
    evi_median: Optional[float] = None
    ndmi_median: Optional[float] = None
    bsi_median: Optional[float] = None

    model_config = ConfigDict(extra="allow")


class CatalogSpectralSummary(BaseModel):
    source: str
    selected_area_count: int
    complete_feature_count: int
    usable_baseline_count: int
    not_usable_baseline_count: int
    mean_observations: Optional[float] = None
    mean_indices: dict[str, Optional[float]]
    spectral_status_counts: dict[str, int]
    interpretation_scope: str

    model_config = ConfigDict(extra="allow")


class CatalogRelativeIndexPosition(BaseModel):
    raw_value: float
    rank_desc: int
    compared_area_count: int
    relative_position_0_1: float

    model_config = ConfigDict(extra="allow")


class CatalogRelativeIndexSummary(BaseModel):
    compared_area_count: int
    minimum: float
    maximum: float
    spread: float
    interpretation: str

    model_config = ConfigDict(extra="allow")


class CatalogRelativeComparison(BaseModel):
    status: str
    comparison_scope: str
    minimum_required_areas: int
    comparable_area_count: int
    position_definition: str
    indices: dict[
        str,
        CatalogRelativeIndexSummary,
    ]

    model_config = ConfigDict(extra="allow")


class CatalogScreeningArea(BaseModel):
    area_id: str
    area_ha: Optional[float] = None
    reliability_score: Optional[float] = None
    reliability_class: Optional[str] = None
    priority_candidate: Optional[bool] = None
    technical_subtype_id: Optional[str] = None
    spatial_validation_zone: Optional[str] = None
    spectral_quality: Optional[CatalogSpectralQuality] = None
    spectral_indices: Optional[CatalogSpectralIndices] = None
    relative_position: Optional[
        dict[str, CatalogRelativeIndexPosition]
    ] = None

    model_config = ConfigDict(extra="allow")


class CatalogScreeningResult(BaseModel):
    result_type: Literal["catalog_screening_diagnostic_v1"]
    status: Literal["completed"]
    job_id: str
    entity_id: str
    analysis_profile: str
    worker_version: str
    catalog_version: str
    model_version: str
    generated_at: str
    summary: CatalogScreeningSummary
    spectral_summary: Optional[CatalogSpectralSummary] = None
    relative_comparison: Optional[
        CatalogRelativeComparison
    ] = None
    areas: list[CatalogScreeningArea]
    limitations: list[str]

    model_config = ConfigDict(extra="allow")


class JobError(BaseModel):
    code: str
    message: str
    error_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class BatchJobCreateRequest(BaseModel):
    entity_id: str
    area_ids: list[str]
    analysis_profile: str = "catalog_screening_v1"

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class JobCreateResponse(BaseModel):
    job_id: str
    status: Literal["queued"]

    model_config = ConfigDict(extra="allow")


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "processing", "done", "error", "cancelled"]
    current_step: Optional[str] = None
    progress_pct: Optional[float] = None
    result: Optional[AnalysisResultContract | CatalogScreeningResult] = None
    error: Optional[JobError] = None

    model_config = ConfigDict(extra="allow")