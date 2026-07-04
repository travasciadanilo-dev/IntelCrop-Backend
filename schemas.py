from typing import Any, Dict, List, Optional

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

    mapLayers: Optional[Dict[str, MapLayer]] = None
    mapSnapshots: Optional[MapSnapshots] = None

    contractValidation: Optional[ContractValidation] = None
    analysisProfile: Optional[AnalysisProfile] = None

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow"
    )