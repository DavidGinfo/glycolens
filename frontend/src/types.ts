// 1-to-1 TypeScript interfaces matching backend/glycolens/schemas.py

export interface GlucosePoint {
  ts: string; // "YYYY-MM-DD HH:MM" local wall-clock
  mg_dl: number;
  slope_15m: number; // mg/dL change over prior 15 mins
  iob_units: number; // estimated active bolus insulin on board (U)
}

export interface BolusEvent {
  ts: string;
  units: number;
  carbs_g: number;
  bg_input: number;
  carb_ratio: number;
  prior_25m_rise: number;
  glucose_at_bolus?: number | null;
}

export interface DailyInsulinSummary {
  date: string; // "YYYY-MM-DD"
  total_units: number;
  basal_units: number;
  bolus_units: number;
}

export interface BasalEvent {
  ts: string;
  event_type: string; // "Suspend" | "Temporary" | "Scheduled"
  duration_min: number;
  rate: number;
}

export type DeviceAlarmCategory =
  | 'high_glucose'
  | 'low_glucose'
  | 'pod_expiration'
  | 'signal_loss'
  | 'other';

export interface DeviceAlarm {
  ts: string;
  label: string;
  category: DeviceAlarmCategory;
}

export interface WorkoutSession {
  start_ts: string;
  end_ts: string;
  activity_type: string;
  duration_min: number;
  steps: number;
  cadence: number;
  speed_mps: number;
  mean_hr: number;
  max_hr: number;
  calories: number;
  start_cgm?: number | null;
  end_cgm?: number | null;
  delta_cgm?: number | null;
  nadir_90m_cgm?: number | null;
  iob_at_start: number;
}

export type SleepStage = 'Awake' | 'Light' | 'Deep' | 'REM';

export interface SleepStageInterval {
  sleep_id: string;
  start_ts: string;
  end_ts: string;
  stage: SleepStage;
  duration_min: number;
}

export interface SleepSessionSummary {
  sleep_id: string;
  wake_date: string;
  start_ts: string;
  end_ts: string;
  total_sleep_min: number;
  deep_min: number;
  rem_min: number;
  light_min: number;
  awake_min: number;
  wake_cgm?: number | null;
  surge_3h_mg_dl?: number | null;
  overnight_tir_pct?: number | null;
  overnight_tar180_pct?: number | null;
  overnight_tbr70_pct?: number | null;
  overnight_alarms: number;
}

export interface DailyVitalsSummary {
  date: string;
  steps: number;
  walk_min: number;
  run_min: number;
  active_calories: number;
  move_hourly_count: number;
  floors: number;
  wrist_temp_c?: number | null;
  wrist_temp_min_c?: number | null;
  resp_rate_brpm?: number | null;
  systolic_bp?: number | null;
  diastolic_bp?: number | null;
  pulse_bpm?: number | null;
  hrv_ms?: number | null;
}

export interface PersonalThresholds {
  aerobic_hr: number;
  anaerobic_hr: number;
  max_hr: number;
}

export interface WalkModelWeights {
  bias: number;
  w_start_cgm: number;
  w_iob: number;
  w_steps: number;
  w_steps_x_iob: number;
  mean_g0: number;
  sd_g0: number;
  mean_iob: number;
  sd_iob: number;
  mean_steps: number;
  sd_steps: number;
  holdout_r: number;
  mae_mg_dl: number;
  sample_count: number;
}

export interface CanonicalDataset {
  dataset_id: string;
  title: string;
  description: string;
  source_tags: string[];
  start_date: string;
  end_date: string;
  glucose: GlucosePoint[];
  boluses: BolusEvent[];
  daily_insulin: DailyInsulinSummary[];
  basal_events: BasalEvent[];
  alarms: DeviceAlarm[];
  workouts: WorkoutSession[];
  sleep_stages: SleepStageInterval[];
  sleep_sessions: SleepSessionSummary[];
  daily_vitals: DailyVitalsSummary[];
  thresholds: PersonalThresholds;
  walk_model?: WalkModelWeights | null;
}

export interface HourlyBucket {
  hour: number;
  median_cgm: number;
  p25: number;
  p75: number;
  p10: number;
  p90: number;
  tir_pct: number;
  low_pct: number;
  very_high_pct: number;
}

export interface DayOfWeekStat {
  day: string;
  tir_pct: number;
  mean_cgm: number;
  sleep_hours: number;
  steps: number;
  basal_units: number;
}

export interface DataProfile {
  dataset_id: string;
  title: string;
  description: string;
  source_tags: string[];
  start_date: string;
  end_date: string;
  days_covered: number;
  cgm_count: number;
  mean_cgm: number;
  median_cgm: number;
  sd_cgm: number;
  cv_pct: number;
  gmi_pct: number;
  tir_pct: number;
  tbr_pct: number;
  tar_pct: number;
  very_high_pct: number;
  bolus_count: number;
  pct_zero_carb_boluses: number;
  temp_target_count: number;
  mean_tdd_units: number;
  mean_basal_units: number;
  workout_count: number;
  sleep_count: number;
  bp_count: number;
  has_skin_temp: boolean;
  has_resp_rate: boolean;
  hourly_agp: HourlyBucket[];
  day_of_week: DayOfWeekStat[];
  programmed_ratios_by_hour: Record<string, string>;
  undisturbed_7am_median?: number | null;
  walk_model?: WalkModelWeights | null;
}

export interface DayReplayBolus {
  hour: number;
  ts?: string;
  units: number;
  carbs_g?: number;
  glucose_at_bolus?: number | null;
  [key: string]: unknown;
}

export interface DayReplayWalk {
  hour?: number;
  start_hour?: number;
  end_hour?: number;
  activity_type?: string;
  duration_min?: number;
  steps?: number;
  mean_hr?: number;
  start_cgm?: number | null;
  end_cgm?: number | null;
  delta_cgm?: number | null;
  iob_at_start?: number;
  [key: string]: unknown;
}

export interface DayReplayBpEvent {
  hour: number;
  systolic_bp: number;
  diastolic_bp?: number;
  pulse_bpm?: number | null;
  [key: string]: unknown;
}

export interface DayReplay {
  date: string;
  label: string;
  cgm: [number, number][];
  boluses: DayReplayBolus[];
  walks: DayReplayWalk[];
  sleep_end_hour?: number | null;
  bp_events: DayReplayBpEvent[];
}

export interface PairedBarItem {
  label: string;
  condition_a_val: number; // Risky / Current baseline habit
  condition_b_val: number; // Improved / Target habit
  max_val: number;
  unit: string;
  higher_is_better: boolean;
}

export type ChartType = 'paired_bars' | 'scatter' | 'trajectory';

export interface ChartSpec {
  chart_type: ChartType;
  condition_a_label: string;
  condition_b_label: string;
  bars: PairedBarItem[];
  points: Record<string, unknown>[];
  series: Record<string, unknown>[];
}

export type SpecialistDomain = 'activity' | 'sleep' | 'dosing' | 'vitals';
export type SwarmDomain = SpecialistDomain | 'synthesizer';

export interface StatisticalEvidence {
  domain: SpecialistDomain;
  hypothesis_id: string;
  title: string;
  n_a: number;
  n_b: number;
  mean_a: number;
  mean_b: number;
  diff: number;
  ci_95_low: number;
  ci_95_high: number;
  unit: string;
  statistically_significant: boolean;
  example_timestamps: string[];
  raw_summary: string;
  chart_spec: ChartSpec;
}

export type InsightCategory =
  | 'habit_experiment'
  | 'pre_activity_shield'
  | 'doctor_discussion';

export interface InsightCard {
  id: string;
  domain: SpecialistDomain;
  category: InsightCategory;
  severity_rank: number;
  headline: string;
  plain_explanation: string;
  impact_badge: string;
  personal_anchor: string;
  try_this_week: string;
  evidence: StatisticalEvidence;
}

export type SwarmEventType =
  | 'agent_status'
  | 'hypothesis_tested'
  | 'insight_ready'
  | 'complete'
  | 'error';

export type SwarmStatus =
  | 'queued'
  | 'running'
  | 'testing'
  | 'verified'
  | 'rejected'
  | 'done'
  | 'error';

export type EngineMode = 'gemini_swarm' | 'statistical_fallback';

export interface DiscoveryReport {
  session_id: string;
  engine_mode: EngineMode;
  executive_summary: string;
  insights: InsightCard[];
  hypotheses_tested: number;
  hypotheses_verified: number;
}

export interface SwarmStreamEvent {
  event_type: SwarmEventType;
  agent_name: string;
  domain?: SwarmDomain | null;
  status: SwarmStatus;
  message: string;
  evidence?: StatisticalEvidence | null;
  insight?: InsightCard | null;
  report?: DiscoveryReport | null;
  engine_mode: EngineMode;
}

export interface WalkSimulationRequest {
  start_cgm: number;
  active_iob: number;
  planned_steps: number;
}

export type HypoRiskLevel = 'low' | 'moderate' | 'high';

export interface WalkSimulationResponse {
  predicted_delta_mg_dl: number;
  predicted_end_cgm: number;
  hypo_risk_level: HypoRiskLevel;
  recommendation: string;
  model_r: number;
}

export interface ChatRequest {
  question: string;
  history: Record<string, string>[];
}

export interface ChatResponse {
  answer: string;
  supporting_numbers: string[];
  engine_mode: EngineMode;
}

export interface PersonaInfo {
  id: string;
  title: string;
  subtitle: string;
  badges: string[];
  is_local_real: boolean;
}

// API Bundle / Health types matching backend/glycolens/api/app.py
export interface SessionBundleResponse {
  session_id: string;
  profile: DataProfile;
  replays: DayReplay[];
}

export interface EngineHealthStatus {
  status: string;
  gemini_configured: boolean;
  has_api_key?: boolean;
  model?: string;
}
