from __future__ import annotations

import asyncio
import inspect
import json
import re
from typing import Any, AsyncIterator, Callable, Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from glycolens.agents.config import (
    create_genai_client,
    get_fast_model,
    get_pro_model,
    has_gemini_api_key,
)
from glycolens.schemas import (
    CanonicalDataset,
    DataProfile,
    DiscoveryReport,
    InsightCard,
    StatisticalEvidence,
    SwarmStreamEvent,
)
from glycolens.stats import (
    probe_activity_patterns,
    probe_dosing_patterns,
    probe_sleep_patterns,
    probe_vitals_patterns,
    query_custom_cohort_split,
)

DomainLiteral = Literal["activity", "sleep", "dosing", "vitals"]
CategoryLiteral = Literal["habit_experiment", "pre_activity_shield", "doctor_discussion"]
EngineModeLiteral = Literal["gemini_swarm", "statistical_fallback"]

_STREAM_DONE = object()

MEDICAL_SAFETY_SYSTEM_RULES = """
CRITICAL MEDICAL & CLINICAL SAFETY GUARDRAILS (MANDATORY):
1. NEVER prescribe, calculate, or recommend specific insulin doses, unit changes, carb ratios, basal rates, correction/sensitivity factors (ISF), or target glucose numbers.
2. ANY observation involving insulin pump parameters or programmed settings — including Carb Ratio (ICR / I:C), Target Glucose, Basal rate/schedule, Correction Factor (ISF), automated insulin delivery settings, or manual bolus calculator overrides — MUST be classified with category="doctor_discussion" and framed strictly as collaborative questions to review with an endocrinologist or diabetes care team.
3. Lifestyle, meal-timing awareness, pod rotation cadence, sleep consistency, or post-meal gentle movement observations must use category="habit_experiment".
4. Pre-exercise preparation habits (checking active IOB + starting CGM before walks/workouts, fueling buffers, or zone pacing) must use category="pre_activity_shield" (unless proposing programmed pump setting edits, which require "doctor_discussion").
5. Ground every number, confidence interval, and timestamp strictly in the provided deterministic StatisticalEvidence and DataProfile. Never fabricate statistics or timestamps.
""".strip()

_PUMP_PARAM_PATTERN = re.compile(
    r"\b("
    r"carb\s*ratio|carb_ratio|i\s*:\s*c|\bicr\b|"
    r"target\s*glucose|glucose\s*target|"
    r"basal\s*rate|basal\s*schedule|basal\s*profile|overnight\s*basal|programmed\s*basal|"
    r"pump\s*config|pump\s*setting|pump\s*parameter|bolus\s*calculator|"
    r"sensitivity\s*factor|correction\s*factor|\bisf\b"
    r")\b",
    re.IGNORECASE,
)

_PRESCRIPTIVE_DOSE_PATTERN = re.compile(
    r"\b("
    r"(?:increase|decrease|raise|lower|change|adjust|set|reduce)\s+(?:your\s+)?"
    r"(?:insulin|bolus|basal|carb\s*ratio|dose|units|icr|isf)\b|"
    r"take\s+\d+(?:\.\d+)?\s*(?:u|units)\b|"
    r"inject\s+\d+(?:\.\d+)?\s*(?:u|units)\b"
    r")",
    re.IGNORECASE,
)

_DOCTOR_DISCUSSION_HYPOTHESIS_IDS = {
    "dosing_pump_config_bypass_review",
    "sleep_wake_foot_on_floor_surge",
    "vitals_wrist_temp_resp_resistance",
    "vitals_smartwatch_bp_surge",
}

_PRE_ACTIVITY_HYPOTHESIS_IDS = {
    "activity_iob_walk_drop_rate",
    "activity_unprotected_walk_hypo",
    "activity_anaerobic_adrenaline_spike",
    "vitals_high_steps_evening_bolus_hypo",
}


class _DraftInsightSpec(BaseModel):
    hypothesis_id: str = Field(
        description="Exact hypothesis_id from the verified StatisticalEvidence list."
    )
    category: CategoryLiteral = Field(
        description=(
            "Safety category: 'doctor_discussion' for ANY pump parameter/Carb Ratio/"
            "Target Glucose/Basal schedule topic; 'pre_activity_shield' for pre-exercise "
            "protection; 'habit_experiment' for daily lifestyle habits."
        )
    )
    severity_rank: int = Field(
        default=1, description="Priority rank starting at 1 for highest personal impact."
    )
    headline: str = Field(
        description="Specific, empathetic, human-readable headline (8 to 15 words)."
    )
    plain_explanation: str = Field(
        description=(
            "2-3 clear sentences explaining the pattern in plain language, citing exact "
            "cohort means, effect diff, and 95% CI from the verified evidence."
        )
    )
    impact_badge: str = Field(
        description="Compact quantitative badge, e.g. '+16.4% TIR' or '-38 mg/dL drop'."
    )
    personal_anchor: str = Field(
        description="Concrete reference to this user's real timestamps/dates and sample counts."
    )
    try_this_week: str = Field(
        description=(
            "Safe next step: for 'doctor_discussion', a specific question to bring to "
            "an endocrinologist (NEVER prescribe doses!); for lifestyle categories, a "
            "practical 1-week behavioral experiment."
        )
    )


class _SynthesizerOutput(BaseModel):
    executive_summary: str = Field(
        description=(
            "Warm, clinically grounded 3-4 sentence overview synthesizing how this "
            "person's movement, sleep, dosing habits, and vitals interact."
        )
    )
    insights: list[_DraftInsightSpec] = Field(
        description="Top 5 to 7 verified insights ordered from highest to lowest priority."
    )


# Curated fallback templates keyed by hypothesis_id (with generic domain fallback for any new IDs)
_FALLBACK_COPY_BY_HYPOTHESIS: dict[str, dict[str, str]] = {
    "activity_iob_walk_drop_rate": {
        "category": "pre_activity_shield",
        "headline": "Brisk walks with active bolus insulin accelerate your glucose drop",
        "try_this_week": (
            "Before heading out for a brisk walk within 2–3 hours of a meal bolus, "
            "glance at your active IOB and starting CGM trend in the Pre-Walk Shield "
            "simulator and keep fast-acting carbs handy."
        ),
    },
    "activity_unprotected_walk_hypo": {
        "category": "pre_activity_shield",
        "headline": "Starting walks below buffer threshold with active IOB raises 90-minute low risk",
        "try_this_week": (
            "Try a 1-week pre-walk check: when starting CGM is under ~155 mg/dL with "
            "active bolus insulin on board, use a pre-activity carb buffer or wait until "
            "trend arrows stabilize before setting off."
        ),
    },
    "activity_anaerobic_adrenaline_spike": {
        "category": "pre_activity_shield",
        "headline": "High-heart-rate anaerobic sessions trigger an adrenaline glucose rise vs Zone-2",
        "try_this_week": (
            "Pair intense high-HR intervals with a 10-minute gentle Zone-2 cool-down walk "
            "and avoid reactive post-workout corrections while post-exercise insulin "
            "sensitivity rebounds—discuss exercise targets with your care team."
        ),
    },
    "activity_high_bg_walk_rescue": {
        "category": "habit_experiment",
        "headline": "Gentle 15–25 minute walks smoothly bring down post-meal high glucose",
        "try_this_week": (
            "When post-meal glucose drifts above 180 mg/dL with modest IOB, experiment "
            "with an easy 15–20 minute conversational walk instead of sitting still, "
            "while watching your CGM trend."
        ),
    },
    "sleep_wake_foot_on_floor_surge": {
        "category": "doctor_discussion",
        "headline": "Consistent morning 'foot-on-floor' glucose surge appears after waking",
        "try_this_week": (
            "Bring these wake-up timestamps to your next endocrinologist visit to ask "
            "whether your morning basal schedule, target glucose, or breakfast carb "
            "ratio window aligns with your waking cortisol rise."
        ),
    },
    "sleep_stage_rem_vs_deep_drift": {
        "category": "habit_experiment",
        "headline": "Overnight glucose drifts differently during REM vs restorative Deep sleep",
        "try_this_week": (
            "Protect early-night Deep sleep by keeping bedroom temperature cool and "
            "finishing heavy meals 3 hours before lights-out, then compare overnight "
            "CGM smoothness across the week."
        ),
    },
    "sleep_high_bg_nights_duration": {
        "category": "habit_experiment",
        "headline": "High overnight glucose nights cut into your total restorative sleep time",
        "try_this_week": (
            "Aim for a calm evening wind-down and earlier dinner window on 4 weeknights "
            "to reduce overnight high alarms and reclaim uninterrupted sleep."
        ),
    },
    "sleep_short_vs_restorative_next_day": {
        "category": "habit_experiment",
        "headline": "Sleeping under 6 hours measurably lowers your next-day Time in Range",
        "try_this_week": (
            "Treat a 7-hour sleep opportunity as a metabolic anchor this week—especially "
            "before busy workdays—and notice how much steadier post-breakfast CGM behaves."
        ),
    },
    "dosing_reactive_vs_prebolus": {
        "category": "habit_experiment",
        "headline": "Bolusing into an already-rising meal curve leads to higher post-meal peaks",
        "try_this_week": (
            "Focus purely on meal-bolus timing consistency (without altering prescribed "
            "doses): pair your mealtime bolus ritual with plating food rather than "
            "waiting until glucose is already climbing rapidly."
        ),
    },
    "dosing_post_hypo_rebound": {
        "category": "habit_experiment",
        "headline": "Fast carb over-treatment during lows drives 3-hour rebound hyperglycemia",
        "try_this_week": (
            "Pre-portion measured fast-acting low treatments (e.g., glucose tabs or "
            "juice pouches) at your bedside and desk, rechecking CGM at 15 minutes "
            "to avoid 'panic-snacking' rebounds."
        ),
    },
    "dosing_late_night_bolusing": {
        "category": "habit_experiment",
        "headline": "Late-night eating and bolusing after 9 PM erodes overnight Time in Range",
        "try_this_week": (
            "Try an 'early kitchen close' before 21:00 on 4 nights this week so meal "
            "digestion and active insulin settle before sleep."
        ),
    },
    "dosing_pump_config_bypass_review": {
        "category": "doctor_discussion",
        "headline": "Programmed pump ratios and manual bolus patterns warrant an endocrinology review",
        "try_this_week": (
            "Share this report with your endocrinologist and ask: 'Do my programmed "
            "Carb Ratios, Basal schedule, and Target Glucose settings still fit my "
            "daily meal windows, or am I working around them manually?'"
        ),
    },
    "dosing_expired_pod_wear": {
        "category": "habit_experiment",
        "headline": "Late-cycle / stretched pod wear windows show slower insulin absorption",
        "try_this_week": (
            "Set a calendar reminder 2 hours before pod expiration and rotate infusion "
            "sites consistently to keep absorption predictable on day 3."
        ),
    },
    "vitals_workweek_vs_weekend_tir": {
        "category": "habit_experiment",
        "headline": "Your glycemic rhythm shifts noticeably between workweek and weekend routines",
        "try_this_week": (
            "Keep one anchor habit consistent across Saturday and Sunday—such as wake "
            "time within 60 minutes or a morning walk—to bridge the routine gap."
        ),
    },
    "vitals_high_steps_evening_bolus_hypo": {
        "category": "pre_activity_shield",
        "headline": "High-step days (>11k steps) amplify evening insulin sensitivity overnight",
        "try_this_week": (
            "On 11,000+ step days, flag bedtime for extra low-prevention awareness and "
            "ask your endocrinologist whether an activity/overnight temp target strategy "
            "makes sense after heavy movement days."
        ),
    },
    "vitals_wrist_temp_resp_resistance": {
        "category": "doctor_discussion",
        "headline": "Elevated overnight wrist temperature & breathing rate precede next-day resistance",
        "try_this_week": (
            "When your watch flags simultaneous overnight wrist temperature and respiratory "
            "jumps, prioritize hydration/rest and ask your endocrinologist ahead of time "
            "for a personalized sick-day / stress management plan."
        ),
    },
    "vitals_smartwatch_bp_surge": {
        "category": "doctor_discussion",
        "headline": "Higher smartwatch blood pressure readings track alongside elevated glucose windows",
        "try_this_week": (
            "Bring your paired blood pressure and CGM log to your clinician to review "
            "stress, sleep, hydration, and cardiometabolic targets together."
        ),
    },
}


def invoke_domain_probe(
    probe_fn: Callable[..., list[StatisticalEvidence]],
    ds: CanonicalDataset,
    profile: DataProfile,
) -> list[StatisticalEvidence]:
    """Invoke a domain probe cleanly whether it accepts (ds) or (ds, profile)."""
    sig = inspect.signature(probe_fn)
    params = list(sig.parameters.values())
    if len(params) >= 2:
        return probe_fn(ds, profile)
    return probe_fn(ds)


def make_bound_cohort_tool(
    ds: CanonicalDataset,
    tool_call_log: Optional[list[dict[str, Any]]] = None,
) -> Callable[..., dict[str, Any]]:
    """
    Create a google-genai automatic-function-calling tool wrapping
    `glycolens.stats.query_custom_cohort_split` with `ds` bound.
    """

    def query_cohort_split_tool(
        metric: str,
        split_feature: str,
        threshold: float,
    ) -> dict[str, Any]:
        """
        Evaluate an ad-hoc daily cohort threshold split on this user's dataset using
        a 1000-iteration bootstrap 95% CI.

        Args:
            metric: Glycemic outcome to compare ('tir_pct' or 'mean_cgm').
            split_feature: Daily feature to split on ('steps', 'sleep_hours', 'bolus_units', 'systolic_bp', or 'wrist_temp_c').
            threshold: Numeric split cutoff comparing days >= threshold vs days < threshold.
        """
        try:
            res = query_custom_cohort_split(
                ds,
                metric=metric,
                split_feature=split_feature,
                threshold=float(threshold),
            )
            payload = (
                res.model_dump()
                if hasattr(res, "model_dump")
                else (dict(res) if isinstance(res, dict) else {"result": str(res)})
            )
        except Exception as exc:
            payload = {
                "error": str(exc),
                "statistically_significant": False,
            }
        if tool_call_log is not None:
            tool_call_log.append(payload)
        return payload

    query_cohort_split_tool.__name__ = "query_custom_cohort_split"
    return query_cohort_split_tool


def format_profile_summary(profile: DataProfile) -> str:
    """Compact, high-signal textual digest of DataProfile for LLM prompts."""
    ratios_str = (
        ", ".join(f"{k}: {v}" for k, v in list(profile.programmed_ratios_by_hour.items())[:8])
        if profile.programmed_ratios_by_hour
        else "not reported"
    )
    agp_highlights = ""
    if profile.hourly_agp:
        peak_hr = max(profile.hourly_agp, key=lambda h: h.median_cgm)
        low_hr = max(profile.hourly_agp, key=lambda h: h.low_pct)
        best_hr = max(profile.hourly_agp, key=lambda h: h.tir_pct)
        agp_highlights = (
            f"Peak median hour: {peak_hr.hour:02d}:00 ({peak_hr.median_cgm:.0f} mg/dL); "
            f"Highest hypo-exposure hour: {low_hr.hour:02d}:00 ({low_hr.low_pct:.1f}% <70); "
            f"Best TIR hour: {best_hr.hour:02d}:00 ({best_hr.tir_pct:.1f}% TIR)."
        )
    dow_highlights = ""
    if profile.day_of_week:
        best_day = max(profile.day_of_week, key=lambda d: d.tir_pct)
        worst_day = min(profile.day_of_week, key=lambda d: d.tir_pct)
        dow_highlights = (
            f"Best day: {best_day.day} ({best_day.tir_pct:.1f}% TIR, {best_day.steps} steps, {best_day.sleep_hours:.1f}h sleep); "
            f"Toughest day: {worst_day.day} ({worst_day.tir_pct:.1f}% TIR, {worst_day.steps} steps, {worst_day.sleep_hours:.1f}h sleep)."
        )
    return (
        f"Dataset: {profile.title} ({profile.start_date} to {profile.end_date}, {profile.days_covered:.1f} days)\n"
        f"Glycemia: Mean {profile.mean_cgm:.1f} mg/dL, Median {profile.median_cgm:.1f} mg/dL, "
        f"SD {profile.sd_cgm:.1f}, CV {profile.cv_pct:.1f}%, GMI {profile.gmi_pct:.2f}%, "
        f"TIR (70-180) {profile.tir_pct:.1f}%, TBR (<70) {profile.tbr_pct:.1f}%, "
        f"TAR (>180) {profile.tar_pct:.1f}%, Very High (>250) {profile.very_high_pct:.1f}%\n"
        f"Insulin & Dosing: {profile.bolus_count} boluses ({profile.pct_zero_carb_boluses:.1f}% zero-carb), "
        f"Mean TDD {profile.mean_tdd_units:.1f} U, Mean Basal {profile.mean_basal_units:.1f} U, "
        f"Programmed Ratios by Hour: {ratios_str}\n"
        f"Wearable & Context Counts: {profile.workout_count} workouts, {profile.sleep_count} sleep sessions, "
        f"{profile.bp_count} BP readings, skin_temp={profile.has_skin_temp}, resp_rate={profile.has_resp_rate}, "
        f"undisturbed_7am_median={profile.undisturbed_7am_median}\n"
        f"{agp_highlights}\n{dow_highlights}"
    ).strip()


def _serialize_evidence_for_prompt(ev: StatisticalEvidence) -> dict[str, Any]:
    return {
        "hypothesis_id": ev.hypothesis_id,
        "domain": ev.domain,
        "title": ev.title,
        "statistically_significant": ev.statistically_significant,
        "condition_a_label": ev.chart_spec.condition_a_label,
        "condition_b_label": ev.chart_spec.condition_b_label,
        "n_a": ev.n_a,
        "n_b": ev.n_b,
        "mean_a": round(ev.mean_a, 2),
        "mean_b": round(ev.mean_b, 2),
        "diff": round(ev.diff, 2),
        "ci_95_low": round(ev.ci_95_low, 2),
        "ci_95_high": round(ev.ci_95_high, 2),
        "unit": ev.unit,
        "example_timestamps": ev.example_timestamps[:4],
        "raw_summary": ev.raw_summary,
    }


def _evidence_effect_score(ev: StatisticalEvidence) -> float:
    """Normalized magnitude score across heterogeneous clinical units for ranking."""
    abs_diff = abs(ev.diff)
    unit_lower = ev.unit.lower()
    if "mg/dl/hr" in unit_lower or "mg/dl/5m" in unit_lower:
        norm = abs_diff * 1.4
    elif "mg/dl" in unit_lower:
        norm = abs_diff / 2.2
    elif "%" in unit_lower:
        norm = abs_diff * 1.15
    elif "min" in unit_lower:
        norm = abs_diff / 2.5
    elif "hr" in unit_lower or "hour" in unit_lower:
        norm = abs_diff * 14.0
    else:
        norm = abs_diff

    # Modest bonus for tighter relative CI & solid sample size
    sample_factor = min(1.35, 0.85 + 0.015 * min(ev.n_a + ev.n_b, 35))
    return norm * sample_factor


def _format_impact_badge(ev: StatisticalEvidence) -> str:
    unit = ev.unit.strip()
    abs_d = abs(ev.diff)
    sign_d = f"{ev.diff:+.1f}" if abs_d < 10 else f"{ev.diff:+.0f}"
    if unit == "%":
        return f"{sign_d}% delta"
    return f"{sign_d} {unit}"


def _format_personal_anchor(ev: StatisticalEvidence) -> str:
    cond_a = ev.chart_spec.condition_a_label or "Condition A"
    cond_b = ev.chart_spec.condition_b_label or "Condition B"
    if ev.example_timestamps:
        shown = ", ".join(ev.example_timestamps[:3])
        return f"Grounded in {ev.n_a} vs {ev.n_b} personal windows ({cond_a} vs {cond_b}), including {shown}."
    return f"Measured across {ev.n_a} {cond_a} windows vs {ev.n_b} {cond_b} windows in your log."


def enforce_medical_safety(card: InsightCard) -> InsightCard:
    """
    Deterministic post-generation guardrail guaranteeing:
    1. Any pump parameter observation (Carb Ratio, Target Glucose, Basal schedule, ISF)
       is strictly classified as `category="doctor_discussion"`.
    2. No prescriptive insulin dosing commands ever reach the UI.
    """
    ev = card.evidence
    combined_text = " ".join(
        [
            ev.hypothesis_id,
            ev.title,
            ev.raw_summary,
            card.headline,
            card.plain_explanation,
            card.try_this_week,
        ]
    )

    category: CategoryLiteral = card.category
    if (
        ev.hypothesis_id in _DOCTOR_DISCUSSION_HYPOTHESIS_IDS
        or _PUMP_PARAM_PATTERN.search(combined_text)
    ):
        category = "doctor_discussion"
    elif (
        ev.hypothesis_id in _PRE_ACTIVITY_HYPOTHESIS_IDS
        and category != "doctor_discussion"
    ):
        category = "pre_activity_shield"

    try_text = card.try_this_week.strip()
    explanation_text = card.plain_explanation.strip()

    # Scrub any accidental prescriptive insulin commands
    if _PRESCRIPTIVE_DOSE_PATTERN.search(try_text) or (
        category == "doctor_discussion"
        and not re.search(
            r"\b(doctor|endocrinolog|clinician|care\s*team|provider|visit|ask|discuss|review)\b",
            try_text,
            re.IGNORECASE,
        )
    ):
        template = _FALLBACK_COPY_BY_HYPOTHESIS.get(ev.hypothesis_id, {})
        if category == "doctor_discussion" and template.get("category") == "doctor_discussion":
            try_text = template["try_this_week"]
        elif category == "doctor_discussion":
            anchors = (
                f" (such as {', '.join(ev.example_timestamps[:2])})"
                if ev.example_timestamps
                else ""
            )
            try_text = (
                f"Bring these logged patterns{anchors} to your next endocrinologist "
                f"appointment and ask whether your programmed pump settings in this "
                f"window still match your daily physiology—never adjust insulin "
                f"doses without clinical guidance."
            )
        else:
            try_text = template.get(
                "try_this_week",
                "Track when this pattern repeats over the next 7 days and review any "
                "insulin or pump parameter questions with your endocrinologist.",
            )

    if _PRESCRIPTIVE_DOSE_PATTERN.search(explanation_text):
        explanation_text = ev.raw_summary

    return card.model_copy(
        update={
            "category": category,
            "plain_explanation": explanation_text,
            "try_this_week": try_text,
        }
    )


def build_fallback_insight_card(ev: StatisticalEvidence, rank: int) -> InsightCard:
    """Build a clear, empathetic, safety-checked InsightCard deterministically from StatisticalEvidence."""
    tpl = _FALLBACK_COPY_BY_HYPOTHESIS.get(ev.hypothesis_id, {})

    raw_cat = tpl.get("category")
    if raw_cat in ("habit_experiment", "pre_activity_shield", "doctor_discussion"):
        category: CategoryLiteral = raw_cat  # type: ignore[assignment]
    elif ev.domain == "activity":
        category = "pre_activity_shield"
    elif ev.hypothesis_id in _DOCTOR_DISCUSSION_HYPOTHESIS_IDS:
        category = "doctor_discussion"
    else:
        category = "habit_experiment"

    headline = tpl.get("headline") or ev.title
    cond_a = ev.chart_spec.condition_a_label or "Baseline cohort"
    cond_b = ev.chart_spec.condition_b_label or "Comparison cohort"

    plain_explanation = (
        f"{ev.raw_summary} Across your personal data, {cond_a} (n={ev.n_a}) averaged "
        f"{ev.mean_a:.1f} {ev.unit} compared with {ev.mean_b:.1f} {ev.unit} during "
        f"{cond_b} (n={ev.n_b}), a verified shift of {ev.diff:+.1f} {ev.unit} "
        f"(95% CI [{ev.ci_95_low:+.1f}, {ev.ci_95_high:+.1f}])."
    )

    try_this_week = tpl.get("try_this_week") or (
        "Bring these timestamped examples to your endocrinologist to discuss how "
        "your care plan applies to this window."
        if category == "doctor_discussion"
        else "Observe this pattern across the next 7 days and test whether smoothing "
        "this daily routine narrows the glucose gap."
    )

    card = InsightCard(
        id=f"insight_{rank}_{ev.hypothesis_id}",
        domain=ev.domain,
        category=category,
        severity_rank=rank,
        headline=headline,
        plain_explanation=plain_explanation,
        impact_badge=_format_impact_badge(ev),
        personal_anchor=_format_personal_anchor(ev),
        try_this_week=try_this_week,
        evidence=ev,
    )
    return enforce_medical_safety(card)


def select_diverse_top_evidences(
    verified: list[StatisticalEvidence],
    max_cards: int = 6,
) -> list[StatisticalEvidence]:
    """
    Rank verified evidences by effect magnitude while ensuring cross-domain coverage
    when multiple domains have statistically significant signals.
    """
    if not verified:
        return []

    scored = sorted(verified, key=_evidence_effect_score, reverse=True)
    selected: list[StatisticalEvidence] = []
    seen_ids: set[str] = set()

    # Pass 1: top signal per active domain (in order of domain's strongest signal)
    seen_domains: set[str] = set()
    for ev in scored:
        if ev.domain not in seen_domains and len(selected) < max_cards:
            selected.append(ev)
            seen_ids.add(ev.hypothesis_id)
            seen_domains.add(ev.domain)

    # Pass 2: fill remaining slots up to max_cards (5-7 range) by effect score
    for ev in scored:
        if len(selected) >= max_cards:
            break
        if ev.hypothesis_id not in seen_ids:
            selected.append(ev)
            seen_ids.add(ev.hypothesis_id)

    # Re-sort chosen set strictly by impact magnitude
    selected.sort(key=_evidence_effect_score, reverse=True)
    return selected


def build_fallback_executive_summary(
    profile: DataProfile,
    tested_count: int,
    verified_count: int,
    insights: list[InsightCard],
) -> str:
    if not insights:
        return (
            f"Across {profile.days_covered:.0f} days of data ({profile.tir_pct:.1f}% Time in Range, "
            f"mean CGM {profile.mean_cgm:.0f} mg/dL), GlycoLens evaluated {tested_count} cross-domain "
            f"hypotheses across activity, sleep, dosing, and vitals. No single cohort split "
            f"crossed the strict 95% bootstrap confidence threshold yet—continue logging "
            f"meals, sleep, and movement to sharpen statistical power."
        )

    domains_found = sorted({c.domain for c in insights})
    top_headlines = "; ".join(c.headline.lower() for c in insights[:3])
    return (
        f"Across {profile.days_covered:.0f} days ({profile.cgm_count:,} CGM readings, "
        f"{profile.tir_pct:.1f}% Time in Range, {profile.tbr_pct:.1f}% below 70 mg/dL), "
        f"the 4-specialist discovery swarm tested {tested_count} physiological hypotheses "
        f"and verified {verified_count} statistically significant patterns spanning "
        f"{', '.join(domains_found)}. Your highest-leverage personal signals show that "
        f"{top_headlines}. Lifestyle experiments are separated clearly from pump-setting "
        f"questions to review with your endocrinologist."
    )


async def _run_specialist_worker(
    *,
    agent_name: str,
    domain: DomainLiteral,
    probe_fn: Callable[..., list[StatisticalEvidence]],
    ds: CanonicalDataset,
    profile: DataProfile,
    profile_digest: str,
    queue: asyncio.Queue[Any],
    use_gemini: bool,
    client: Optional[genai.Client],
    fast_model: str,
    stagger_delay: float,
) -> tuple[DomainLiteral, list[StatisticalEvidence], str, bool]:
    """
    Execute one parallel specialist:
    1. Emit running status
    2. Compute deterministic bootstrap hypotheses and stream each live
    3. If Gemini is enabled, call gemini-2.5-flash with `query_custom_cohort_split`
       tool to check confounders and draft domain interpretations.
    """
    mode: EngineModeLiteral = "gemini_swarm" if use_gemini else "statistical_fallback"
    if stagger_delay > 0:
        await asyncio.sleep(stagger_delay)

    await queue.put(
        SwarmStreamEvent(
            event_type="agent_status",
            agent_name=agent_name,
            domain=domain,
            status="running",
            message=f"{agent_name} scanning {domain} cohorts & bootstrap confidence intervals...",
            engine_mode=mode,
        )
    )

    evidences = await asyncio.to_thread(invoke_domain_probe, probe_fn, ds, profile)

    pace_sec = 0.08 if use_gemini else 0.12
    for ev in evidences:
        await asyncio.sleep(pace_sec)
        ev_status: Literal["verified", "rejected"] = (
            "verified" if ev.statistically_significant else "rejected"
        )
        verdict_word = "Verified" if ev.statistically_significant else "Rejected"
        msg = (
            f"{verdict_word}: {ev.title} "
            f"({ev.diff:+.1f} {ev.unit}, 95% CI [{ev.ci_95_low:+.1f}, {ev.ci_95_high:+.1f}], "
            f"n={ev.n_a} vs {ev.n_b})"
        )
        await queue.put(
            SwarmStreamEvent(
                event_type="hypothesis_tested",
                agent_name=agent_name,
                domain=domain,
                status=ev_status,
                message=msg,
                evidence=ev,
                engine_mode=mode,
            )
        )

    verified_evs = [e for e in evidences if e.statistically_significant]

    if not use_gemini or client is None:
        await asyncio.sleep(0.12)
        await queue.put(
            SwarmStreamEvent(
                event_type="agent_status",
                agent_name=agent_name,
                domain=domain,
                status="done",
                message=(
                    f"{agent_name} completed: {len(verified_evs)}/{len(evidences)} "
                    f"hypotheses statistically significant."
                ),
                engine_mode="statistical_fallback",
            )
        )
        return domain, evidences, "", False

    # Gemini Specialist Confounder Check & Candidate Interpretation
    await queue.put(
        SwarmStreamEvent(
            event_type="agent_status",
            agent_name=agent_name,
            domain=domain,
            status="testing",
            message=(
                f"{agent_name} auditing confounders & ranking {len(verified_evs)} "
                f"verified {domain} signals with {fast_model}..."
            ),
            engine_mode="gemini_swarm",
        )
    )

    tool_calls_logged: list[dict[str, Any]] = []
    bound_tool = make_bound_cohort_tool(ds, tool_call_log=tool_calls_logged)
    serialized_evs = [_serialize_evidence_for_prompt(e) for e in evidences]

    specialist_prompt = (
        f"You are the {agent_name} in the GlycoLens Multi-Agent Metabolic Discovery Swarm.\n\n"
        f"USER DATA PROFILE:\n{profile_digest}\n\n"
        f"DETERMINISTIC {domain.upper()} HYPOTHESIS TEST RESULTS:\n"
        f"{json.dumps(serialized_evs, indent=2)}\n\n"
        f"YOUR TASK:\n"
        f"1. Optionally call `query_custom_cohort_split` (at most 1-2 targeted calls) to check "
        f"daily confounders (e.g., whether daily `steps`, `sleep_hours`, `bolus_units`, "
        f"`systolic_bp`, or `wrist_temp_c` confound `tir_pct` or `mean_cgm`).\n"
        f"2. Focus ONLY on hypotheses with `statistically_significant: true`.\n"
        f"3. Write a concise clinical & behavioral brief (bulleted, under 220 words) ranking "
        f"the most impactful verified `{domain}` findings for THIS specific person, noting "
        f"any confounder insights, exact `hypothesis_id`s, personal timestamps, and the "
        f"appropriate safety category (`habit_experiment`, `pre_activity_shield`, or `doctor_discussion`)."
    )

    gen_cfg = types.GenerateContentConfig(
        system_instruction=MEDICAL_SAFETY_SYSTEM_RULES,
        tools=[bound_tool],
        temperature=0.2,
        thinking_config=types.ThinkingConfig(thinking_level="LOW"),
    )
    candidate_models = list(
        dict.fromkeys([fast_model, "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"])
    )

    for cand_model in candidate_models:
        try:
            chat_sess = client.aio.chats.create(model=cand_model, config=gen_cfg)
            resp = await chat_sess.send_message(specialist_prompt)
            memo_text = (resp.text or "").strip()
            extra_note = (
                f" ({len(tool_calls_logged)} confounder split check(s) run)"
                if tool_calls_logged
                else ""
            )
            await queue.put(
                SwarmStreamEvent(
                    event_type="agent_status",
                    agent_name=agent_name,
                    domain=domain,
                    status="done",
                    message=(
                        f"{agent_name} verified {len(verified_evs)}/{len(evidences)} "
                        f"signals{extra_note}."
                    ),
                    engine_mode="gemini_swarm",
                )
            )
            return domain, evidences, memo_text, True
        except Exception:
            continue

    await queue.put(
        SwarmStreamEvent(
            event_type="agent_status",
            agent_name=agent_name,
            domain=domain,
            status="done",
            message=(
                f"{agent_name} completed statistical verification "
                f"({len(verified_evs)}/{len(evidences)} significant)."
            ),
            engine_mode="statistical_fallback",
        )
    )
    return domain, evidences, "", False


async def _run_chief_synthesizer_gemini(
    *,
    client: genai.Client,
    pro_model: str,
    fast_model: str,
    profile_digest: str,
    verified_evidences: list[StatisticalEvidence],
    specialist_memos: dict[str, str],
) -> Optional[_SynthesizerOutput]:
    """
    Call Chief Clinical Synthesizer (`gemini-2.5-pro`, falling back to `gemini-2.5-flash`
    on quota/model error) with structured Pydantic JSON schema output.
    """
    target_min = min(5, len(verified_evidences))
    target_max = min(7, len(verified_evidences))
    verified_payload = [_serialize_evidence_for_prompt(e) for e in verified_evidences]
    memos_block = "\n\n".join(
        f"### {dom.upper()} SPECIALIST BRIEF:\n{memo}"
        for dom, memo in specialist_memos.items()
        if memo
    )

    synth_prompt = (
        f"You are the Chief Clinical Synthesizer for GlycoLens.\n\n"
        f"USER DATA PROFILE:\n{profile_digest}\n\n"
        f"SPECIALIST CONFOUNDER & INTERPRETATION BRIEFS:\n{memos_block or 'Use deterministic evidence below.'}\n\n"
        f"VERIFIED STATISTICAL EVIDENCE (SELECT ONLY FROM THESE EXACT `hypothesis_id`s):\n"
        f"{json.dumps(verified_payload, indent=2)}\n\n"
        f"SYNTHESIS INSTRUCTIONS:\n"
        f"1. Select the top {target_min} to {target_max} highest-impact verified findings across domains.\n"
        f"2. Every `hypothesis_id` MUST match an exact `hypothesis_id` from VERIFIED STATISTICAL EVIDENCE above.\n"
        f"3. Enforce the CRITICAL MEDICAL SAFETY GUARDRAIL: ANY observation touching Carb Ratio, "
        f"Target Glucose, Basal schedule/rate, ISF, or pump configuration MUST have "
        f"`category=\"doctor_discussion\"` and `try_this_week` phrased as collaborative questions "
        f"for an endocrinologist—NEVER prescribe insulin doses!\n"
        f"4. Write warm, empowering, plain-English explanations anchored in this person's real "
        f"numbers, 95% confidence intervals, and example timestamps."
    )

    gen_config = types.GenerateContentConfig(
        system_instruction=MEDICAL_SAFETY_SYSTEM_RULES,
        response_mime_type="application/json",
        response_schema=_SynthesizerOutput,
        temperature=0.2,
        thinking_config=types.ThinkingConfig(thinking_level="LOW"),
    )

    models_to_try = list(
        dict.fromkeys([pro_model, fast_model, "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"])
    )

    for model_name in models_to_try:
        try:
            resp = await client.aio.models.generate_content(
                model=model_name,
                contents=synth_prompt,
                config=gen_config,
            )
            if getattr(resp, "parsed", None) is not None and isinstance(
                resp.parsed, _SynthesizerOutput
            ):
                return resp.parsed
            raw_json = (resp.text or "").strip()
            if raw_json:
                return _SynthesizerOutput.model_validate_json(raw_json)
        except Exception:
            continue
    return None


def _assemble_gemini_insights(
    synth_out: _SynthesizerOutput,
    verified_evidences: list[StatisticalEvidence],
) -> list[InsightCard]:
    """
    Re-attach the EXACT `StatisticalEvidence` objects by `hypothesis_id` so charts
    and bootstrap intervals can never be corrupted by LLM text generation.
    """
    verified_by_id = {ev.hypothesis_id: ev for ev in verified_evidences}
    cards: list[InsightCard] = []
    seen_ids: set[str] = set()

    for draft in synth_out.insights:
        ev = verified_by_id.get(draft.hypothesis_id)
        if ev is None or draft.hypothesis_id in seen_ids:
            continue
        seen_ids.add(draft.hypothesis_id)
        rank = len(cards) + 1
        card = InsightCard(
            id=f"insight_{rank}_{ev.hypothesis_id}",
            domain=ev.domain,
            category=draft.category,
            severity_rank=rank,
            headline=draft.headline.strip(),
            plain_explanation=draft.plain_explanation.strip(),
            impact_badge=draft.impact_badge.strip() or _format_impact_badge(ev),
            personal_anchor=draft.personal_anchor.strip() or _format_personal_anchor(ev),
            try_this_week=draft.try_this_week.strip(),
            evidence=ev,
        )
        cards.append(enforce_medical_safety(card))
        if len(cards) >= 7:
            break

    # Top up to at least min(5, len(verified_evidences)) if LLM omitted valid IDs
    min_desired = min(5, len(verified_evidences))
    if len(cards) < min_desired:
        for ev in select_diverse_top_evidences(verified_evidences, max_cards=7):
            if ev.hypothesis_id not in seen_ids:
                seen_ids.add(ev.hypothesis_id)
                cards.append(build_fallback_insight_card(ev, len(cards) + 1))
            if len(cards) >= min_desired:
                break

    # Normalize sequential severity_rank 1..N
    return [
        c.model_copy(update={"id": f"insight_{idx}_{c.evidence.hypothesis_id}", "severity_rank": idx})
        for idx, c in enumerate(cards, start=1)
    ]


async def stream_autonomous_discovery(
    session_id: str,
    ds: CanonicalDataset,
    profile: DataProfile,
) -> AsyncIterator[SwarmStreamEvent]:
    """
    Orchestrate the 4 Parallel Specialist Agents + Chief Clinical Synthesizer,
    streaming live `SwarmStreamEvent`s via an `asyncio.Queue` in real time.
    """
    use_gemini = has_gemini_api_key()
    fast_model = get_fast_model()
    pro_model = get_pro_model()
    client: Optional[genai.Client] = None

    if use_gemini:
        try:
            client = create_genai_client()
        except Exception:
            use_gemini = False
            client = None

    profile_digest = format_profile_summary(profile)
    queue: asyncio.Queue[Any] = asyncio.Queue()

    specialists_spec: list[tuple[str, DomainLiteral, Callable[..., list[StatisticalEvidence]], float]] = [
        ("Activity & Movement Specialist", "activity", probe_activity_patterns, 0.00),
        ("Sleep & Circadian Specialist", "sleep", probe_sleep_patterns, 0.03),
        ("Dosing & Habit Specialist", "dosing", probe_dosing_patterns, 0.06),
        ("Vitals & Cardiometabolic Specialist", "vitals", probe_vitals_patterns, 0.09),
    ]

    specialist_tasks: list[asyncio.Task[tuple[DomainLiteral, list[StatisticalEvidence], str, bool]]] = []

    async def _coordinator() -> None:
        nonlocal specialist_tasks
        try:
            specialist_tasks = [
                asyncio.create_task(
                    _run_specialist_worker(
                        agent_name=name,
                        domain=dom,
                        probe_fn=fn,
                        ds=ds,
                        profile=profile,
                        profile_digest=profile_digest,
                        queue=queue,
                        use_gemini=use_gemini,
                        client=client,
                        fast_model=fast_model,
                        stagger_delay=delay,
                    )
                )
                for name, dom, fn, delay in specialists_spec
            ]

            results = await asyncio.gather(*specialist_tasks)

            all_evidences: list[StatisticalEvidence] = []
            specialist_memos: dict[str, str] = {}
            any_gemini_specialist_ok = False

            for dom, evs, memo, gem_ok in results:
                all_evidences.extend(evs)
                if memo:
                    specialist_memos[dom] = memo
                if gem_ok:
                    any_gemini_specialist_ok = True

            verified_evidences = [e for e in all_evidences if e.statistically_significant]
            hypotheses_tested = len(all_evidences)
            hypotheses_verified = len(verified_evidences)

            final_mode: EngineModeLiteral = (
                "gemini_swarm"
                if (use_gemini and client is not None and any_gemini_specialist_ok)
                else "statistical_fallback"
            )

            await queue.put(
                SwarmStreamEvent(
                    event_type="agent_status",
                    agent_name="Chief Synthesizer",
                    domain="synthesizer",
                    status="running",
                    message=(
                        f"Chief Clinical Synthesizer integrating {hypotheses_verified} "
                        f"verified signals across all 4 domains..."
                    ),
                    engine_mode=final_mode,
                )
            )

            insights: list[InsightCard] = []
            executive_summary = ""

            if final_mode == "gemini_swarm" and client is not None and verified_evidences:
                synth_out = await _run_chief_synthesizer_gemini(
                    client=client,
                    pro_model=pro_model,
                    fast_model=fast_model,
                    profile_digest=profile_digest,
                    verified_evidences=verified_evidences,
                    specialist_memos=specialist_memos,
                )
                if synth_out is not None:
                    insights = _assemble_gemini_insights(synth_out, verified_evidences)
                    executive_summary = synth_out.executive_summary.strip()
                else:
                    final_mode = "statistical_fallback"

            if final_mode == "statistical_fallback" or not executive_summary:
                if final_mode != "gemini_swarm":
                    await asyncio.sleep(0.12)
                top_evs = select_diverse_top_evidences(verified_evidences, max_cards=6)
                if not insights:
                    insights = [
                        build_fallback_insight_card(ev, idx)
                        for idx, ev in enumerate(top_evs, start=1)
                    ]
                if not executive_summary:
                    executive_summary = build_fallback_executive_summary(
                        profile=profile,
                        tested_count=hypotheses_tested,
                        verified_count=hypotheses_verified,
                        insights=insights,
                    )

            card_pace = 0.08 if final_mode == "gemini_swarm" else 0.12
            for card in insights:
                await asyncio.sleep(card_pace)
                await queue.put(
                    SwarmStreamEvent(
                        event_type="insight_ready",
                        agent_name="Chief Synthesizer",
                        domain="synthesizer",
                        status="verified",
                        message=f"Insight #{card.severity_rank}: {card.headline}",
                        evidence=card.evidence,
                        insight=card,
                        engine_mode=final_mode,
                    )
                )

            report = DiscoveryReport(
                session_id=session_id,
                engine_mode=final_mode,
                executive_summary=executive_summary,
                insights=insights,
                hypotheses_tested=hypotheses_tested,
                hypotheses_verified=hypotheses_verified,
            )

            await asyncio.sleep(0.06)
            await queue.put(
                SwarmStreamEvent(
                    event_type="complete",
                    agent_name="Chief Synthesizer",
                    domain="synthesizer",
                    status="done",
                    message=(
                        f"Discovery complete: {len(insights)} priority insights synthesized "
                        f"from {hypotheses_verified}/{hypotheses_tested} verified hypotheses."
                    ),
                    report=report,
                    engine_mode=final_mode,
                )
            )
        except asyncio.CancelledError:
            for t in specialist_tasks:
                if not t.done():
                    t.cancel()
            raise
        except Exception as exc:
            await queue.put(
                SwarmStreamEvent(
                    event_type="error",
                    agent_name="Chief Synthesizer",
                    domain="synthesizer",
                    status="error",
                    message=f"Discovery swarm encountered an error: {exc}",
                    engine_mode="statistical_fallback" if not use_gemini else "gemini_swarm",
                )
            )
        finally:
            await queue.put(_STREAM_DONE)

    coord_task = asyncio.create_task(_coordinator())
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_DONE:
                break
            yield item
    finally:
        if not coord_task.done():
            coord_task.cancel()
            await asyncio.gather(coord_task, return_exceptions=True)
        for t in specialist_tasks:
            if not t.done():
                t.cancel()
