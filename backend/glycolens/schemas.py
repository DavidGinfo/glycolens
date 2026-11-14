from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class GlucosePoint(BaseModel):
    ts: str  # "YYYY-MM-DD HH:MM" local wall-clock
    mg_dl: float
    slope_15m: float = 0.0  # mg/dL change over prior 15 mins
    iob_units: float = 0.0  # estimated active bolus insulin on board (U)


class BolusEvent(BaseModel):
    ts: str
    units: float
    carbs_g: float = 0.0
    bg_input: float = 0.0
    carb_ratio: float = 0.0
    prior_25m_rise: float = 0.0
    glucose_at_bolus: Optional[float] = None


class DailyInsulinSummary(BaseModel):
    date: str  # "YYYY-MM-DD"
    total_units: float
    basal_units: float
    bolus_units: float


class BasalEvent(BaseModel):
    ts: str
    event_type: str  # "Suspend" | "Temporary" | "Scheduled"
    duration_min: float = 0.0
    rate: float = 0.0


class DeviceAlarm(BaseModel):
    ts: str
    label: str
    category: Literal["high_glucose", "low_glucose", "pod_expiration", "signal_loss", "other"] = "other"


class WorkoutSession(BaseModel):
    start_ts: str
    end_ts: str
    activity_type: str = "Walking"
    duration_min: float
    steps: int = 0
    cadence: float = 0.0
    speed_mps: float = 0.0
    mean_hr: float = 0.0
    max_hr: float = 0.0
    calories: float = 0.0
    start_cgm: Optional[float] = None
    end_cgm: Optional[float] = None
    delta_cgm: Optional[float] = None
    nadir_90m_cgm: Optional[float] = None
    iob_at_start: float = 0.0


class SleepStageInterval(BaseModel):
    sleep_id: str
    start_ts: str
    end_ts: str
    stage: Literal["Awake", "Light", "Deep", "REM"]
    duration_min: float


class SleepSessionSummary(BaseModel):
    sleep_id: str
    wake_date: str
    start_ts: str
    end_ts: str
    total_sleep_min: float
    deep_min: float
    rem_min: float
    light_min: float
    awake_min: float
    wake_cgm: Optional[float] = None
    surge_3h_mg_dl: Optional[float] = None
    overnight_tir_pct: Optional[float] = None
    overnight_tar180_pct: Optional[float] = None
    overnight_tbr70_pct: Optional[float] = None
    overnight_alarms: int = 0


class DailyVitalsSummary(BaseModel):
    date: str
    steps: int = 0
    walk_min: float = 0.0
    run_min: float = 0.0
    active_calories: float = 0.0
    move_hourly_count: int = 0
    floors: float = 0.0
    wrist_temp_c: Optional[float] = None
    wrist_temp_min_c: Optional[float] = None
    resp_rate_brpm: Optional[float] = None
    systolic_bp: Optional[float] = None
    diastolic_bp: Optional[float] = None
    pulse_bpm: Optional[float] = None
    hrv_ms: Optional[float] = None


class PersonalThresholds(BaseModel):
    aerobic_hr: int = 131
    anaerobic_hr: int = 179
    max_hr: int = 209


class WalkModelWeights(BaseModel):
    bias: float
    w_start_cgm: float
    w_iob: float
    w_steps: float
    w_steps_x_iob: float
    mean_g0: float
    sd_g0: float
    mean_iob: float
    sd_iob: float
    mean_steps: float
    sd_steps: float
    holdout_r: float = 0.0
    mae_mg_dl: float = 0.0
    sample_count: int = 0


class CanonicalDataset(BaseModel):
    dataset_id: str
    title: str
    description: str = ""
    source_tags: list[str] = Field(default_factory=list)
    start_date: str
    end_date: str
    glucose: list[GlucosePoint] = Field(default_factory=list)
    boluses: list[BolusEvent] = Field(default_factory=list)
    daily_insulin: list[DailyInsulinSummary] = Field(default_factory=list)
    basal_events: list[BasalEvent] = Field(default_factory=list)
    alarms: list[DeviceAlarm] = Field(default_factory=list)
    workouts: list[WorkoutSession] = Field(default_factory=list)
    sleep_stages: list[SleepStageInterval] = Field(default_factory=list)
    sleep_sessions: list[SleepSessionSummary] = Field(default_factory=list)
    daily_vitals: list[DailyVitalsSummary] = Field(default_factory=list)
    thresholds: PersonalThresholds = Field(default_factory=PersonalThresholds)
    walk_model: Optional[WalkModelWeights] = None


class HourlyBucket(BaseModel):
    hour: int
    median_cgm: float
    p25: float
    p75: float
    p10: float
    p90: float
    tir_pct: float
    low_pct: float
    very_high_pct: float


class DayOfWeekStat(BaseModel):
    day: str
    tir_pct: float
    mean_cgm: float
    sleep_hours: float
    steps: int
    basal_units: float


class DataProfile(BaseModel):
    dataset_id: str
    title: str
    description: str = ""
    source_tags: list[str] = Field(default_factory=list)
    start_date: str
    end_date: str
    days_covered: float
    cgm_count: int
    mean_cgm: float
    median_cgm: float
    sd_cgm: float
    cv_pct: float
    gmi_pct: float
    tir_pct: float
    tbr_pct: float
    tar_pct: float
    very_high_pct: float
    bolus_count: int
    pct_zero_carb_boluses: float
    temp_target_count: int = 0
    mean_tdd_units: float = 0.0
    mean_basal_units: float = 0.0
    workout_count: int = 0
    sleep_count: int = 0
    bp_count: int = 0
    has_skin_temp: bool = False
    has_resp_rate: bool = False
    hourly_agp: list[HourlyBucket] = Field(default_factory=list)
    day_of_week: list[DayOfWeekStat] = Field(default_factory=list)
    programmed_ratios_by_hour: dict[str, str] = Field(default_factory=dict)
    undisturbed_7am_median: Optional[float] = None
    walk_model: Optional[WalkModelWeights] = None


class DayReplay(BaseModel):
    date: str
    label: str
    cgm: list[tuple[float, float]] = Field(default_factory=list)
    boluses: list[dict[str, Any]] = Field(default_factory=list)
    walks: list[dict[str, Any]] = Field(default_factory=list)
    sleep_end_hour: Optional[float] = None
    bp_events: list[dict[str, Any]] = Field(default_factory=list)


class PairedBarItem(BaseModel):
    label: str
    condition_a_val: float  # Risky / Current baseline habit
    condition_b_val: float  # Improved / Target habit
    max_val: float
    unit: str = ""
    higher_is_better: bool = True


class ChartSpec(BaseModel):
    chart_type: Literal["paired_bars", "scatter", "trajectory"] = "paired_bars"
    condition_a_label: str = ""
    condition_b_label: str = ""
    bars: list[PairedBarItem] = Field(default_factory=list)
    points: list[dict[str, Any]] = Field(default_factory=list)
    series: list[dict[str, Any]] = Field(default_factory=list)


class StatisticalEvidence(BaseModel):
    domain: Literal["activity", "sleep", "dosing", "vitals"]
    hypothesis_id: str
    title: str
    n_a: int
    n_b: int
    mean_a: float
    mean_b: float
    diff: float
    ci_95_low: float
    ci_95_high: float
    unit: str
    statistically_significant: bool
    example_timestamps: list[str] = Field(default_factory=list)
    raw_summary: str
    chart_spec: ChartSpec


class InsightCard(BaseModel):
    id: str
    domain: Literal["activity", "sleep", "dosing", "vitals"]
    category: Literal["habit_experiment", "pre_activity_shield", "doctor_discussion"]
    severity_rank: int = 1
    headline: str
    plain_explanation: str
    impact_badge: str
    personal_anchor: str
    try_this_week: str
    evidence: StatisticalEvidence


class SwarmStreamEvent(BaseModel):
    event_type: Literal["agent_status", "hypothesis_tested", "insight_ready", "complete", "error"]
    agent_name: str
    domain: Optional[Literal["activity", "sleep", "dosing", "vitals", "synthesizer"]] = None
    status: Literal["queued", "running", "testing", "verified", "rejected", "done", "error"] = "running"
    message: str = ""
    evidence: Optional[StatisticalEvidence] = None
    insight: Optional[InsightCard] = None
    report: Optional["DiscoveryReport"] = None
    engine_mode: Literal["gemini_swarm", "statistical_fallback"] = "gemini_swarm"


class DiscoveryReport(BaseModel):
    session_id: str
    engine_mode: Literal["gemini_swarm", "statistical_fallback"]
    executive_summary: str
    insights: list[InsightCard] = Field(default_factory=list)
    hypotheses_tested: int = 0
    hypotheses_verified: int = 0


class WalkSimulationRequest(BaseModel):
    start_cgm: float = 220.0
    active_iob: float = 2.0
    planned_steps: int = 2400


class WalkSimulationResponse(BaseModel):
    predicted_delta_mg_dl: float
    predicted_end_cgm: float
    hypo_risk_level: Literal["low", "moderate", "high"]
    recommendation: str
    model_r: float = 0.0


class ChatRequest(BaseModel):
    question: str
    history: list[dict[str, str]] = Field(default_factory=list)


class ChatResponse(BaseModel):
    answer: str
    supporting_numbers: list[str] = Field(default_factory=list)
    engine_mode: Literal["gemini_swarm", "statistical_fallback"] = "gemini_swarm"


class PersonaInfo(BaseModel):
    id: str
    title: str
    subtitle: str
    badges: list[str] = Field(default_factory=list)
    is_local_real: bool = False
