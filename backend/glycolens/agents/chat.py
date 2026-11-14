from __future__ import annotations

import json
import re
from typing import Any, Optional

from google import genai
from google.genai import types

from glycolens.agents.config import (
    create_genai_client,
    get_fast_model,
    has_gemini_api_key,
)
from glycolens.agents.swarm import (
    MEDICAL_SAFETY_SYSTEM_RULES,
    format_profile_summary,
    invoke_domain_probe,
    make_bound_cohort_tool,
)
from glycolens.schemas import (
    CanonicalDataset,
    ChatRequest,
    ChatResponse,
    DataProfile,
    DiscoveryReport,
    StatisticalEvidence,
)
from glycolens.stats import (
    probe_activity_patterns,
    probe_dosing_patterns,
    probe_sleep_patterns,
    probe_vitals_patterns,
    query_custom_cohort_split,
)

_CHAT_SYSTEM_INSTRUCTION = f"""
You are GlycoLens 'Ask My Body', an empathetic, precision personal metabolic data analyst.
You answer questions strictly grounded in THIS specific user's wearable + CGM + insulin dataset.

{MEDICAL_SAFETY_SYSTEM_RULES}

RESPONSE GUIDELINES:
1. Cite concrete numbers, cohort comparisons, 95% confidence intervals, and example dates/timestamps from the user's DataProfile, Verified Evidence, or `query_custom_cohort_split` tool results.
2. Whenever the user asks about a custom daily cutoff or threshold (e.g., steps, sleep hours, daily bolus units, systolic BP, or wrist temperature vs TIR or mean CGM), call `query_custom_cohort_split` to compute exact bootstrap numbers rather than guessing!
3. Keep your explanation concise, clear, and well-structured (2–4 short paragraphs or crisp bullet points).
4. At the very end of your response, include a section starting with exact header `SUPPORTING_NUMBERS:` followed by 3 to 5 short bullet lines (`- Label: Value`), each under 65 characters, summarizing the key personal stats cited.
""".strip()

_PRESCRIPTIVE_CHECK = re.compile(
    r"\b(?:increase|decrease|change|adjust|set)\s+(?:your\s+)?(?:basal|carb\s*ratio|insulin\s*dose|icr|isf)\s+to\b",
    re.IGNORECASE,
)


def _gather_verified_evidence(
    ds: CanonicalDataset,
    profile: DataProfile,
    report: Optional[DiscoveryReport],
) -> list[StatisticalEvidence]:
    """Collect all verified StatisticalEvidence across report insights and domain probes."""
    by_id: dict[str, StatisticalEvidence] = {}
    if report is not None:
        for card in report.insights:
            by_id[card.evidence.hypothesis_id] = card.evidence

    # Supplement with full 4-domain probe sweep so questions outside top-6 cards still hit exact stats
    for probe_fn in (
        probe_activity_patterns,
        probe_sleep_patterns,
        probe_dosing_patterns,
        probe_vitals_patterns,
    ):
        try:
            for ev in invoke_domain_probe(probe_fn, ds, profile):
                if ev.statistically_significant and ev.hypothesis_id not in by_id:
                    by_id[ev.hypothesis_id] = ev
        except Exception:
            continue

    return list(by_id.values())


def _split_answer_and_supporting_numbers(
    raw_text: str,
    tool_logs: list[dict[str, Any]],
    profile: DataProfile,
    matched_evidences: list[StatisticalEvidence],
) -> tuple[str, list[str]]:
    """Extract `SUPPORTING_NUMBERS:` block cleanly from LLM output with deterministic fallback."""
    marker = re.search(r"\n*\s*#*\s*SUPPORTING_NUMBERS\s*:\s*\n", raw_text, re.IGNORECASE)
    chips: list[str] = []
    if marker:
        answer_part = raw_text[: marker.start()].strip()
        tail = raw_text[marker.end() :]
        for line in tail.splitlines():
            cleaned = re.sub(r"^[\s\-\*\•\d\.\)]+", "", line).strip()
            if cleaned:
                chips.append(cleaned[:90])
    else:
        answer_part = raw_text.strip()

    for tlog in tool_logs:
        if "split_feature" in tlog and "diff" in tlog:
            feat = tlog["split_feature"]
            thr = tlog["threshold"]
            metric = tlog["metric"]
            unit = tlog.get("unit", "")
            chip = (
                f"{feat} >= {thr:g} vs < {thr:g}: {tlog['mean_a']:.1f} vs "
                f"{tlog['mean_b']:.1f} {unit} {metric} (Δ {tlog['diff']:+.1f} {unit})"
            )
            if chip not in chips:
                chips.insert(0, chip)

    if not chips:
        chips.append(
            f"Overall TIR (70–180): {profile.tir_pct:.1f}% across {profile.days_covered:.0f}d"
        )
        chips.append(
            f"Mean CGM: {profile.mean_cgm:.0f} mg/dL (CV {profile.cv_pct:.1f}%, TBR {profile.tbr_pct:.1f}%)"
        )
        for ev in matched_evidences[:2]:
            chips.append(f"{ev.title[:38]}: {ev.diff:+.1f} {ev.unit} (n={ev.n_a} vs {ev.n_b})")

    return answer_part, chips[:6]


def _detect_and_run_adhoc_split(
    ds: CanonicalDataset,
    question_lower: str,
) -> Optional[dict[str, Any]]:
    """Detect natural-language daily threshold questions in fallback mode and run bootstrap split."""
    metric = (
        "mean_cgm"
        if any(w in question_lower for w in ("mean glucose", "average glucose", "mean cgm", "avg cgm"))
        else "tir_pct"
    )

    # Steps threshold (e.g. "8000 steps", "10k steps", "10,000 steps")
    m_steps_k = re.search(r"(\d+(?:\.\d+)?)\s*k\s*steps", question_lower)
    m_steps = re.search(r"(\d[\d,]{2,5})\s*steps", question_lower)
    if m_steps_k:
        thr = float(m_steps_k.group(1)) * 1000.0
        return query_custom_cohort_split(ds, metric=metric, split_feature="steps", threshold=thr)
    if m_steps:
        thr = float(m_steps.group(1).replace(",", ""))
        return query_custom_cohort_split(ds, metric=metric, split_feature="steps", threshold=thr)
    if "step" in question_lower and any(w in question_lower for w in ("high", "more", "compare", "split")):
        return query_custom_cohort_split(ds, metric=metric, split_feature="steps", threshold=8500.0)

    # Sleep hours threshold (e.g. "7 hours of sleep", "6.5h sleep", "under 6 hours")
    m_sleep = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b.*?\bsleep|\bsleep.*?\b(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b",
        question_lower,
    )
    if m_sleep:
        val = float(m_sleep.group(1) or m_sleep.group(2))
        if 2.5 <= val <= 12.0:
            return query_custom_cohort_split(
                ds, metric=metric, split_feature="sleep_hours", threshold=val
            )

    # Blood pressure threshold
    m_bp = re.search(r"(\d{3})\s*(?:mmhg|bp|systolic)", question_lower)
    if m_bp or ("blood pressure" in question_lower or "systolic" in question_lower):
        thr = float(m_bp.group(1)) if m_bp else 130.0
        res = query_custom_cohort_split(ds, metric=metric, split_feature="systolic_bp", threshold=thr)
        if res.get("n_a", 0) + res.get("n_b", 0) >= 3:
            return res

    return None


def _score_evidence_relevance(ev: StatisticalEvidence, q_lower: str) -> int:
    score = 0
    domain_keywords: dict[str, tuple[str, ...]] = {
        "activity": (
            "walk",
            "workout",
            "exercise",
            "run",
            "step",
            "activity",
            "move",
            "movement",
            "aerobic",
            "anaerobic",
            "hr",
            "heart rate",
            "iob",
            "drop",
        ),
        "sleep": (
            "sleep",
            "night",
            "overnight",
            "wake",
            "morning",
            "dawn",
            "foot on floor",
            "foot-on-floor",
            "rem",
            "deep",
            "bed",
        ),
        "dosing": (
            "bolus",
            "dose",
            "dosing",
            "insulin",
            "carb",
            "meal",
            "prebolus",
            "pre-bolus",
            "late",
            "eat",
            "dinner",
            "pod",
            "pump",
            "ratio",
            "basal",
            "rebound",
            "hypo",
            "low",
        ),
        "vitals": (
            "vital",
            "bp",
            "blood pressure",
            "systolic",
            "temp",
            "wrist",
            "resp",
            "breathing",
            "weekend",
            "weekday",
            "workweek",
            "saturday",
            "sunday",
            "monday",
        ),
    }
    for kw in domain_keywords.get(ev.domain, ()):
        if kw in q_lower:
            score += 3

    title_words = [w for w in re.findall(r"[a-z0-9]+", ev.title.lower()) if len(w) >= 4]
    for w in title_words:
        if w in q_lower:
            score += 2
    return score


def _build_statistical_fallback_chat(
    ds: CanonicalDataset,
    profile: DataProfile,
    report: Optional[DiscoveryReport],
    verified_evidences: list[StatisticalEvidence],
    req: ChatRequest,
) -> ChatResponse:
    """Smart deterministic 'Ask My Body' responder when GEMINI_API_KEY is not configured."""
    q = req.question.strip()
    q_lower = q.lower()

    adhoc = _detect_and_run_adhoc_split(ds, q_lower)
    scored_evs = sorted(
        verified_evidences,
        key=lambda e: (_score_evidence_relevance(e, q_lower), abs(e.diff)),
        reverse=True,
    )
    matched_evs = [e for e in scored_evs if _score_evidence_relevance(e, q_lower) > 0]
    if not matched_evs:
        matched_evs = scored_evs[:3]

    paragraphs: list[str] = []
    chips: list[str] = [
        f"Time in Range (70–180): {profile.tir_pct:.1f}% ({profile.days_covered:.0f} days)",
        f"Mean CGM: {profile.mean_cgm:.0f} mg/dL | CV: {profile.cv_pct:.1f}% | TBR: {profile.tbr_pct:.1f}%",
    ]

    if adhoc and not adhoc.get("error") and (adhoc.get("n_a", 0) > 0 or adhoc.get("n_b", 0) > 0):
        paragraphs.append(f"**Custom Cohort Split on Your Log:** {adhoc['summary']}")
        chips.insert(
            0,
            f"{adhoc['condition_a_label']} (n={adhoc['n_a']}): {adhoc['mean_a']:.1f} {adhoc['unit']} vs {adhoc['mean_b']:.1f} {adhoc['unit']} (Δ {adhoc['diff']:+.1f})",
        )

    # Day-of-week specific answer if asked about days / weekends
    if profile.day_of_week and any(
        w in q_lower
        for w in (
            "day of week",
            "weekend",
            "weekday",
            "workweek",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
            "best day",
            "worst day",
        )
    ):
        best_d = max(profile.day_of_week, key=lambda d: d.tir_pct)
        worst_d = min(profile.day_of_week, key=lambda d: d.tir_pct)
        paragraphs.append(
            f"**Day-of-Week Rhythm:** Your highest Time in Range occurs on **{best_d.day}** "
            f"(**{best_d.tir_pct:.1f}% TIR**, mean {best_d.mean_cgm:.0f} mg/dL, {best_d.steps:,} steps, "
            f"{best_d.sleep_hours:.1f}h sleep), whereas **{worst_d.day}** is your toughest day "
            f"(**{worst_d.tir_pct:.1f}% TIR**, mean {worst_d.mean_cgm:.0f} mg/dL, {worst_d.steps:,} steps)."
        )
        chips.append(f"Best Day ({best_d.day}): {best_d.tir_pct:.1f}% TIR")
        chips.append(f"Lowest Day ({worst_d.day}): {worst_d.tir_pct:.1f}% TIR")

    # Hourly / time-of-day AGP insights
    if profile.hourly_agp and any(
        w in q_lower
        for w in ("hour", "time of day", "morning", "afternoon", "evening", "overnight", "dawn", "agp", "peak")
    ):
        peak_h = max(profile.hourly_agp, key=lambda h: h.median_cgm)
        low_h = max(profile.hourly_agp, key=lambda h: h.low_pct)
        paragraphs.append(
            f"**24-Hour AGP Profile:** Your median glucose peaks around **{peak_h.hour:02d}:00** "
            f"at **{peak_h.median_cgm:.0f} mg/dL** (IQR {peak_h.p25:.0f}–{peak_h.p75:.0f} mg/dL), "
            f"while your highest hypoglycemia exposure window is **{low_h.hour:02d}:00** "
            f"(**{low_h.low_pct:.1f}%** of readings <70 mg/dL)."
        )
        chips.append(f"Peak Hour ({peak_h.hour:02d}:00): {peak_h.median_cgm:.0f} mg/dL median")

    # Walk model specifics if asking about walking / exercise / IOB
    if profile.walk_model and any(
        w in q_lower for w in ("walk", "exercise", "workout", "iob", "step", "activity")
    ):
        wm = profile.walk_model
        paragraphs.append(
            f"**Personal Walk Response Model (n={wm.sample_count} walks, r={wm.holdout_r:.2f}):** "
            f"Across {profile.workout_count} logged sessions, your glucose change during walks "
            f"depends strongly on both starting CGM (mean {wm.mean_g0:.0f} mg/dL) and active "
            f"bolus IOB at start (mean {wm.mean_iob:.2f} U, mean {wm.mean_steps:.0f} steps)."
        )
        chips.append(
            f"Walk Model: n={wm.sample_count} walks (MAE {wm.mae_mg_dl:.1f} mg/dL, r={wm.holdout_r:.2f})"
        )

    # Verified statistical evidence bullets
    if matched_evs:
        ev_lines: list[str] = []
        for ev in matched_evs[:3]:
            ts_note = (
                f" (e.g., {', '.join(ev.example_timestamps[:2])})"
                if ev.example_timestamps
                else ""
            )
            ev_lines.append(
                f"- **{ev.title}:** {ev.raw_summary}{ts_note}"
            )
            chips.append(
                f"{ev.title[:36]}: {ev.diff:+.1f} {ev.unit} [95% CI {ev.ci_95_low:+.1f}, {ev.ci_95_high:+.1f}]"
            )
        paragraphs.append("**Verified Personal Signals:**\n" + "\n".join(ev_lines))
    else:
        paragraphs.append(
            f"**Dataset Overview ({profile.title}):** Over **{profile.days_covered:.1f} days** "
            f"({profile.start_date} to {profile.end_date}), you logged **{profile.cgm_count:,} CGM readings** "
            f"with **{profile.tir_pct:.1f}% Time in Range**, **{profile.tbr_pct:.1f}% below 70 mg/dL**, "
            f"**{profile.tar_pct:.1f}% above 180 mg/dL**, and **{profile.bolus_count} boluses** "
            f"(mean TDD {profile.mean_tdd_units:.1f} U)."
        )

    # Medical safety note whenever insulin/ratios/basal/pump topics come up
    if any(
        w in q_lower
        for w in ("ratio", "basal", "dose", "unit", "pump", "target", "icr", "isf", "bolus")
    ):
        ratios_note = (
            f" (currently logged programmed ratios: {', '.join(f'{k}={v}' for k, v in list(profile.programmed_ratios_by_hour.items())[:5])})"
            if profile.programmed_ratios_by_hour
            else ""
        )
        paragraphs.append(
            f"*Clinical Safety Note:* GlycoLens surfaces statistical patterns in your own logs{ratios_note} "
            f"and never prescribes insulin doses. Please review any Carb Ratio, Basal schedule, or "
            f"Target Glucose questions directly with your endocrinologist."
        )

    dedup_chips: list[str] = []
    for c in chips:
        if c not in dedup_chips:
            dedup_chips.append(c)

    return ChatResponse(
        answer="\n\n".join(paragraphs),
        supporting_numbers=dedup_chips[:6],
        engine_mode="statistical_fallback",
    )


def answer_data_question(
    ds: CanonicalDataset,
    profile: DataProfile,
    report: DiscoveryReport | None,
    req: ChatRequest,
) -> ChatResponse:
    """
    Answer a natural-language user question about their own GlycoLens data using
    Gemini (`google-genai`) grounded in `profile` + verified `StatisticalEvidence`
    + `query_custom_cohort_split` tool, falling back deterministically if unconfigured.
    """
    verified_evidences = _gather_verified_evidence(ds, profile, report)

    if not has_gemini_api_key():
        return _build_statistical_fallback_chat(
            ds=ds,
            profile=profile,
            report=report,
            verified_evidences=verified_evidences,
            req=req,
        )

    try:
        client: genai.Client = create_genai_client()
    except Exception:
        return _build_statistical_fallback_chat(
            ds=ds,
            profile=profile,
            report=report,
            verified_evidences=verified_evidences,
            req=req,
        )

    tool_logs: list[dict[str, Any]] = []
    bound_tool = make_bound_cohort_tool(ds, tool_call_log=tool_logs)

    evidence_digest = [
        {
            "hypothesis_id": ev.hypothesis_id,
            "domain": ev.domain,
            "title": ev.title,
            "condition_a": ev.chart_spec.condition_a_label,
            "condition_b": ev.chart_spec.condition_b_label,
            "n_a": ev.n_a,
            "n_b": ev.n_b,
            "mean_a": round(ev.mean_a, 2),
            "mean_b": round(ev.mean_b, 2),
            "diff": round(ev.diff, 2),
            "ci_95": [round(ev.ci_95_low, 2), round(ev.ci_95_high, 2)],
            "unit": ev.unit,
            "example_timestamps": ev.example_timestamps[:3],
            "summary": ev.raw_summary,
        }
        for ev in verified_evidences
    ]

    history_lines: list[str] = []
    for turn in req.history[-8:]:
        role = turn.get("role", "user").upper()
        content = (turn.get("content") or turn.get("text") or "").strip()
        if content:
            history_lines.append(f"{role}: {content}")
    history_block = "\n".join(history_lines) if history_lines else "(No prior conversation turns)"

    report_summary = report.executive_summary if report is not None else "(Report not yet generated)"

    prompt = (
        f"USER DATA PROFILE:\n{format_profile_summary(profile)}\n\n"
        f"DAY-OF-WEEK BREAKDOWN:\n"
        f"{json.dumps([d.model_dump() for d in profile.day_of_week], indent=2)}\n\n"
        f"EXECUTIVE SUMMARY:\n{report_summary}\n\n"
        f"VERIFIED STATISTICAL EVIDENCE:\n{json.dumps(evidence_digest, indent=2)}\n\n"
        f"RECENT CHAT HISTORY:\n{history_block}\n\n"
        f"USER QUESTION:\n{req.question.strip()}"
    )

    gen_cfg = types.GenerateContentConfig(
        system_instruction=_CHAT_SYSTEM_INSTRUCTION,
        tools=[bound_tool],
        temperature=0.2,
        thinking_config=types.ThinkingConfig(thinking_level="LOW"),
    )
    candidate_models = list(
        dict.fromkeys([get_fast_model(), "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash"])
    )

    for cand_model in candidate_models:
        try:
            chat_sess = client.chats.create(model=cand_model, config=gen_cfg)
            resp = chat_sess.send_message(prompt)
            raw_text = (resp.text or "").strip()
            if not raw_text:
                continue

            q_lower = req.question.lower()
            relevant_evs = sorted(
                verified_evidences,
                key=lambda e: (_score_evidence_relevance(e, q_lower), abs(e.diff)),
                reverse=True,
            )
            answer_text, supporting_numbers = _split_answer_and_supporting_numbers(
                raw_text=raw_text,
                tool_logs=tool_logs,
                profile=profile,
                matched_evidences=relevant_evs,
            )

            # Final deterministic safety check on chat answer
            if _PRESCRIPTIVE_CHECK.search(answer_text):
                answer_text += (
                    "\n\n*Clinical Safety Reminder: Never change programmed insulin doses, "
                    "Carb Ratios, or Basal schedules without reviewing these logs with your endocrinologist.*"
                )

            return ChatResponse(
                answer=answer_text,
                supporting_numbers=supporting_numbers,
                engine_mode="gemini_swarm",
            )
        except Exception:
            continue

    return _build_statistical_fallback_chat(
        ds=ds,
        profile=profile,
        report=report,
        verified_evidences=verified_evidences,
        req=req,
    )
