from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, timedelta
import hashlib
import math
import random
import statistics
from typing import Any, Literal, Optional

from glycolens.schemas import (
    CanonicalDataset,
    ChartSpec,
    PairedBarItem,
    SleepSessionSummary,
    StatisticalEvidence,
)
from glycolens.stats.profiler import (
    GlucoseIndex,
    build_bolus_index,
    enrich_workout_metrics,
    parse_date_safe,
    parse_ts_min,
    percentile_sorted,
    ts_date_str,
    ts_hour_float,
    ts_hour_int,
)

BOOTSTRAP_ITERATIONS = 1000


def _deterministic_rng(key: str) -> random.Random:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big")
    return random.Random(seed)


def bootstrap_independent_diff(
    vals_a: list[float],
    vals_b: list[float],
    seed_key: str,
    iterations: int = BOOTSTRAP_ITERATIONS,
) -> tuple[float, float, float, float, float]:
    """
    Compute (mean_a, mean_b, diff=mean_a-mean_b, ci_95_low, ci_95_high)
    via deterministic 1000-iteration nonparametric bootstrap.
    """
    na = len(vals_a)
    nb = len(vals_b)
    if na == 0 or nb == 0:
        ma = statistics.fmean(vals_a) if na > 0 else 0.0
        mb = statistics.fmean(vals_b) if nb > 0 else 0.0
        d = ma - mb
        return (round(ma, 2), round(mb, 2), round(d, 2), 0.0, 0.0)

    mean_a = statistics.fmean(vals_a)
    mean_b = statistics.fmean(vals_b)
    diff = mean_a - mean_b

    if na < 2 and nb < 2:
        return (round(mean_a, 2), round(mean_b, 2), round(diff, 2), round(diff, 2), round(diff, 2))

    rng = _deterministic_rng(seed_key)
    choices = rng.choices
    inv_na = 1.0 / na
    inv_nb = 1.0 / nb
    boot_diffs: list[float] = []
    for _ in range(iterations):
        sa = sum(choices(vals_a, k=na)) * inv_na
        sb = sum(choices(vals_b, k=nb)) * inv_nb
        boot_diffs.append(sa - sb)

    boot_diffs.sort()
    ci_low = percentile_sorted(boot_diffs, 2.5)
    ci_high = percentile_sorted(boot_diffs, 97.5)
    return (
        round(mean_a, 2),
        round(mean_b, 2),
        round(diff, 2),
        round(ci_low, 2),
        round(ci_high, 2),
    )


def bootstrap_paired_diff(
    vals_a: list[float],
    vals_b: list[float],
    seed_key: str,
    iterations: int = BOOTSTRAP_ITERATIONS,
) -> tuple[float, float, float, float, float]:
    """
    Compute paired 1000-iteration bootstrap CI for (a_i - b_i) when A and B are matched on the same N units.
    """
    n = min(len(vals_a), len(vals_b))
    if n == 0:
        return (0.0, 0.0, 0.0, 0.0, 0.0)

    a_sub = vals_a[:n]
    b_sub = vals_b[:n]
    mean_a = statistics.fmean(a_sub)
    mean_b = statistics.fmean(b_sub)
    deltas = [a - b for a, b in zip(a_sub, b_sub)]
    diff = statistics.fmean(deltas)

    if n < 2:
        return (round(mean_a, 2), round(mean_b, 2), round(diff, 2), round(diff, 2), round(diff, 2))

    rng = _deterministic_rng(seed_key)
    choices = rng.choices
    inv_n = 1.0 / n
    boot_diffs = [sum(choices(deltas, k=n)) * inv_n for _ in range(iterations)]
    boot_diffs.sort()
    ci_low = percentile_sorted(boot_diffs, 2.5)
    ci_high = percentile_sorted(boot_diffs, 97.5)
    return (
        round(mean_a, 2),
        round(mean_b, 2),
        round(diff, 2),
        round(ci_low, 2),
        round(ci_high, 2),
    )


def make_bar(
    label: str,
    val_a: float,
    val_b: float,
    unit: str = "",
    higher_is_better: bool = True,
    fixed_max: Optional[float] = None,
) -> PairedBarItem:
    """Create CSS-safe non-negative PairedBarItem with clean headroom scaling."""
    ca = round(max(0.0, float(val_a) if math.isfinite(val_a) else 0.0), 1)
    cb = round(max(0.0, float(val_b) if math.isfinite(val_b) else 0.0), 1)
    peak = max(ca, cb)
    if fixed_max is not None and fixed_max >= peak and fixed_max > 0:
        mv = round(float(fixed_max), 1)
    elif unit == "%" and peak <= 100.0:
        mv = 100.0
    else:
        mv = round(max(peak * 1.18, 1.0), 1)
    return PairedBarItem(
        label=label,
        condition_a_val=ca,
        condition_b_val=cb,
        max_val=mv,
        unit=unit,
        higher_is_better=higher_is_better,
    )


def is_sig(
    n_a: int,
    n_b: int,
    diff: float,
    ci_low: float,
    ci_high: float,
    min_abs_diff: float = 1.0,
    expected_sign: Optional[Literal["pos", "neg"]] = None,
) -> bool:
    """Enforce n_a >= 5, n_b >= 5, 95% CI excluding zero, minimum effect size, and directional coherence."""
    if n_a < 5 or n_b < 5:
        return False
    ci_excludes_zero = (ci_low > 0.0) or (ci_high < 0.0)
    if not ci_excludes_zero:
        return False
    if abs(diff) < min_abs_diff:
        return False
    if expected_sign == "pos" and diff <= 0.0:
        return False
    if expected_sign == "neg" and diff >= 0.0:
        return False
    return True


def _enrich_sleep_session(s: SleepSessionSummary, g_idx: GlucoseIndex) -> dict[str, Any]:
    s_min = parse_ts_min(s.start_ts)
    e_min = parse_ts_min(s.end_ts)
    wake_cgm = s.wake_cgm
    if wake_cgm is None and e_min is not None:
        wake_cgm = g_idx.nearest_val(e_min, max_tol_min=25)

    surge_3h = s.surge_3h_mg_dl
    peak_3h: Optional[float] = None
    if e_min is not None:
        win_3h = g_idx.window_vals(e_min, e_min + 180)
        if win_3h:
            peak_3h = max(win_3h)
            if surge_3h is None and wake_cgm is not None:
                surge_3h = peak_3h - wake_cgm
    if peak_3h is None and wake_cgm is not None and surge_3h is not None:
        peak_3h = wake_cgm + surge_3h

    tir_on = s.overnight_tir_pct
    tar_on = s.overnight_tar180_pct
    tbr_on = s.overnight_tbr70_pct
    if (tir_on is None or tar_on is None or tbr_on is None) and s_min is not None and e_min is not None:
        ov = g_idx.window_vals(s_min, e_min)
        if ov:
            cnt = len(ov)
            if tir_on is None:
                tir_on = sum(1 for v in ov if 70.0 <= v <= 180.0) * 100.0 / cnt
            if tar_on is None:
                tar_on = sum(1 for v in ov if v > 180.0) * 100.0 / cnt
            if tbr_on is None:
                tbr_on = sum(1 for v in ov if v < 70.0) * 100.0 / cnt

    return {
        "session": s,
        "start_min": s_min,
        "end_min": e_min,
        "wake_cgm": wake_cgm,
        "surge_3h": surge_3h,
        "peak_3h": peak_3h,
        "overnight_tir": tir_on,
        "overnight_tar180": tar_on,
        "overnight_tbr70": tbr_on,
    }


# ============================================================================
# 1. ACTIVITY DOMAIN PROBES
# ============================================================================


def probe_activity_patterns(ds: CanonicalDataset) -> list[StatisticalEvidence]:
    g_idx = GlucoseIndex(ds.glucose)
    b_mins, b_events = build_bolus_index(ds.boluses)
    trust_zero_iob = any(w.iob_at_start > 0 for w in ds.workouts)
    enriched = [
        enrich_workout_metrics(w, g_idx, b_mins, b_events, trust_zero_iob)
        for w in ds.workouts
    ]

    brisk_walks = [
        r
        for r in enriched
        if (
            "walk" in r["workout"].activity_type.lower()
            or "hike" in r["workout"].activity_type.lower()
            or (r["workout"].mean_hr < 150 and r["steps"] >= 400)
        )
        and r["delta_cgm"] is not None
        and r["workout"].duration_min >= 8.0
    ]

    results: list[StatisticalEvidence] = []

    # (a) Active Bolus IOB (>=1U) vs Zero-IOB Brisk Walk glucose drop rate
    iob_walks = [r for r in brisk_walks if r["iob_at_start"] >= 1.0]
    strict_zero_walks = [r for r in brisk_walks if r["iob_at_start"] <= 0.05]
    zero_iob_walks = (
        strict_zero_walks
        if len(strict_zero_walks) >= 5
        else [r for r in brisk_walks if r["iob_at_start"] <= 0.25]
    )

    drop_rate_a = [
        (-float(r["delta_cgm"])) / max(r["workout"].duration_min / 60.0, 0.2)
        for r in iob_walks
    ]
    drop_rate_b = [
        (-float(r["delta_cgm"])) / max(r["workout"].duration_min / 60.0, 0.2)
        for r in zero_iob_walks
    ]
    ma, mb, diff, cil, cih = bootstrap_independent_diff(
        drop_rate_a, drop_rate_b, "activity_iob_walk_drop"
    )
    sig_a = is_sig(len(drop_rate_a), len(drop_rate_b), diff, cil, cih, min_abs_diff=10.0, expected_sign="pos")

    abs_drop_a = statistics.fmean(-float(r["delta_cgm"]) for r in iob_walks) if iob_walks else 0.0
    abs_drop_b = statistics.fmean(-float(r["delta_cgm"]) for r in zero_iob_walks) if zero_iob_walks else 0.0
    nadir_a = (
        statistics.fmean(float(r["nadir_90m_cgm"]) for r in iob_walks if r["nadir_90m_cgm"] is not None)
        if any(r["nadir_90m_cgm"] is not None for r in iob_walks)
        else 0.0
    )
    nadir_b = (
        statistics.fmean(float(r["nadir_90m_cgm"]) for r in zero_iob_walks if r["nadir_90m_cgm"] is not None)
        if any(r["nadir_90m_cgm"] is not None for r in zero_iob_walks)
        else 0.0
    )
    ex_a = [
        r["workout"].start_ts
        for r in sorted(iob_walks, key=lambda x: float(x["delta_cgm"]))[:4]
    ]
    results.append(
        StatisticalEvidence(
            domain="activity",
            hypothesis_id="activity_iob_walk_drop_rate",
            title="Active Bolus IOB (≥1U) vs Zero-IOB Brisk Walk Glucose Drop Rate",
            n_a=len(drop_rate_a),
            n_b=len(drop_rate_b),
            mean_a=ma,
            mean_b=mb,
            diff=diff,
            ci_95_low=cil,
            ci_95_high=cih,
            unit="mg/dL/hr",
            statistically_significant=sig_a,
            example_timestamps=ex_a,
            raw_summary=(
                f"Walks started with ≥1.0U active bolus IOB (n={len(drop_rate_a)}) dropped glucose at "
                f"{ma:.1f} mg/dL/hr (mean Δ {-abs_drop_a:+.1f} mg/dL, 90m nadir {nadir_a:.0f} mg/dL) vs "
                f"{mb:.1f} mg/dL/hr (mean Δ {-abs_drop_b:+.1f} mg/dL, 90m nadir {nadir_b:.0f} mg/dL) on "
                f"zero/minimal-IOB walks (n={len(drop_rate_b)}); Δ={diff:+.1f} mg/dL/hr "
                f"[95% CI {cil:+.1f}, {cih:+.1f}]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"Active IOB ≥1U Walks (n={len(drop_rate_a)})",
                condition_b_label=f"Zero-IOB Walks (n={len(drop_rate_b)})",
                bars=[
                    make_bar("Glucose Drop Speed", ma, mb, unit="mg/dL/hr", higher_is_better=False),
                    make_bar("Mean Session Drop", abs_drop_a, abs_drop_b, unit="mg/dL", higher_is_better=False),
                    make_bar("90m Post-Walk Nadir", nadir_a, nadir_b, unit="mg/dL", higher_is_better=True),
                ],
            ),
        )
    )

    # (b) Unprotected Walk Hypo Risk (start_cgm < 155 and iob >= 1.0 or start_cgm < 120 vs buffered walks)
    walks_with_nadir = [
        r
        for r in brisk_walks
        if r["start_cgm"] is not None
        and (r["nadir_90m_cgm"] is not None or r["end_cgm"] is not None)
    ]
    unprotected: list[dict[str, Any]] = []
    buffered: list[dict[str, Any]] = []
    for r in walks_with_nadir:
        s_cgm = float(r["start_cgm"])
        iob_v = float(r["iob_at_start"])
        nad = min(
            [
                float(x)
                for x in (r["end_cgm"], r["nadir_90m_cgm"])
                if x is not None
            ]
        )
        item = {**r, "effective_nadir": nad}
        if (s_cgm < 155.0 and iob_v >= 1.0) or (s_cgm < 120.0):
            unprotected.append(item)
        else:
            buffered.append(item)

    hypo70_a = [100.0 if r["effective_nadir"] < 70.0 else 0.0 for r in unprotected]
    hypo70_b = [100.0 if r["effective_nadir"] < 70.0 else 0.0 for r in buffered]
    hypo85_a = [100.0 if r["effective_nadir"] < 85.0 else 0.0 for r in unprotected]
    hypo85_b = [100.0 if r["effective_nadir"] < 85.0 else 0.0 for r in buffered]

    m70a, m70b, d70, cil70, cih70 = bootstrap_independent_diff(
        hypo70_a, hypo70_b, "activity_unprotected_hypo70"
    )
    m85a, m85b, d85, cil85, cih85 = bootstrap_independent_diff(
        hypo85_a, hypo85_b, "activity_unprotected_hypo85"
    )
    sig70 = is_sig(len(unprotected), len(buffered), d70, cil70, cih70, min_abs_diff=8.0, expected_sign="pos")
    sig85 = is_sig(len(unprotected), len(buffered), d85, cil85, cih85, min_abs_diff=10.0, expected_sign="pos")

    if sig70 or not sig85:
        pb_ma, pb_mb, pb_diff, pb_cil, pb_cih, pb_sig, pb_metric = (
            m70a, m70b, d70, cil70, cih70, sig70, "<70 mg/dL"
        )
    else:
        pb_ma, pb_mb, pb_diff, pb_cil, pb_cih, pb_sig, pb_metric = (
            m85a, m85b, d85, cil85, cih85, sig85, "<85 mg/dL"
        )

    nad_unp = statistics.fmean(r["effective_nadir"] for r in unprotected) if unprotected else 0.0
    nad_buf = statistics.fmean(r["effective_nadir"] for r in buffered) if buffered else 0.0
    ex_b = [
        r["workout"].start_ts
        for r in sorted(unprotected, key=lambda x: x["effective_nadir"])[:4]
    ]
    results.append(
        StatisticalEvidence(
            domain="activity",
            hypothesis_id="activity_unprotected_walk_hypo",
            title="Unprotected Walk Hypoglycemia Risk (<155 mg/dL + ≥1U IOB or <120 mg/dL vs Buffered)",
            n_a=len(unprotected),
            n_b=len(buffered),
            mean_a=pb_ma,
            mean_b=pb_mb,
            diff=pb_diff,
            ci_95_low=pb_cil,
            ci_95_high=pb_cih,
            unit="%",
            statistically_significant=pb_sig,
            example_timestamps=ex_b,
            raw_summary=(
                f"Unprotected walks (n={len(unprotected)}) dipped <70 mg/dL in {m70a:.1f}% and <85 mg/dL in "
                f"{m85a:.1f}% of sessions (mean 90m nadir {nad_unp:.0f} mg/dL) vs {m70b:.1f}% (<70) and "
                f"{m85b:.1f}% (<85) on buffered walks (n={len(buffered)}, nadir {nad_buf:.0f} mg/dL); "
                f"primary {pb_metric} Δ={pb_diff:+.1f}% [95% CI {pb_cil:+.1f}%, {pb_cih:+.1f}%]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"Unprotected Walks (n={len(unprotected)})",
                condition_b_label=f"Buffered Walks (n={len(buffered)})",
                bars=[
                    make_bar("Dropping <70 mg/dL", m70a, m70b, unit="%", higher_is_better=False),
                    make_bar("Dropping <85 mg/dL", m85a, m85b, unit="%", higher_is_better=False),
                    make_bar("90m Nadir Glucose", nad_unp, nad_buf, unit="mg/dL", higher_is_better=True),
                ],
            ),
        )
    )

    # (c) Anaerobic High-HR Workout (mean_hr >= 155) Adrenaline Spike vs Aerobic Zone-2 (mean_hr < 140)
    anaerobic = [
        r for r in enriched if r["workout"].mean_hr >= 155.0 and r["delta_cgm"] is not None
    ]
    aerobic_z2 = [
        r for r in enriched if 0.0 < r["workout"].mean_hr < 140.0 and r["delta_cgm"] is not None
    ]
    delta_ana = [float(r["delta_cgm"]) for r in anaerobic]
    delta_aer = [float(r["delta_cgm"]) for r in aerobic_z2]
    mc_a, mc_b, dc, cil_c, cih_c = bootstrap_independent_diff(
        delta_ana, delta_aer, "activity_anaerobic_spike"
    )
    sig_c = is_sig(len(delta_ana), len(delta_aer), dc, cil_c, cih_c, min_abs_diff=12.0, expected_sign="pos")

    pct_rising_ana = (
        sum(1 for d in delta_ana if d >= 10.0) * 100.0 / len(delta_ana) if delta_ana else 0.0
    )
    pct_rising_aer = (
        sum(1 for d in delta_aer if d >= 10.0) * 100.0 / len(delta_aer) if delta_aer else 0.0
    )
    end_ana = (
        statistics.fmean(float(r["end_cgm"]) for r in anaerobic if r["end_cgm"] is not None)
        if any(r["end_cgm"] is not None for r in anaerobic)
        else 0.0
    )
    end_aer = (
        statistics.fmean(float(r["end_cgm"]) for r in aerobic_z2 if r["end_cgm"] is not None)
        if any(r["end_cgm"] is not None for r in aerobic_z2)
        else 0.0
    )
    hr_ana = statistics.fmean(r["workout"].mean_hr for r in anaerobic) if anaerobic else 0.0
    hr_aer = statistics.fmean(r["workout"].mean_hr for r in aerobic_z2) if aerobic_z2 else 0.0
    ex_c = [
        r["workout"].start_ts
        for r in sorted(anaerobic, key=lambda x: float(x["delta_cgm"]), reverse=True)[:4]
    ]
    results.append(
        StatisticalEvidence(
            domain="activity",
            hypothesis_id="activity_anaerobic_adrenaline_spike",
            title="Anaerobic High-HR Workout (≥155 bpm) Adrenaline Spike vs Aerobic Zone-2 (<140 bpm)",
            n_a=len(delta_ana),
            n_b=len(delta_aer),
            mean_a=mc_a,
            mean_b=mc_b,
            diff=dc,
            ci_95_low=cil_c,
            ci_95_high=cih_c,
            unit="mg/dL",
            statistically_significant=sig_c,
            example_timestamps=ex_c,
            raw_summary=(
                f"High-HR anaerobic workouts ≥155 bpm (n={len(delta_ana)}, mean {hr_ana:.0f} bpm) shifted "
                f"glucose by {mc_a:+.1f} mg/dL ({pct_rising_ana:.0f}% surging ≥+10 mg/dL, end {end_ana:.0f} mg/dL) "
                f"vs {mc_b:+.1f} mg/dL during Zone-2 aerobic sessions <140 bpm (n={len(delta_aer)}, "
                f"mean {hr_aer:.0f} bpm, end {end_aer:.0f} mg/dL); Δ={dc:+.1f} mg/dL "
                f"[95% CI {cil_c:+.1f}, {cih_c:+.1f}]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"High-HR ≥155 bpm (n={len(delta_ana)})",
                condition_b_label=f"Zone-2 <140 bpm (n={len(delta_aer)})",
                bars=[
                    make_bar("Workouts Surging ≥+10", pct_rising_ana, pct_rising_aer, unit="%", higher_is_better=False),
                    make_bar("End-of-Workout CGM", end_ana, end_aer, unit="mg/dL", higher_is_better=False),
                    make_bar("Mean Heart Rate", hr_ana, hr_aer, unit="bpm", higher_is_better=False, fixed_max=205.0),
                ],
            ),
        )
    )

    # (d) Post-meal / High-glucose (>180) Walk Rescue vs In-range walk stability
    high_bg_walks = [
        r
        for r in brisk_walks
        if r["start_cgm"] is not None and float(r["start_cgm"]) > 180.0
    ]
    in_range_walks = [
        r
        for r in brisk_walks
        if r["start_cgm"] is not None and 70.0 <= float(r["start_cgm"]) <= 180.0
    ]
    drop_high = [-float(r["delta_cgm"]) for r in high_bg_walks]
    drop_in_range = [-float(r["delta_cgm"]) for r in in_range_walks]
    md_a, md_b, dd, cil_d, cih_d = bootstrap_independent_diff(
        drop_high, drop_in_range, "activity_high_bg_walk_rescue"
    )
    sig_d = is_sig(len(drop_high), len(drop_in_range), dd, cil_d, cih_d, min_abs_diff=10.0, expected_sign="pos")

    start_high = statistics.fmean(float(r["start_cgm"]) for r in high_bg_walks) if high_bg_walks else 0.0
    start_ir = statistics.fmean(float(r["start_cgm"]) for r in in_range_walks) if in_range_walks else 0.0
    end_high = (
        statistics.fmean(float(r["end_cgm"]) for r in high_bg_walks if r["end_cgm"] is not None)
        if any(r["end_cgm"] is not None for r in high_bg_walks)
        else max(0.0, start_high - md_a)
    )
    end_ir = (
        statistics.fmean(float(r["end_cgm"]) for r in in_range_walks if r["end_cgm"] is not None)
        if any(r["end_cgm"] is not None for r in in_range_walks)
        else max(0.0, start_ir - md_b)
    )
    ex_d = [
        r["workout"].start_ts
        for r in sorted(high_bg_walks, key=lambda x: float(x["delta_cgm"]))[:4]
    ]
    results.append(
        StatisticalEvidence(
            domain="activity",
            hypothesis_id="activity_high_bg_walk_rescue",
            title="Post-Meal High-Glucose (>180 mg/dL) Walk Rescue vs In-Range Walk Stability",
            n_a=len(drop_high),
            n_b=len(drop_in_range),
            mean_a=md_a,
            mean_b=md_b,
            diff=dd,
            ci_95_low=cil_d,
            ci_95_high=cih_d,
            unit="mg/dL",
            statistically_significant=sig_d,
            example_timestamps=ex_d,
            raw_summary=(
                f"Starting a brisk walk above 180 mg/dL (n={len(drop_high)}, mean start {start_high:.0f} → "
                f"end {end_high:.0f} mg/dL) lowered glucose by {md_a:.1f} mg/dL vs a gentler {md_b:.1f} mg/dL "
                f"drop when starting in-range 70–180 mg/dL (n={len(drop_in_range)}, {start_ir:.0f} → "
                f"{end_ir:.0f} mg/dL); rescue advantage Δ={dd:+.1f} mg/dL [95% CI {cil_d:+.1f}, {cih_d:+.1f}]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"High-BG Walks >180 (n={len(drop_high)})",
                condition_b_label=f"In-Range Walks (n={len(drop_in_range)})",
                bars=[
                    make_bar("Glucose Reduction", md_a, md_b, unit="mg/dL", higher_is_better=True),
                    make_bar("Starting Glucose", start_high, start_ir, unit="mg/dL", higher_is_better=False),
                    make_bar("Ending Glucose", end_high, end_ir, unit="mg/dL", higher_is_better=False),
                ],
            ),
        )
    )

    return results


# ============================================================================
# 2. SLEEP DOMAIN PROBES
# ============================================================================


def probe_sleep_patterns(ds: CanonicalDataset) -> list[StatisticalEvidence]:
    g_idx = GlucoseIndex(ds.glucose)
    enriched_sessions = [_enrich_sleep_session(s, g_idx) for s in ds.sleep_sessions]
    results: list[StatisticalEvidence] = []

    # (a) Watch Wake-Up Foot-on-Floor 3h Surge
    wake_mornings = [
        r
        for r in enriched_sessions
        if r["wake_cgm"] is not None and r["peak_3h"] is not None and r["surge_3h"] is not None
    ]
    peaks_3h = [float(r["peak_3h"]) for r in wake_mornings]
    wakes_0h = [float(r["wake_cgm"]) for r in wake_mornings]
    surges = [float(r["surge_3h"]) for r in wake_mornings]

    ma_s, mb_s, diff_s, cil_s, cih_s = bootstrap_paired_diff(
        peaks_3h, wakes_0h, "sleep_wake_foot_on_floor_surge"
    )
    sig_sa = is_sig(len(peaks_3h), len(wakes_0h), diff_s, cil_s, cih_s, min_abs_diff=20.0, expected_sign="pos")

    pct_surge_60 = (
        sum(1 for sg in surges if sg > 60.0) * 100.0 / len(surges) if surges else 0.0
    )
    pct_peak_180 = (
        sum(1 for pk in peaks_3h if pk > 180.0) * 100.0 / len(peaks_3h) if peaks_3h else 0.0
    )
    pct_wake_180 = (
        sum(1 for wk in wakes_0h if wk > 180.0) * 100.0 / len(wakes_0h) if wakes_0h else 0.0
    )
    ex_sa = [
        r["session"].end_ts
        for r in sorted(wake_mornings, key=lambda x: float(x["surge_3h"]), reverse=True)[:4]
    ]
    results.append(
        StatisticalEvidence(
            domain="sleep",
            hypothesis_id="sleep_wake_foot_on_floor_surge",
            title="Smartwatch Wake-Up Foot-on-Floor 3-Hour Glucose Surge",
            n_a=len(peaks_3h),
            n_b=len(wakes_0h),
            mean_a=ma_s,
            mean_b=mb_s,
            diff=diff_s,
            ci_95_low=cil_s,
            ci_95_high=cih_s,
            unit="mg/dL",
            statistically_significant=sig_sa,
            example_timestamps=ex_sa,
            raw_summary=(
                f"Across {len(peaks_3h)} watch-detected wake-ups, glucose rose from a wake-up mean of "
                f"{mb_s:.1f} mg/dL to a 3h peak of {ma_s:.1f} mg/dL (mean surge {diff_s:+.1f} mg/dL, "
                f"95% CI [{cil_s:+.1f}, {cih_s:+.1f}]); {pct_surge_60:.1f}% of mornings surged >+60 mg/dL."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"3h Post-Wake Peak (n={len(peaks_3h)})",
                condition_b_label=f"Wake-Up CGM (n={len(wakes_0h)})",
                bars=[
                    make_bar("Morning Glucose Level", ma_s, mb_s, unit="mg/dL", higher_is_better=False),
                    make_bar("Mornings >180 mg/dL", pct_peak_180, pct_wake_180, unit="%", higher_is_better=False),
                    make_bar("Surging >+60 mg/dL (vs 0% Goal)", pct_surge_60, 0.0, unit="%", higher_is_better=False),
                ],
            ),
        )
    )

    # (b) Sleep Stage 5-min CGM Drift (REM vs Deep sleep hourly drift mg/dL/hr)
    rem_drifts: list[tuple[float, float, str]] = []  # (drift_hr, mean_stage_cgm, start_ts)
    deep_drifts: list[tuple[float, float, str]] = []

    for st in ds.sleep_stages:
        if st.stage not in ("REM", "Deep") or st.duration_min < 4.0:
            continue
        sm = parse_ts_min(st.start_ts)
        em = parse_ts_min(st.end_ts)
        if sm is None:
            continue
        if em is None or em <= sm:
            em = sm + int(round(st.duration_min))

        pairs = g_idx.window_pairs(sm, em)
        drift_hr: Optional[float] = None
        stage_cgm: Optional[float] = None

        step_drifts: list[float] = []
        if len(pairs) >= 2:
            for idx_p in range(len(pairs) - 1):
                dt_m = pairs[idx_p + 1][0] - pairs[idx_p][0]
                if 3 <= dt_m <= 16:
                    step_drifts.append((pairs[idx_p + 1][1] - pairs[idx_p][1]) * (60.0 / dt_m))
            if step_drifts:
                drift_hr = statistics.median(step_drifts)
                stage_cgm = statistics.fmean(p[1] for p in pairs)
            elif (pairs[-1][0] - pairs[0][0]) >= 4:
                drift_hr = (pairs[-1][1] - pairs[0][1]) * (60.0 / (pairs[-1][0] - pairs[0][0]))
                stage_cgm = statistics.fmean(p[1] for p in pairs)

        if drift_hr is None:
            p_start = g_idx.nearest_pair(sm, max_tol_min=5)
            p_end = g_idx.nearest_pair(em, max_tol_min=5)
            if p_start is not None and p_end is not None and (p_end[0] - p_start[0]) >= 4:
                drift_hr = (p_end[1] - p_start[1]) / ((p_end[0] - p_start[0]) / 60.0)
                stage_cgm = 0.5 * (p_start[1] + p_end[1])
            else:
                pt = g_idx.nearest_point((sm + em) // 2, max_tol_min=10)
                if pt is not None and pt.slope_15m != 0.0:
                    drift_hr = pt.slope_15m * 4.0
                    stage_cgm = pt.mg_dl

        if drift_hr is not None and stage_cgm is not None:
            drift_clamped = max(-180.0, min(180.0, drift_hr))
            if st.stage == "REM":
                rem_drifts.append((drift_clamped, stage_cgm, st.start_ts))
            else:
                deep_drifts.append((drift_clamped, stage_cgm, st.start_ts))

    rem_vals = [x[0] for x in rem_drifts]
    deep_vals = [x[0] for x in deep_drifts]
    mb_a, mb_b, db_diff, cil_b, cih_b = bootstrap_independent_diff(
        rem_vals, deep_vals, "sleep_stage_rem_vs_deep_drift"
    )
    sig_sb = is_sig(len(rem_vals), len(deep_vals), db_diff, cil_b, cih_b, min_abs_diff=4.0)

    pct_up_rem = sum(1 for v in rem_vals if v > 5.0) * 100.0 / len(rem_vals) if rem_vals else 0.0
    pct_up_deep = sum(1 for v in deep_vals if v > 5.0) * 100.0 / len(deep_vals) if deep_vals else 0.0
    pos_drift_rem = statistics.fmean(max(0.0, v) for v in rem_vals) if rem_vals else 0.0
    pos_drift_deep = statistics.fmean(max(0.0, v) for v in deep_vals) if deep_vals else 0.0
    cgm_rem = statistics.fmean(x[1] for x in rem_drifts) if rem_drifts else 0.0
    cgm_deep = statistics.fmean(x[1] for x in deep_drifts) if deep_drifts else 0.0
    ex_sb = [x[2] for x in sorted(rem_drifts, key=lambda r: abs(r[0]), reverse=True)[:4]]

    results.append(
        StatisticalEvidence(
            domain="sleep",
            hypothesis_id="sleep_stage_rem_vs_deep_drift",
            title="Sleep Architecture 5-Min CGM Drift: REM vs Deep Sleep",
            n_a=len(rem_vals),
            n_b=len(deep_vals),
            mean_a=mb_a,
            mean_b=mb_b,
            diff=db_diff,
            ci_95_low=cil_b,
            ci_95_high=cih_b,
            unit="mg/dL/hr",
            statistically_significant=sig_sb,
            example_timestamps=ex_sb,
            raw_summary=(
                f"REM sleep stages (n={len(rem_vals)}) exhibited a mean glucose drift of {mb_a:+.1f} mg/dL/hr "
                f"({pct_up_rem:.0f}% rising >+5 mg/dL/hr) compared with {mb_b:+.1f} mg/dL/hr during restorative "
                f"Deep sleep (n={len(deep_vals)}, {pct_up_deep:.0f}% rising); Δ={db_diff:+.1f} mg/dL/hr "
                f"[95% CI {cil_b:+.1f}, {cih_b:+.1f}]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"REM Sleep Epochs (n={len(rem_vals)})",
                condition_b_label=f"Deep Sleep Epochs (n={len(deep_vals)})",
                bars=[
                    make_bar("Epochs Rising >+5 mg/dL/h", pct_up_rem, pct_up_deep, unit="%", higher_is_better=False),
                    make_bar("Upward Drift Speed", pos_drift_rem, pos_drift_deep, unit="mg/dL/hr", higher_is_better=False),
                    make_bar("Mean Stage Glucose", cgm_rem, cgm_deep, unit="mg/dL", higher_is_better=False),
                ],
            ),
        )
    )

    # (c) High-Glucose Nights (>=50% TAR >180) vs In-Range Nights (<15% TAR) impact on Total Sleep Duration
    high_tar_nights = [
        r
        for r in enriched_sessions
        if r["overnight_tar180"] is not None
        and float(r["overnight_tar180"]) >= 50.0
        and r["session"].total_sleep_min > 0
    ]
    clean_nights = [
        r
        for r in enriched_sessions
        if r["overnight_tar180"] is not None
        and float(r["overnight_tar180"]) < 15.0
        and r["session"].total_sleep_min > 0
    ]
    sleep_hrs_high = [r["session"].total_sleep_min / 60.0 for r in high_tar_nights]
    sleep_hrs_clean = [r["session"].total_sleep_min / 60.0 for r in clean_nights]

    mc_sa, mc_sb, dc_s, cil_sc, cih_sc = bootstrap_independent_diff(
        sleep_hrs_high, sleep_hrs_clean, "sleep_high_tar_duration"
    )
    sig_sc = is_sig(len(sleep_hrs_high), len(sleep_hrs_clean), dc_s, cil_sc, cih_sc, min_abs_diff=0.35, expected_sign="neg")

    restorative_high = (
        statistics.fmean((r["session"].deep_min + r["session"].rem_min) / 60.0 for r in high_tar_nights)
        if high_tar_nights
        else 0.0
    )
    restorative_clean = (
        statistics.fmean((r["session"].deep_min + r["session"].rem_min) / 60.0 for r in clean_nights)
        if clean_nights
        else 0.0
    )
    awake_high = statistics.fmean(r["session"].awake_min for r in high_tar_nights) if high_tar_nights else 0.0
    awake_clean = statistics.fmean(r["session"].awake_min for r in clean_nights) if clean_nights else 0.0
    ex_sc = [
        r["session"].start_ts
        for r in sorted(high_tar_nights, key=lambda x: x["session"].total_sleep_min)[:4]
    ]
    results.append(
        StatisticalEvidence(
            domain="sleep",
            hypothesis_id="sleep_high_bg_nights_duration",
            title="High-Glucose Nights (≥50% TAR >180) vs In-Range Nights (<15% TAR) Sleep Duration",
            n_a=len(sleep_hrs_high),
            n_b=len(sleep_hrs_clean),
            mean_a=mc_sa,
            mean_b=mc_sb,
            diff=dc_s,
            ci_95_low=cil_sc,
            ci_95_high=cih_sc,
            unit="hrs",
            statistically_significant=sig_sc,
            example_timestamps=ex_sc,
            raw_summary=(
                f"Nights with ≥50% time above 180 mg/dL (n={len(sleep_hrs_high)}) yielded {mc_sa:.2f} hrs total "
                f"sleep ({restorative_high:.2f} hrs Deep+REM, {awake_high:.0f} min awake) vs {mc_sb:.2f} hrs on "
                f"in-range nights <15% TAR (n={len(sleep_hrs_clean)}, {restorative_clean:.2f} hrs Deep+REM); "
                f"Δ={dc_s:+.2f} hrs [95% CI {cil_sc:+.2f}, {cih_sc:+.2f}]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"High-BG Nights ≥50% TAR (n={len(sleep_hrs_high)})",
                condition_b_label=f"Clean Nights <15% TAR (n={len(sleep_hrs_clean)})",
                bars=[
                    make_bar("Total Sleep Duration", mc_sa, mc_sb, unit="hrs", higher_is_better=True, fixed_max=10.0),
                    make_bar("Deep + REM Sleep", restorative_high, restorative_clean, unit="hrs", higher_is_better=True, fixed_max=5.0),
                    make_bar("Overnight Awake Time", awake_high, awake_clean, unit="min", higher_is_better=False),
                ],
            ),
        )
    )

    # (d) Short Sleep (<6h) vs Restorative Sleep (>=7h) impact on Next-Day Mean Glucose & TIR%
    short_sleep_rows: list[tuple[float, float, float, str]] = []  # (next_tir, next_mean, sleep_hrs, wake_ts)
    rest_sleep_rows: list[tuple[float, float, float, str]] = []

    for r in enriched_sessions:
        s = r["session"]
        if s.total_sleep_min <= 0:
            continue
        em = r["end_min"]
        next_vals: list[float] = []
        if em is not None:
            # 16 waking hours immediately following wake-up (handles shiftworkers waking at any hour!)
            next_vals = g_idx.window_vals(em, em + 16 * 60)
        if len(next_vals) < 6:
            wd = parse_date_safe(s.wake_date)
            if wd is not None:
                d_min = wd.toordinal() * 1440
                next_vals = g_idx.window_vals(d_min + 6 * 60, d_min + 23 * 60 + 59)
        if len(next_vals) < 6:
            continue

        next_tir = sum(1 for v in next_vals if 70.0 <= v <= 180.0) * 100.0 / len(next_vals)
        next_mean = statistics.fmean(next_vals)
        s_hrs = s.total_sleep_min / 60.0
        anchor_ts = s.end_ts or s.wake_date

        if s.total_sleep_min < 360.0:
            short_sleep_rows.append((next_tir, next_mean, s_hrs, anchor_ts))
        elif s.total_sleep_min >= 420.0:
            rest_sleep_rows.append((next_tir, next_mean, s_hrs, anchor_ts))

    tir_short = [x[0] for x in short_sleep_rows]
    tir_rest = [x[0] for x in rest_sleep_rows]
    cgm_short = [x[1] for x in short_sleep_rows]
    cgm_rest = [x[1] for x in rest_sleep_rows]

    md_ta, md_tb, dd_t, cil_dt, cih_dt = bootstrap_independent_diff(
        tir_short, tir_rest, "sleep_short_next_day_tir"
    )
    md_ga, md_gb, dd_g, cil_dg, cih_dg = bootstrap_independent_diff(
        cgm_short, cgm_rest, "sleep_short_next_day_cgm"
    )
    sig_tir = is_sig(len(tir_short), len(tir_rest), dd_t, cil_dt, cih_dt, min_abs_diff=5.0, expected_sign="neg")
    sig_cgm = is_sig(len(cgm_short), len(cgm_rest), dd_g, cil_dg, cih_dg, min_abs_diff=8.0, expected_sign="pos")

    hrs_short_mean = statistics.fmean(x[2] for x in short_sleep_rows) if short_sleep_rows else 0.0
    hrs_rest_mean = statistics.fmean(x[2] for x in rest_sleep_rows) if rest_sleep_rows else 0.0
    ex_sd = [x[3] for x in sorted(short_sleep_rows, key=lambda r: r[0])[:4]]

    if sig_tir or not sig_cgm:
        s4_ma, s4_mb, s4_diff, s4_cil, s4_cih, s4_unit, s4_sig = (
            md_ta, md_tb, dd_t, cil_dt, cih_dt, "%", sig_tir
        )
    else:
        s4_ma, s4_mb, s4_diff, s4_cil, s4_cih, s4_unit, s4_sig = (
            md_ga, md_gb, dd_g, cil_dg, cih_dg, "mg/dL", sig_cgm
        )

    results.append(
        StatisticalEvidence(
            domain="sleep",
            hypothesis_id="sleep_short_vs_restorative_next_day",
            title="Short Sleep (<6h) vs Restorative Sleep (≥7h) Next-Day Glycemic Control",
            n_a=len(short_sleep_rows),
            n_b=len(rest_sleep_rows),
            mean_a=s4_ma,
            mean_b=s4_mb,
            diff=s4_diff,
            ci_95_low=s4_cil,
            ci_95_high=s4_cih,
            unit=s4_unit,
            statistically_significant=s4_sig,
            example_timestamps=ex_sd,
            raw_summary=(
                f"Following <6h short sleep (n={len(short_sleep_rows)}, mean {hrs_short_mean:.1f}h), next-day "
                f"16h TIR averaged {md_ta:.1f}% with mean glucose {md_ga:.1f} mg/dL vs {md_tb:.1f}% TIR and "
                f"{md_gb:.1f} mg/dL after ≥7h restorative sleep (n={len(rest_sleep_rows)}, mean {hrs_rest_mean:.1f}h); "
                f"TIR Δ={dd_t:+.1f}% [95% CI {cil_dt:+.1f}%, {cih_dt:+.1f}%], Mean CGM Δ={dd_g:+.1f} mg/dL."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"Short Sleep <6h (n={len(short_sleep_rows)})",
                condition_b_label=f"Restorative ≥7h (n={len(rest_sleep_rows)})",
                bars=[
                    make_bar("Next-Day Time in Range", md_ta, md_tb, unit="%", higher_is_better=True),
                    make_bar("Next-Day Mean Glucose", md_ga, md_gb, unit="mg/dL", higher_is_better=False),
                    make_bar("Sleep Duration", hrs_short_mean, hrs_rest_mean, unit="hrs", higher_is_better=True, fixed_max=10.0),
                ],
            ),
        )
    )

    return results


# ============================================================================
# 3. DOSING DOMAIN PROBES
# ============================================================================


def probe_dosing_patterns(ds: CanonicalDataset) -> list[StatisticalEvidence]:
    g_idx = GlucoseIndex(ds.glucose)
    b_mins, b_events = build_bolus_index(ds.boluses)
    results: list[StatisticalEvidence] = []

    # (a) Reactive/Late Meal Bolusing (prior_25m_rise >= 18) vs On-Time Pre-Bolusing (glucose < 165 & rise <= 12)
    has_precomputed_rise = any(abs(b.prior_25m_rise) > 0.01 for b in ds.boluses)
    reactive_rows: list[tuple[float, float, float, str]] = []  # (tir_3h, over250_flag, peak_3h, ts)
    prebolus_rows: list[tuple[float, float, float, str]] = []

    meaningful_boluses = [b for b in ds.boluses if b.units >= 0.4]
    if len(meaningful_boluses) < 10:
        meaningful_boluses = list(ds.boluses)

    for b in meaningful_boluses:
        bm = parse_ts_min(b.ts)
        if bm is None:
            continue
        g_at = b.glucose_at_bolus
        if g_at is None:
            g_at = g_idx.nearest_val(bm, max_tol_min=15)
        if g_at is None:
            continue

        rise_25 = b.prior_25m_rise
        if not has_precomputed_rise and rise_25 == 0.0:
            g_prev = g_idx.nearest_val(bm - 25, max_tol_min=12)
            if g_prev is not None:
                rise_25 = g_at - g_prev

        post_3h = g_idx.window_vals(bm, bm + 180)
        if len(post_3h) < 3:
            continue
        tir_3h = sum(1 for v in post_3h if 70.0 <= v <= 180.0) * 100.0 / len(post_3h)
        peak_3h = max(post_3h)
        over250 = 100.0 if peak_3h > 250.0 else 0.0

        if rise_25 >= 18.0:
            reactive_rows.append((tir_3h, over250, peak_3h, b.ts))
        elif g_at < 165.0 and rise_25 <= 12.0:
            prebolus_rows.append((tir_3h, over250, peak_3h, b.ts))

    tir_reac = [x[0] for x in reactive_rows]
    tir_pre = [x[0] for x in prebolus_rows]
    ma_d1, mb_d1, diff_d1, cil_d1, cih_d1 = bootstrap_independent_diff(
        tir_reac, tir_pre, "dosing_reactive_vs_prebolus"
    )
    sig_d1 = is_sig(len(tir_reac), len(tir_pre), diff_d1, cil_d1, cih_d1, min_abs_diff=6.0, expected_sign="neg")

    pct250_reac = statistics.fmean(x[1] for x in reactive_rows) if reactive_rows else 0.0
    pct250_pre = statistics.fmean(x[1] for x in prebolus_rows) if prebolus_rows else 0.0
    peak_reac = statistics.fmean(x[2] for x in reactive_rows) if reactive_rows else 0.0
    peak_pre = statistics.fmean(x[2] for x in prebolus_rows) if prebolus_rows else 0.0
    ex_d1 = [x[3] for x in sorted(reactive_rows, key=lambda r: r[2], reverse=True)[:4]]

    results.append(
        StatisticalEvidence(
            domain="dosing",
            hypothesis_id="dosing_reactive_vs_prebolus",
            title="Reactive Late Bolusing (25m Rise ≥+18) vs On-Time Pre-Bolusing (<165 & Steady)",
            n_a=len(tir_reac),
            n_b=len(tir_pre),
            mean_a=ma_d1,
            mean_b=mb_d1,
            diff=diff_d1,
            ci_95_low=cil_d1,
            ci_95_high=cih_d1,
            unit="%",
            statistically_significant=sig_d1,
            example_timestamps=ex_d1,
            raw_summary=(
                f"Reactive boluses delivered after glucose was already surging ≥+18 mg/dL/25m (n={len(tir_reac)}) "
                f"achieved {ma_d1:.1f}% 3h TIR (3h peak {peak_reac:.0f} mg/dL, {pct250_reac:.1f}% spiking >250) "
                f"vs {mb_d1:.1f}% 3h TIR (peak {peak_pre:.0f} mg/dL, {pct250_pre:.1f}% >250) with on-time "
                f"pre-bolusing (n={len(tir_pre)}); TIR Δ={diff_d1:+.1f}% [95% CI {cil_d1:+.1f}%, {cih_d1:+.1f}%]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"Reactive Late Bolus (n={len(tir_reac)})",
                condition_b_label=f"On-Time Pre-Bolus (n={len(tir_pre)})",
                bars=[
                    make_bar("3h Post-Bolus TIR", ma_d1, mb_d1, unit="%", higher_is_better=True),
                    make_bar("3h Peak Glucose", peak_reac, peak_pre, unit="mg/dL", higher_is_better=False),
                    make_bar("Episodes >250 mg/dL", pct250_reac, pct250_pre, unit="%", higher_is_better=False),
                ],
            ),
        )
    )

    # (b) Post-Hypoglycemia Over-Treatment Rebounds (% of <70 episodes rebounding >180 and >250 within 3h)
    hypo_episodes: list[tuple[int, float, str]] = []  # (nadir_min, nadir_val, start_ts)
    idx = 0
    n_pts = len(g_idx)
    while idx < n_pts:
        if g_idx.vals[idx] < 70.0:
            ep_start_ts = g_idx.points[idx].ts
            nadir_min = g_idx.mins[idx]
            nadir_val = g_idx.vals[idx]
            last_low_min = g_idx.mins[idx]
            idx += 1
            while idx < n_pts and (g_idx.vals[idx] < 70.0 or (g_idx.mins[idx] - last_low_min <= 45 and g_idx.vals[idx] < 72.0)):
                if g_idx.vals[idx] < 70.0:
                    last_low_min = g_idx.mins[idx]
                if g_idx.vals[idx] < nadir_val:
                    nadir_val = g_idx.vals[idx]
                    nadir_min = g_idx.mins[idx]
                idx += 1
            hypo_episodes.append((nadir_min, nadir_val, ep_start_ts))
        else:
            idx += 1

    hypo_outcomes: list[tuple[float, float, float, str]] = []  # (reb180_pct, reb250_pct, peak_3h, ts)
    for n_min, _, ep_ts in hypo_episodes:
        rec_vals = g_idx.window_vals(n_min + 5, n_min + 180)
        if len(rec_vals) >= 2:
            pk = max(rec_vals)
            hypo_outcomes.append((100.0 if pk > 180.0 else 0.0, 100.0 if pk > 250.0 else 0.0, pk, ep_ts))

    # Baseline comparison: distinct non-hypo euglycemic 80-115 mg/dL points spaced >=6h apart
    baseline_outcomes: list[tuple[float, float, float]] = []
    last_base_min = -10_000
    for m, v in zip(g_idx.mins, g_idx.vals):
        if m - last_base_min < 360:
            continue
        if 80.0 <= v <= 115.0:
            # Ensure no <70 reading in prior 2h
            prior = g_idx.window_vals(m - 120, m)
            if prior and min(prior) < 70.0:
                continue
            nxt = g_idx.window_vals(m + 5, m + 180)
            if len(nxt) >= 3:
                pk = max(nxt)
                baseline_outcomes.append((100.0 if pk > 180.0 else 0.0, 100.0 if pk > 250.0 else 0.0, pk))
                last_base_min = m

    reb180_a = [x[0] for x in hypo_outcomes]
    reb180_b = [x[0] for x in baseline_outcomes]
    ma_d2, mb_d2, diff_d2, cil_d2, cih_d2 = bootstrap_independent_diff(
        reb180_a, reb180_b, "dosing_post_hypo_rebound"
    )
    sig_d2 = is_sig(len(reb180_a), len(reb180_b), diff_d2, cil_d2, cih_d2, min_abs_diff=15.0, expected_sign="pos")

    reb250_mean_a = statistics.fmean(x[1] for x in hypo_outcomes) if hypo_outcomes else 0.0
    reb250_mean_b = statistics.fmean(x[1] for x in baseline_outcomes) if baseline_outcomes else 0.0
    peak_hypo_a = statistics.fmean(x[2] for x in hypo_outcomes) if hypo_outcomes else 0.0
    peak_base_b = statistics.fmean(x[2] for x in baseline_outcomes) if baseline_outcomes else 0.0
    ex_d2 = [x[3] for x in sorted(hypo_outcomes, key=lambda r: r[2], reverse=True)[:4]]

    results.append(
        StatisticalEvidence(
            domain="dosing",
            hypothesis_id="dosing_post_hypo_rebound",
            title="Post-Hypoglycemia (<70 mg/dL) Over-Treatment Rebound Spikes Within 3 Hours",
            n_a=len(reb180_a),
            n_b=len(reb180_b),
            mean_a=ma_d2,
            mean_b=mb_d2,
            diff=diff_d2,
            ci_95_low=cil_d2,
            ci_95_high=cih_d2,
            unit="%",
            statistically_significant=sig_d2,
            example_timestamps=ex_d2,
            raw_summary=(
                f"Out of {len(reb180_a)} hypoglycemia episodes (<70 mg/dL), {ma_d2:.1f}% rebounded >180 mg/dL "
                f"and {reb250_mean_a:.1f}% surged >250 mg/dL within 3h (mean recovery peak {peak_hypo_a:.0f} mg/dL) "
                f"vs {mb_d2:.1f}% >180 mg/dL from steady 80–115 mg/dL baselines (n={len(reb180_b)}, peak "
                f"{peak_base_b:.0f} mg/dL); excess rebound Δ={diff_d2:+.1f}% [95% CI {cil_d2:+.1f}%, {cih_d2:+.1f}%]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"Post-Hypo <70 Windows (n={len(reb180_a)})",
                condition_b_label=f"Steady 80–115 Baselines (n={len(reb180_b)})",
                bars=[
                    make_bar("Rebounding >180 mg/dL (3h)", ma_d2, mb_d2, unit="%", higher_is_better=False),
                    make_bar("Severe Rebound >250 mg/dL", reb250_mean_a, reb250_mean_b, unit="%", higher_is_better=False),
                    make_bar("3h Peak Glucose", peak_hypo_a, peak_base_b, unit="mg/dL", higher_is_better=False),
                ],
            ),
        )
    )

    # (c) Late-Night Eating/Bolusing (21:00-01:30) vs Early Cutoff -> Overnight Hypo Risk %, Overnight TIR%, Sleep Hours
    sleep_by_wake: dict[str, SleepSessionSummary] = {}
    for s in ds.sleep_sessions:
        wd = s.wake_date or ts_date_str(s.end_ts)
        if wd:
            sleep_by_wake[wd] = s

    all_ord_days = sorted({m // 1440 for m in g_idx.mins})
    late_nights: list[tuple[float, float, Optional[float], str]] = []  # (hypo_flag, ov_tir, sleep_hrs, anchor)
    early_nights: list[tuple[float, float, Optional[float], str]] = []

    for d_ord in all_ord_days:
        win_late_start = d_ord * 1440 + 21 * 60
        win_late_end = (d_ord + 1) * 1440 + 1 * 60 + 30
        ov_vals = g_idx.window_vals((d_ord + 1) * 1440, (d_ord + 1) * 1440 + 6 * 60 + 30)
        if len(ov_vals) < 6:
            continue

        b_lo = bisect_left(b_mins, win_late_start)
        b_hi = bisect_right(b_mins, win_late_end)
        late_bolus_list = [b_events[i] for i in range(b_lo, b_hi) if b_events[i].units >= 0.3]

        next_date_str = date.fromordinal(d_ord + 1).isoformat()
        curr_date_str = date.fromordinal(d_ord).isoformat()
        s_sess = sleep_by_wake.get(next_date_str)
        sleep_hrs = (s_sess.total_sleep_min / 60.0) if (s_sess and s_sess.total_sleep_min > 0) else None

        hypo_flag = 100.0 if (min(ov_vals) < 70.0 or (s_sess and (s_sess.overnight_tbr70_pct or 0.0) > 0.0)) else 0.0
        ov_tir = sum(1 for v in ov_vals if 70.0 <= v <= 180.0) * 100.0 / len(ov_vals)

        if late_bolus_list:
            late_nights.append((hypo_flag, ov_tir, sleep_hrs, late_bolus_list[-1].ts))
        else:
            early_nights.append((hypo_flag, ov_tir, sleep_hrs, curr_date_str))

    hypo_late = [x[0] for x in late_nights]
    hypo_early = [x[0] for x in early_nights]
    tir_late = [x[1] for x in late_nights]
    tir_early = [x[1] for x in early_nights]

    mh_a, mh_b, dh, cil_h, cih_h = bootstrap_independent_diff(hypo_late, hypo_early, "dosing_late_night_hypo")
    mt_a, mt_b, dt, cil_t, cih_t = bootstrap_independent_diff(tir_late, tir_early, "dosing_late_night_tir")

    sig_hypo = is_sig(len(late_nights), len(early_nights), dh, cil_h, cih_h, min_abs_diff=10.0, expected_sign="pos")
    sig_tir_ln = is_sig(len(late_nights), len(early_nights), dt, cil_t, cih_t, min_abs_diff=6.0, expected_sign="neg")

    slp_late_vals = [x[2] for x in late_nights if x[2] is not None]
    slp_early_vals = [x[2] for x in early_nights if x[2] is not None]
    slp_late_mean = statistics.fmean(slp_late_vals) if slp_late_vals else 0.0
    slp_early_mean = statistics.fmean(slp_early_vals) if slp_early_vals else 0.0

    if sig_hypo or not sig_tir_ln:
        d3_ma, d3_mb, d3_diff, d3_cil, d3_cih, d3_sig = mh_a, mh_b, dh, cil_h, cih_h, sig_hypo
    else:
        d3_ma, d3_mb, d3_diff, d3_cil, d3_cih, d3_sig = mt_a, mt_b, dt, cil_t, cih_t, sig_tir_ln

    bars_d3 = [
        make_bar("Overnight Hypo Nights (<70)", mh_a, mh_b, unit="%", higher_is_better=False),
        make_bar("Overnight Time in Range", mt_a, mt_b, unit="%", higher_is_better=True),
    ]
    if slp_late_vals and slp_early_vals:
        bars_d3.append(
            make_bar("Watch Sleep Duration", slp_late_mean, slp_early_mean, unit="hrs", higher_is_better=True, fixed_max=10.0)
        )

    ex_d3 = [x[3] for x in sorted(late_nights, key=lambda r: (-r[0], r[1]))[:4]]
    results.append(
        StatisticalEvidence(
            domain="dosing",
            hypothesis_id="dosing_late_night_bolusing",
            title="Late-Night Eating & Bolusing (21:00–01:30) vs Early Kitchen Cutoff (<21:00)",
            n_a=len(late_nights),
            n_b=len(early_nights),
            mean_a=d3_ma,
            mean_b=d3_mb,
            diff=d3_diff,
            ci_95_low=d3_cil,
            ci_95_high=d3_cih,
            unit="%",
            statistically_significant=d3_sig,
            example_timestamps=ex_d3,
            raw_summary=(
                f"Nights with late boluses between 21:00–01:30 (n={len(late_nights)}) had {mh_a:.1f}% overnight "
                f"hypoglycemia risk, {mt_a:.1f}% overnight TIR, and {slp_late_mean:.2f}h sleep vs {mh_b:.1f}% hypo "
                f"risk, {mt_b:.1f}% TIR, and {slp_early_mean:.2f}h sleep after early <21:00 cutoff "
                f"(n={len(early_nights)}); primary Δ={d3_diff:+.1f}% [95% CI {d3_cil:+.1f}%, {d3_cih:+.1f}%]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"Late Bolus 21:00–01:30 (n={len(late_nights)})",
                condition_b_label=f"Early Cutoff <21:00 (n={len(early_nights)})",
                bars=bars_d3,
            ),
        )
    )

    # (d) Pump Configuration & Manual Bolus Bypass Review
    # Triggers on: (>80% zero-carb boluses) OR (evening TIR trough with weaker evening carb ratio) OR (7AM settle >=125 mg/dL)
    total_boluses = len(ds.boluses)
    pct_zero_carb = (
        sum(1 for b in ds.boluses if b.carbs_g <= 0.0) * 100.0 / total_boluses
        if total_boluses > 0
        else 0.0
    )

    day_ratios = [b.carb_ratio for b in ds.boluses if b.carb_ratio > 0 and 7 <= ts_hour_int(b.ts) <= 16]
    eve_ratios = [b.carb_ratio for b in ds.boluses if b.carb_ratio > 0 and (18 <= ts_hour_int(b.ts) <= 23)]
    mean_day_icr = statistics.fmean(day_ratios) if day_ratios else 0.0
    mean_eve_icr = statistics.fmean(eve_ratios) if eve_ratios else 0.0
    weaker_evening_icr = (mean_eve_icr > mean_day_icr + 0.4) if (day_ratios and eve_ratios) else False

    # Daily evening (18-24h) vs daytime (07-17h) TIR%
    eve_day_tirs: list[tuple[float, float, str]] = []
    for d_ord in all_ord_days:
        dv = g_idx.window_vals(d_ord * 1440 + 7 * 60, d_ord * 1440 + 16 * 60 + 59)
        ev = g_idx.window_vals(d_ord * 1440 + 18 * 60, d_ord * 1440 + 23 * 60 + 59)
        if len(dv) >= 4 and len(ev) >= 3:
            dtir = sum(1 for v in dv if 70.0 <= v <= 180.0) * 100.0 / len(dv)
            etir = sum(1 for v in ev if 70.0 <= v <= 180.0) * 100.0 / len(ev)
            eve_day_tirs.append((etir, dtir, date.fromordinal(d_ord).isoformat()))

    # Undisturbed 7AM fasting readings
    morning_7am_rows: list[tuple[float, str]] = []
    for d_ord in all_ord_days:
        g7 = g_idx.nearest_val(d_ord * 1440 + 7 * 60, max_tol_min=30)
        if g7 is None:
            continue
        blo = bisect_left(b_mins, d_ord * 1440 + 3 * 60)
        bhi = bisect_right(b_mins, d_ord * 1440 + 7 * 60 + 5)
        if blo == bhi:
            morning_7am_rows.append((g7, f"{date.fromordinal(d_ord).isoformat()} 07:00"))
    med_7am = statistics.median([x[0] for x in morning_7am_rows]) if morning_7am_rows else 0.0

    # Daily manual zero-carb bolus percentage across active bolus days
    bolus_by_day: dict[str, list[Any]] = defaultdict(list)
    for b in ds.boluses:
        bolus_by_day[ts_date_str(b.ts)].append(b)
    daily_zero_pct = [
        (sum(1 for b in blist if b.carbs_g <= 0.0) * 100.0 / len(blist), d_s)
        for d_s, blist in sorted(bolus_by_day.items())
        if len(blist) >= 1
    ]

    etir_list = [x[0] for x in eve_day_tirs]
    dtir_list = [x[1] for x in eve_day_tirs]
    me_tir, md_tir, de_tir, cil_et, cih_et = bootstrap_paired_diff(
        etir_list, dtir_list, "dosing_config_evening_trough"
    )
    eve_trough_sig = weaker_evening_icr and is_sig(
        len(etir_list), len(dtir_list), de_tir, cil_et, cih_et, min_abs_diff=5.0, expected_sign="neg"
    )

    z_a = [x[0] for x in daily_zero_pct]
    z_b = [100.0 - x[0] for x in daily_zero_pct]
    mz_a, mz_b, dz, cil_z, cih_z = bootstrap_paired_diff(z_a, z_b, "dosing_config_manual_bypass")
    manual_bypass_sig = (pct_zero_carb > 80.0) and is_sig(
        len(z_a), len(z_b), dz, cil_z, cih_z, min_abs_diff=40.0, expected_sign="pos"
    )

    has_pump_or_bolus = bool(ds.boluses or ds.daily_insulin or ds.basal_events)
    m7_vals = [x[0] for x in morning_7am_rows]
    m7_ref = [110.0 for _ in morning_7am_rows]
    m7_a, m7_b, d7, cil_7, cih_7 = bootstrap_paired_diff(m7_vals, m7_ref, "dosing_config_7am_settle")
    settle_7am_sig = has_pump_or_bolus and (med_7am >= 125.0) and is_sig(
        len(m7_vals), len(m7_ref), d7, cil_7, cih_7, min_abs_diff=12.0, expected_sign="pos"
    )

    if eve_trough_sig:
        d4_na, d4_nb, d4_ma, d4_mb, d4_diff, d4_cil, d4_cih, d4_unit, d4_sig = (
            len(etir_list), len(dtir_list), me_tir, md_tir, de_tir, cil_et, cih_et, "%", True
        )
        d4_lbl_a = f"Evening 18–24h (1:{mean_eve_icr:.0f}g)"
        d4_lbl_b = f"Daytime 07–17h (1:{mean_day_icr:.0f}g)"
        ex_d4 = [x[2] for x in sorted(eve_day_tirs, key=lambda r: r[0] - r[1])[:4]]
    elif manual_bypass_sig:
        d4_na, d4_nb, d4_ma, d4_mb, d4_diff, d4_cil, d4_cih, d4_unit, d4_sig = (
            len(z_a), len(z_b), mz_a, mz_b, dz, cil_z, cih_z, "%", True
        )
        d4_lbl_a = f"Manual 0g-Carb Boluses ({len(z_a)}d)"
        d4_lbl_b = f"Carb-Entry Calculator ({len(z_b)}d)"
        ex_d4 = [b.ts for b in ds.boluses if b.carbs_g <= 0.0][:4]
    elif settle_7am_sig:
        d4_na, d4_nb, d4_ma, d4_mb, d4_diff, d4_cil, d4_cih, d4_unit, d4_sig = (
            len(m7_vals), len(m7_ref), m7_a, m7_b, d7, cil_7, cih_7, "mg/dL", True
        )
        d4_lbl_a = f"7 AM Fasting Settle (n={len(m7_vals)})"
        d4_lbl_b = "Optimal 110 mg/dL Target"
        ex_d4 = [x[1] for x in sorted(morning_7am_rows, key=lambda r: r[0], reverse=True)[:4]]
    else:
        d4_na, d4_nb, d4_ma, d4_mb, d4_diff, d4_cil, d4_cih, d4_unit, d4_sig = (
            len(etir_list), len(dtir_list), me_tir, md_tir, de_tir, cil_et, cih_et, "%", False
        )
        d4_lbl_a = f"Evening 18–24h (n={len(etir_list)})"
        d4_lbl_b = f"Daytime 07–17h (n={len(dtir_list)})"
        ex_d4 = [x[2] for x in eve_day_tirs[:3]]

    results.append(
        StatisticalEvidence(
            domain="dosing",
            hypothesis_id="dosing_pump_config_bypass_review",
            title="Pump Configuration, Carb Ratio Schedule & Manual Bolus Bypass Audit",
            n_a=d4_na,
            n_b=d4_nb,
            mean_a=d4_ma,
            mean_b=d4_mb,
            diff=d4_diff,
            ci_95_low=d4_cil,
            ci_95_high=d4_cih,
            unit=d4_unit,
            statistically_significant=d4_sig,
            example_timestamps=ex_d4,
            raw_summary=(
                f"Pump audit: {pct_zero_carb:.1f}% zero-carb manual boluses ({total_boluses} total); "
                f"evening 18–24h TIR {me_tir:.1f}% (mean ICR 1:{mean_eve_icr:.1f}g) vs daytime 07–17h TIR "
                f"{md_tir:.1f}% (ICR 1:{mean_day_icr:.1f}g); undisturbed 7 AM fasting median {med_7am:.1f} mg/dL "
                f"(mean {m7_a:.1f} mg/dL across n={len(m7_vals)} mornings); primary Δ={d4_diff:+.1f} {d4_unit} "
                f"[95% CI {d4_cil:+.1f}, {d4_cih:+.1f}]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=d4_lbl_a,
                condition_b_label=d4_lbl_b,
                bars=[
                    make_bar("Zero-Carb Manual vs Calc Share", pct_zero_carb, max(0.0, 100.0 - pct_zero_carb), unit="%", higher_is_better=False),
                    make_bar("Evening vs Daytime TIR", me_tir, md_tir, unit="%", higher_is_better=True),
                    make_bar("7 AM Fasting vs 110 Target", med_7am, 110.0 if med_7am > 0 else 0.0, unit="mg/dL", higher_is_better=False),
                ],
            ),
        )
    )

    # (e) Stretched/Expired Pod Wear windows (if pod expiration alarms >= 5)
    raw_pod_alarms = [
        a
        for a in ds.alarms
        if a.category == "pod_expiration"
        or "pod expir" in a.label.lower()
        or "replace pod" in a.label.lower()
    ]
    parsed_pod_alarms = sorted(
        [(m, a) for a in raw_pod_alarms if (m := parse_ts_min(a.ts)) is not None],
        key=lambda x: x[0],
    )
    # Deduplicate same-hour repeated chimes while preserving >=5 distinct pod alarms if >=5 exist
    dedup_3h: list[tuple[int, Any]] = []
    for m, a in parsed_pod_alarms:
        if not dedup_3h or (m - dedup_3h[-1][0]) >= 180:
            dedup_3h.append((m, a))
    active_pod_alarms = dedup_3h if (len(dedup_3h) >= 5 or len(parsed_pod_alarms) < 5) else parsed_pod_alarms

    expired_windows: list[tuple[float, float, float, str]] = []  # (mean_cgm, tir, tar, ts)
    fresh_windows: list[tuple[float, float, float]] = []

    for m, a in active_pod_alarms:
        exp_vals = g_idx.window_vals(m - 120, m + 360)
        if len(exp_vals) >= 3:
            expired_windows.append(
                (
                    statistics.fmean(exp_vals),
                    sum(1 for v in exp_vals if 70.0 <= v <= 180.0) * 100.0 / len(exp_vals),
                    sum(1 for v in exp_vals if v > 180.0) * 100.0 / len(exp_vals),
                    a.ts,
                )
            )
        fresh_vals = g_idx.window_vals(m - 1560, m - 1080)  # 24h earlier at same circadian window
        if len(fresh_vals) >= 3:
            fresh_windows.append(
                (
                    statistics.fmean(fresh_vals),
                    sum(1 for v in fresh_vals if 70.0 <= v <= 180.0) * 100.0 / len(fresh_vals),
                    sum(1 for v in fresh_vals if v > 180.0) * 100.0 / len(fresh_vals),
                )
            )

    if len(fresh_windows) < 5 and len(g_idx) >= 20:
        alarm_mins = [m for m, _ in active_pod_alarms]
        for d_ord in all_ord_days:
            cand_m = d_ord * 1440 + 14 * 60
            if all(abs(cand_m - am) > 720 for am in alarm_mins):
                fv = g_idx.window_vals(cand_m - 120, cand_m + 360)
                if len(fv) >= 3:
                    fresh_windows.append(
                        (
                            statistics.fmean(fv),
                            sum(1 for v in fv if 70.0 <= v <= 180.0) * 100.0 / len(fv),
                            sum(1 for v in fv if v > 180.0) * 100.0 / len(fv),
                        )
                    )
            if len(fresh_windows) >= max(10, len(expired_windows)):
                break

    exp_cgm = [x[0] for x in expired_windows]
    fr_cgm = [x[0] for x in fresh_windows]
    me_p, mf_p, dp, cil_p, cih_p = bootstrap_independent_diff(
        exp_cgm, fr_cgm, "dosing_expired_pod_wear"
    )
    sig_d5 = (len(raw_pod_alarms) >= 5) and is_sig(
        len(exp_cgm), len(fr_cgm), dp, cil_p, cih_p, min_abs_diff=10.0, expected_sign="pos"
    )

    exp_tir = statistics.fmean(x[1] for x in expired_windows) if expired_windows else 0.0
    fr_tir = statistics.fmean(x[1] for x in fresh_windows) if fresh_windows else 0.0
    exp_tar = statistics.fmean(x[2] for x in expired_windows) if expired_windows else 0.0
    fr_tar = statistics.fmean(x[2] for x in fresh_windows) if fresh_windows else 0.0
    ex_d5 = [x[3] for x in sorted(expired_windows, key=lambda r: r[0], reverse=True)[:4]]

    results.append(
        StatisticalEvidence(
            domain="dosing",
            hypothesis_id="dosing_expired_pod_wear",
            title="Stretched / Expired Pod Wear Windows vs Fresh Mid-Cycle Pod Absorption",
            n_a=len(exp_cgm),
            n_b=len(fr_cgm),
            mean_a=me_p,
            mean_b=mf_p,
            diff=dp,
            ci_95_low=cil_p,
            ci_95_high=cih_p,
            unit="mg/dL",
            statistically_significant=sig_d5,
            example_timestamps=ex_d5,
            raw_summary=(
                f"Across {len(raw_pod_alarms)} pod expiration alarms ({len(exp_cgm)} stretched wear windows), "
                f"window glucose averaged {me_p:.1f} mg/dL ({exp_tir:.1f}% TIR, {exp_tar:.1f}% >180) vs "
                f"{mf_p:.1f} mg/dL ({fr_tir:.1f}% TIR, {fr_tar:.1f}% >180) during fresh mid-pod wear "
                f"(n={len(fr_cgm)}); Δ={dp:+.1f} mg/dL [95% CI {cil_p:+.1f}, {cih_p:+.1f}]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"Expired Pod Windows (n={len(exp_cgm)})",
                condition_b_label=f"Fresh Pod Windows (n={len(fr_cgm)})",
                bars=[
                    make_bar("Window Mean Glucose", me_p, mf_p, unit="mg/dL", higher_is_better=False),
                    make_bar("Window Time in Range", exp_tir, fr_tir, unit="%", higher_is_better=True),
                    make_bar("Readings >180 mg/dL", exp_tar, fr_tar, unit="%", higher_is_better=False),
                ],
            ),
        )
    )

    return results


# ============================================================================
# 4. VITALS & CIRCADIAN DOMAIN PROBES
# ============================================================================


def _build_daily_glycemic_map(g_idx: GlucoseIndex) -> dict[str, dict[str, float]]:
    by_date: dict[str, list[float]] = defaultdict(list)
    for pt, val in zip(g_idx.points, g_idx.vals):
        by_date[ts_date_str(pt.ts)].append(val)

    min_req = 12 if any(len(v) >= 12 for v in by_date.values()) else 3
    out: dict[str, dict[str, float]] = {}
    for d, vals in by_date.items():
        if len(vals) < min_req:
            continue
        cnt = len(vals)
        out[d] = {
            "mean_cgm": statistics.fmean(vals),
            "sd_cgm": statistics.stdev(vals) if cnt > 1 else 0.0,
            "tir_pct": sum(1 for v in vals if 70.0 <= v <= 180.0) * 100.0 / cnt,
            "tar_pct": sum(1 for v in vals if v > 180.0) * 100.0 / cnt,
            "tbr_pct": sum(1 for v in vals if v < 70.0) * 100.0 / cnt,
        }
    return out


def probe_vitals_patterns(ds: CanonicalDataset) -> list[StatisticalEvidence]:
    g_idx = GlucoseIndex(ds.glucose)
    b_mins, b_events = build_bolus_index(ds.boluses)
    daily_gly = _build_daily_glycemic_map(g_idx)
    insulin_by_date = {ins.date: ins for ins in ds.daily_insulin}
    sleep_by_wake: dict[str, SleepSessionSummary] = {}
    for s in ds.sleep_sessions:
        wd = s.wake_date or ts_date_str(s.end_ts)
        if wd:
            sleep_by_wake[wd] = s

    results: list[StatisticalEvidence] = []

    # (a) Workweek (Mon-Thu) vs Weekend (Fri-Sun) TIR% gap
    workweek_days: list[tuple[str, dict[str, float]]] = []
    weekend_days: list[tuple[str, dict[str, float]]] = []
    for d_str, gstats in sorted(daily_gly.items()):
        dt = parse_date_safe(d_str)
        if dt is None:
            continue
        if dt.weekday() <= 3:
            workweek_days.append((d_str, gstats))
        else:
            weekend_days.append((d_str, gstats))

    ww_tir = [x[1]["tir_pct"] for x in workweek_days]
    we_tir = [x[1]["tir_pct"] for x in weekend_days]
    ww_mean = statistics.fmean(ww_tir) if ww_tir else 0.0
    we_mean = statistics.fmean(we_tir) if we_tir else 0.0

    # Place lower-TIR (riskier) cohort as Condition A so PairedBarItem convention holds universally
    if we_mean <= ww_mean:
        va_days, vb_days = weekend_days, workweek_days
        va_lbl = f"Weekend Fri–Sun (n={len(weekend_days)}d)"
        vb_lbl = f"Workweek Mon–Thu (n={len(workweek_days)}d)"
    else:
        va_days, vb_days = workweek_days, weekend_days
        va_lbl = f"Workweek Mon–Thu (n={len(workweek_days)}d)"
        vb_lbl = f"Weekend Fri–Sun (n={len(weekend_days)}d)"

    tir_a = [x[1]["tir_pct"] for x in va_days]
    tir_b = [x[1]["tir_pct"] for x in vb_days]
    mv_1a, mv_1b, dv_1, cil_v1, cih_v1 = bootstrap_independent_diff(
        tir_a, tir_b, "vitals_workweek_vs_weekend"
    )
    sig_v1 = is_sig(len(tir_a), len(tir_b), dv_1, cil_v1, cih_v1, min_abs_diff=5.0, expected_sign="neg")

    cgm_va = statistics.fmean(x[1]["mean_cgm"] for x in va_days) if va_days else 0.0
    cgm_vb = statistics.fmean(x[1]["mean_cgm"] for x in vb_days) if vb_days else 0.0
    tar_va = statistics.fmean(x[1]["tar_pct"] for x in va_days) if va_days else 0.0
    tar_vb = statistics.fmean(x[1]["tar_pct"] for x in vb_days) if vb_days else 0.0
    ex_v1 = [x[0] for x in sorted(va_days, key=lambda r: r[1]["tir_pct"])[:4]]

    results.append(
        StatisticalEvidence(
            domain="vitals",
            hypothesis_id="vitals_workweek_vs_weekend_tir",
            title="Workweek (Mon–Thu) vs Weekend (Fri–Sun) Routine TIR% Divergence",
            n_a=len(tir_a),
            n_b=len(tir_b),
            mean_a=mv_1a,
            mean_b=mv_1b,
            diff=dv_1,
            ci_95_low=cil_v1,
            ci_95_high=cih_v1,
            unit="%",
            statistically_significant=sig_v1,
            example_timestamps=ex_v1,
            raw_summary=(
                f"{va_lbl} averaged {mv_1a:.1f}% TIR (mean glucose {cgm_va:.1f} mg/dL, {tar_va:.1f}% >180) "
                f"vs {mv_1b:.1f}% TIR on {vb_lbl} (mean {cgm_vb:.1f} mg/dL, {tar_vb:.1f}% >180); "
                f"TIR Δ={dv_1:+.1f}% [95% CI {cil_v1:+.1f}%, {cih_v1:+.1f}%]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=va_lbl,
                condition_b_label=vb_lbl,
                bars=[
                    make_bar("Daily Time in Range", mv_1a, mv_1b, unit="%", higher_is_better=True),
                    make_bar("Daily Mean Glucose", cgm_va, cgm_vb, unit="mg/dL", higher_is_better=False),
                    make_bar("Time >180 mg/dL (TAR)", tar_va, tar_vb, unit="%", higher_is_better=False),
                ],
            ),
        )
    )

    # (b) High Daily Step Strain (>11k steps) + Evening Bolus -> Midnight Hypoglycemia risk
    high_strain_nights: list[tuple[float, float, int, str]] = []  # (hypo_flag, nadir_cgm, steps, date)
    low_strain_nights: list[tuple[float, float, int, str]] = []
    has_any_boluses = len(ds.boluses) > 0

    for v in ds.daily_vitals:
        vd = parse_date_safe(v.date)
        if vd is None:
            continue
        d_ord = vd.toordinal()
        ov_vals = g_idx.window_vals(d_ord * 1440 + 23 * 60 + 30, (d_ord + 1) * 1440 + 6 * 60)
        if len(ov_vals) < 4:
            continue

        b_lo = bisect_left(b_mins, d_ord * 1440 + 17 * 60 + 30)
        b_hi = bisect_right(b_mins, d_ord * 1440 + 23 * 60 + 59)
        eve_bolus_u = sum(b_events[i].units for i in range(b_lo, b_hi))
        has_eve_bolus = (eve_bolus_u >= 0.4) if has_any_boluses else True

        nadir = min(ov_vals)
        next_d = (vd + timedelta(days=1)).isoformat()
        s_next = sleep_by_wake.get(next_d)
        is_hypo = nadir < 70.0 or (s_next is not None and (s_next.overnight_tbr70_pct or 0.0) > 0.0)
        hypo_pct_val = 100.0 if is_hypo else 0.0

        if v.steps > 11_000 and has_eve_bolus:
            high_strain_nights.append((hypo_pct_val, nadir, v.steps, v.date))
        elif v.steps <= 11_000:
            low_strain_nights.append((hypo_pct_val, nadir, v.steps, v.date))

    hs_hypo = [x[0] for x in high_strain_nights]
    ls_hypo = [x[0] for x in low_strain_nights]
    mv_2a, mv_2b, dv_2, cil_v2, cih_v2 = bootstrap_independent_diff(
        hs_hypo, ls_hypo, "vitals_high_steps_midnight_hypo"
    )
    sig_v2 = is_sig(len(hs_hypo), len(ls_hypo), dv_2, cil_v2, cih_v2, min_abs_diff=10.0, expected_sign="pos")

    hs_nadir = statistics.fmean(x[1] for x in high_strain_nights) if high_strain_nights else 0.0
    ls_nadir = statistics.fmean(x[1] for x in low_strain_nights) if low_strain_nights else 0.0
    hs_steps_k = statistics.fmean(x[2] for x in high_strain_nights) / 1000.0 if high_strain_nights else 0.0
    ls_steps_k = statistics.fmean(x[2] for x in low_strain_nights) / 1000.0 if low_strain_nights else 0.0
    ex_v2 = [x[3] for x in sorted(high_strain_nights, key=lambda r: r[1])[:4]]

    results.append(
        StatisticalEvidence(
            domain="vitals",
            hypothesis_id="vitals_high_steps_evening_bolus_hypo",
            title="High Daily Step Strain (>11k Steps) + Evening Bolus Midnight Hypoglycemia Risk",
            n_a=len(hs_hypo),
            n_b=len(ls_hypo),
            mean_a=mv_2a,
            mean_b=mv_2b,
            diff=dv_2,
            ci_95_low=cil_v2,
            ci_95_high=cih_v2,
            unit="%",
            statistically_significant=sig_v2,
            example_timestamps=ex_v2,
            raw_summary=(
                f"Days exceeding 11,000 steps paired with evening insulin (n={len(hs_hypo)}, mean "
                f"{hs_steps_k:.1f}k steps) led to midnight hypoglycemia (<70 mg/dL) on {mv_2a:.1f}% of nights "
                f"(overnight nadir {hs_nadir:.0f} mg/dL) vs {mv_2b:.1f}% on ≤11k-step days (n={len(ls_hypo)}, "
                f"nadir {ls_nadir:.0f} mg/dL); Δ={dv_2:+.1f}% [95% CI {cil_v2:+.1f}%, {cih_v2:+.1f}%]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f">11k Steps + Eve Bolus (n={len(hs_hypo)})",
                condition_b_label=f"≤11k Step Days (n={len(ls_hypo)})",
                bars=[
                    make_bar("Midnight Hypo Nights (<70)", mv_2a, mv_2b, unit="%", higher_is_better=False),
                    make_bar("Overnight Glucose Nadir", hs_nadir, ls_nadir, unit="mg/dL", higher_is_better=True),
                    make_bar("Mean Daily Steps", hs_steps_k, ls_steps_k, unit="k", higher_is_better=True),
                ],
            ),
        )
    )

    # (c) Elevated Overnight Wrist Temp + Respiratory Rate -> Next-Day Insulin Resistance (TDD + Mean Glucose)
    vitals_with_temp = [
        v for v in ds.daily_vitals if v.wrist_temp_c is not None and v.date in daily_gly
    ]
    temps_all = [float(v.wrist_temp_c) for v in vitals_with_temp if v.wrist_temp_c is not None]
    resps_all = [float(v.resp_rate_brpm) for v in vitals_with_temp if v.resp_rate_brpm is not None]

    med_temp = statistics.median(temps_all) if temps_all else 0.0
    med_resp = statistics.median(resps_all) if resps_all else 0.0
    has_resp_col = len(resps_all) >= 5

    def _split_temp_resp(t_delta: float, r_delta: float) -> tuple[list[Any], list[Any]]:
        elev: list[Any] = []
        norm: list[Any] = []
        for v in vitals_with_temp:
            tc = float(v.wrist_temp_c)  # type: ignore[arg-type]
            rr = float(v.resp_rate_brpm) if v.resp_rate_brpm is not None else med_resp
            is_elev = (tc >= med_temp + t_delta) and (not has_resp_col or rr >= med_resp + r_delta)
            is_norm = (tc <= med_temp + 0.12) and (not has_resp_col or rr <= med_resp + 0.45)
            if is_elev:
                elev.append(v)
            elif is_norm:
                norm.append(v)
        return elev, norm

    elev_v, norm_v = _split_temp_resp(0.25, 0.7)
    if len(elev_v) < 5:
        elev_v, norm_v = _split_temp_resp(0.18, 0.4)

    elev_cgm = [daily_gly[v.date]["mean_cgm"] for v in elev_v]
    norm_cgm = [daily_gly[v.date]["mean_cgm"] for v in norm_v]
    elev_tdd = [insulin_by_date[v.date].total_units for v in elev_v if v.date in insulin_by_date and insulin_by_date[v.date].total_units > 0]
    norm_tdd = [insulin_by_date[v.date].total_units for v in norm_v if v.date in insulin_by_date and insulin_by_date[v.date].total_units > 0]

    mc_g1, mc_g2, dc_g, cil_cg, cih_cg = bootstrap_independent_diff(
        elev_cgm, norm_cgm, "vitals_temp_resp_cgm"
    )
    mc_t1, mc_t2, dc_t, cil_ct, cih_ct = bootstrap_independent_diff(
        elev_tdd, norm_tdd, "vitals_temp_resp_tdd"
    )
    sig_cg = is_sig(len(elev_cgm), len(norm_cgm), dc_g, cil_cg, cih_cg, min_abs_diff=7.0, expected_sign="pos")
    sig_ct = is_sig(len(elev_tdd), len(norm_tdd), dc_t, cil_ct, cih_ct, min_abs_diff=2.0, expected_sign="pos")

    if sig_cg or not sig_ct:
        v3_na, v3_nb, v3_ma, v3_mb, v3_diff, v3_cil, v3_cih, v3_unit, v3_sig = (
            len(elev_cgm), len(norm_cgm), mc_g1, mc_g2, dc_g, cil_cg, cih_cg, "mg/dL", sig_cg
        )
    else:
        v3_na, v3_nb, v3_ma, v3_mb, v3_diff, v3_cil, v3_cih, v3_unit, v3_sig = (
            len(elev_tdd), len(norm_tdd), mc_t1, mc_t2, dc_t, cil_ct, cih_ct, "U", sig_ct
        )

    resp_elev_m = statistics.fmean(float(v.resp_rate_brpm) for v in elev_v if v.resp_rate_brpm is not None) if any(v.resp_rate_brpm is not None for v in elev_v) else 0.0
    resp_norm_m = statistics.fmean(float(v.resp_rate_brpm) for v in norm_v if v.resp_rate_brpm is not None) if any(v.resp_rate_brpm is not None for v in norm_v) else 0.0
    tar_elev_m = statistics.fmean(daily_gly[v.date]["tar_pct"] for v in elev_v) if elev_v else 0.0
    tar_norm_m = statistics.fmean(daily_gly[v.date]["tar_pct"] for v in norm_v) if norm_v else 0.0

    bars_v3 = [
        make_bar("Next-Day Mean Glucose", mc_g1, mc_g2, unit="mg/dL", higher_is_better=False),
    ]
    if elev_tdd and norm_tdd:
        bars_v3.append(make_bar("Total Daily Insulin (TDD)", mc_t1, mc_t2, unit="U", higher_is_better=False))
    else:
        bars_v3.append(make_bar("Time >180 mg/dL (TAR)", tar_elev_m, tar_norm_m, unit="%", higher_is_better=False))
    if resp_elev_m > 0 or resp_norm_m > 0:
        bars_v3.append(make_bar("Overnight Resp Rate", resp_elev_m, resp_norm_m, unit="brpm", higher_is_better=False, fixed_max=25.0))

    ex_v3 = [v.date for v in sorted(elev_v, key=lambda x: daily_gly[x.date]["mean_cgm"], reverse=True)[:4]]
    results.append(
        StatisticalEvidence(
            domain="vitals",
            hypothesis_id="vitals_wrist_temp_resp_resistance",
            title="Elevated Overnight Wrist Temp + Respiratory Rate Next-Day Insulin Resistance",
            n_a=v3_na,
            n_b=v3_nb,
            mean_a=v3_ma,
            mean_b=v3_mb,
            diff=v3_diff,
            ci_95_low=v3_cil,
            ci_95_high=v3_cih,
            unit=v3_unit,
            statistically_significant=v3_sig,
            example_timestamps=ex_v3,
            raw_summary=(
                f"Days preceded by elevated overnight wrist temperature + respiratory rate (n={len(elev_v)}) "
                f"averaged {mc_g1:.1f} mg/dL glucose and {mc_t1:.1f}U TDD vs {mc_g2:.1f} mg/dL and {mc_t2:.1f}U "
                f"TDD on baseline recovery nights (n={len(norm_v)}); primary Δ={v3_diff:+.1f} {v3_unit} "
                f"[95% CI {v3_cil:+.1f}, {v3_cih:+.1f}]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"Elevated Temp + Resp (n={len(elev_v)}d)",
                condition_b_label=f"Baseline Vitals (n={len(norm_v)}d)",
                bars=bars_v3,
            ),
        )
    )

    # (d) Smartwatch Blood Pressure Surge (systolic_bp >= 135 vs < 125 days -> Mean CGM, SD, Sleep Hours)
    bp_days = [
        v
        for v in ds.daily_vitals
        if v.systolic_bp is not None and v.systolic_bp > 0 and v.date in daily_gly
    ]
    bp_high = [v for v in bp_days if float(v.systolic_bp) >= 135.0]  # type: ignore[arg-type]
    bp_low_strict = [v for v in bp_days if float(v.systolic_bp) < 125.0]  # type: ignore[arg-type]
    bp_low = (
        bp_low_strict
        if len(bp_low_strict) >= 5
        else [v for v in bp_days if float(v.systolic_bp) < 130.0]  # type: ignore[arg-type]
    )

    bph_cgm = [daily_gly[v.date]["mean_cgm"] for v in bp_high]
    bpl_cgm = [daily_gly[v.date]["mean_cgm"] for v in bp_low]
    bph_sd = [daily_gly[v.date]["sd_cgm"] for v in bp_high]
    bpl_sd = [daily_gly[v.date]["sd_cgm"] for v in bp_low]

    mv_4a, mv_4b, dv_4, cil_v4, cih_v4 = bootstrap_independent_diff(
        bph_cgm, bpl_cgm, "vitals_bp_surge_mean_cgm"
    )
    msd_a, msd_b, d_sd, cil_sd, cih_sd = bootstrap_independent_diff(
        bph_sd, bpl_sd, "vitals_bp_surge_sd_cgm"
    )
    sig_bp_cgm = is_sig(len(bph_cgm), len(bpl_cgm), dv_4, cil_v4, cih_v4, min_abs_diff=7.0, expected_sign="pos")
    sig_bp_sd = is_sig(len(bph_sd), len(bpl_sd), d_sd, cil_sd, cih_sd, min_abs_diff=4.0, expected_sign="pos")

    bph_sleep = [
        sleep_by_wake[v.date].total_sleep_min / 60.0
        for v in bp_high
        if v.date in sleep_by_wake and sleep_by_wake[v.date].total_sleep_min > 0
    ]
    bpl_sleep = [
        sleep_by_wake[v.date].total_sleep_min / 60.0
        for v in bp_low
        if v.date in sleep_by_wake and sleep_by_wake[v.date].total_sleep_min > 0
    ]
    slp_bph_mean = statistics.fmean(bph_sleep) if bph_sleep else 0.0
    slp_bpl_mean = statistics.fmean(bpl_sleep) if bpl_sleep else 0.0
    mslp_a, mslp_b, d_slp, cil_slp, cih_slp = bootstrap_independent_diff(
        bph_sleep, bpl_sleep, "vitals_bp_surge_sleep"
    )
    sig_bp_sleep = is_sig(len(bph_sleep), len(bpl_sleep), d_slp, cil_slp, cih_slp, min_abs_diff=0.4, expected_sign="neg")
    sys_hi_mean = statistics.fmean(float(v.systolic_bp) for v in bp_high) if bp_high else 0.0  # type: ignore[arg-type]
    sys_lo_mean = statistics.fmean(float(v.systolic_bp) for v in bp_low) if bp_low else 0.0  # type: ignore[arg-type]

    if sig_bp_cgm or (not sig_bp_sd and not sig_bp_sleep):
        v4_na, v4_nb, v4_ma, v4_mb, v4_diff, v4_cil, v4_cih, v4_unit, v4_sig = (
            len(bph_cgm), len(bpl_cgm), mv_4a, mv_4b, dv_4, cil_v4, cih_v4, "mg/dL", sig_bp_cgm
        )
    elif sig_bp_sd:
        v4_na, v4_nb, v4_ma, v4_mb, v4_diff, v4_cil, v4_cih, v4_unit, v4_sig = (
            len(bph_sd), len(bpl_sd), msd_a, msd_b, d_sd, cil_sd, cih_sd, "mg/dL", sig_bp_sd
        )
    else:
        v4_na, v4_nb, v4_ma, v4_mb, v4_diff, v4_cil, v4_cih, v4_unit, v4_sig = (
            len(bph_sleep), len(bpl_sleep), mslp_a, mslp_b, d_slp, cil_slp, cih_slp, "hrs", sig_bp_sleep
        )

    bars_v4 = [
        make_bar("Same-Day Mean Glucose", mv_4a, mv_4b, unit="mg/dL", higher_is_better=False),
        make_bar("Glycemic Variability (SD)", msd_a, msd_b, unit="mg/dL", higher_is_better=False),
    ]
    if bph_sleep and bpl_sleep:
        bars_v4.append(
            make_bar("Prior-Night Sleep", slp_bph_mean, slp_bpl_mean, unit="hrs", higher_is_better=True, fixed_max=10.0)
        )
    else:
        bars_v4.append(
            make_bar("Systolic Blood Pressure", sys_hi_mean, sys_lo_mean, unit="mmHg", higher_is_better=False, fixed_max=180.0)
        )

    ex_v4 = [v.date for v in sorted(bp_high, key=lambda x: float(x.systolic_bp or 0.0), reverse=True)[:4]]
    results.append(
        StatisticalEvidence(
            domain="vitals",
            hypothesis_id="vitals_smartwatch_bp_surge",
            title="Smartwatch Blood Pressure Surge (≥135 vs <125 mmHg) Cardiometabolic Coupling",
            n_a=v4_na,
            n_b=v4_nb,
            mean_a=v4_ma,
            mean_b=v4_mb,
            diff=v4_diff,
            ci_95_low=v4_cil,
            ci_95_high=v4_cih,
            unit=v4_unit,
            statistically_significant=v4_sig,
            example_timestamps=ex_v4,
            raw_summary=(
                f"Days with smartwatch systolic BP ≥135 mmHg (n={len(bph_cgm)}, mean {sys_hi_mean:.0f} mmHg) "
                f"showed same-day mean glucose {mv_4a:.1f} mg/dL, glycemic SD {msd_a:.1f} mg/dL, and prior-night "
                f"sleep {slp_bph_mean:.2f}h vs {mv_4b:.1f} mg/dL mean CGM, {msd_b:.1f} mg/dL SD, and "
                f"{slp_bpl_mean:.2f}h sleep on normotensive <125 mmHg days (n={len(bpl_cgm)}, mean {sys_lo_mean:.0f} mmHg); "
                f"primary Δ={v4_diff:+.1f} {v4_unit} [95% CI {v4_cil:+.1f}, {v4_cih:+.1f}]."
            ),
            chart_spec=ChartSpec(
                chart_type="paired_bars",
                condition_a_label=f"Systolic BP ≥135 (n={len(bph_cgm)}d)",
                condition_b_label=f"Systolic BP <125 (n={len(bpl_cgm)}d)",
                bars=bars_v4,
            ),
        )
    )

    return results


# ============================================================================
# 5. AD-HOC CUSTOM COHORT SPLIT TOOL FOR GEMINI TOOL-CALLING
# ============================================================================


def query_custom_cohort_split(
    ds: CanonicalDataset,
    metric: str,
    split_feature: str,
    threshold: float,
) -> dict[str, Any]:
    """
    Evaluate an ad-hoc daily threshold (`steps`, `sleep_hours`, `bolus_units`,
    `systolic_bp`, `wrist_temp_c`) against daily `tir_pct` or `mean_cgm` using
    a 1000-iteration deterministic bootstrap CI.
    """
    norm_metric = metric.strip().lower()
    if norm_metric in ("tir", "tir_pct", "time_in_range"):
        target_metric = "tir_pct"
        unit = "%"
        min_eff = 4.0
    elif norm_metric in ("mean_cgm", "glucose", "mean_glucose", "cgm"):
        target_metric = "mean_cgm"
        unit = "mg/dL"
        min_eff = 5.0
    else:
        return {
            "error": f"Unsupported metric '{metric}'. Supported metrics: 'tir_pct', 'mean_cgm'.",
            "statistically_significant": False,
        }

    feat = split_feature.strip().lower()
    feat_aliases = {
        "steps": "steps",
        "daily_steps": "steps",
        "sleep": "sleep_hours",
        "sleep_hours": "sleep_hours",
        "bolus": "bolus_units",
        "bolus_units": "bolus_units",
        "bp": "systolic_bp",
        "systolic_bp": "systolic_bp",
        "temp": "wrist_temp_c",
        "skin_temp": "wrist_temp_c",
        "wrist_temp": "wrist_temp_c",
        "wrist_temp_c": "wrist_temp_c",
    }
    canonical_feat = feat_aliases.get(feat)
    if canonical_feat is None:
        return {
            "error": (
                f"Unsupported split_feature '{split_feature}'. Supported features: "
                "'steps', 'sleep_hours', 'bolus_units', 'systolic_bp', 'wrist_temp_c'."
            ),
            "statistically_significant": False,
        }

    g_idx = GlucoseIndex(ds.glucose)
    daily_gly = _build_daily_glycemic_map(g_idx)

    vitals_map = {v.date: v for v in ds.daily_vitals}
    insulin_map = {ins.date: ins for ins in ds.daily_insulin}
    bolus_sum_by_date: dict[str, float] = defaultdict(float)
    for b in ds.boluses:
        bolus_sum_by_date[ts_date_str(b.ts)] += b.units

    sleep_hrs_by_date: dict[str, float] = {}
    for s in ds.sleep_sessions:
        wd = s.wake_date or ts_date_str(s.end_ts)
        if wd and s.total_sleep_min > 0:
            sleep_hrs_by_date[wd] = s.total_sleep_min / 60.0

    high_vals: list[float] = []
    low_vals: list[float] = []
    high_dates: list[str] = []
    low_dates: list[str] = []

    for d_str, gstats in sorted(daily_gly.items()):
        f_val: Optional[float] = None
        if canonical_feat == "steps":
            vit = vitals_map.get(d_str)
            if vit is not None:
                f_val = float(vit.steps)
        elif canonical_feat == "sleep_hours":
            f_val = sleep_hrs_by_date.get(d_str)
        elif canonical_feat == "bolus_units":
            if d_str in insulin_map:
                f_val = float(insulin_map[d_str].bolus_units)
            elif d_str in bolus_sum_by_date:
                f_val = float(bolus_sum_by_date[d_str])
        elif canonical_feat == "systolic_bp":
            vit = vitals_map.get(d_str)
            if vit is not None and vit.systolic_bp is not None and vit.systolic_bp > 0:
                f_val = float(vit.systolic_bp)
        elif canonical_feat == "wrist_temp_c":
            vit = vitals_map.get(d_str)
            if vit is not None and vit.wrist_temp_c is not None:
                f_val = float(vit.wrist_temp_c)

        if f_val is None:
            continue

        y_val = gstats[target_metric]
        if f_val >= threshold:
            high_vals.append(y_val)
            high_dates.append(d_str)
        else:
            low_vals.append(y_val)
            low_dates.append(d_str)

    ma, mb, diff, cil, cih = bootstrap_independent_diff(
        high_vals,
        low_vals,
        f"custom_{canonical_feat}_{threshold}_{target_metric}",
    )
    sig = is_sig(len(high_vals), len(low_vals), diff, cil, cih, min_abs_diff=min_eff)

    summary = (
        f"Days with {canonical_feat} >= {threshold} (n={len(high_vals)}) averaged {ma:.1f} {unit} {target_metric} "
        f"vs {mb:.1f} {unit} when {canonical_feat} < {threshold} (n={len(low_vals)}); "
        f"Δ={diff:+.1f} {unit} [95% CI {cil:+.1f}, {cih:+.1f}], significant={sig}."
    )
    return {
        "split_feature": canonical_feat,
        "threshold": float(threshold),
        "metric": target_metric,
        "unit": unit,
        "condition_a_label": f"{canonical_feat} >= {threshold}",
        "condition_b_label": f"{canonical_feat} < {threshold}",
        "n_a": len(high_vals),
        "n_b": len(low_vals),
        "mean_a": ma,
        "mean_b": mb,
        "n_high": len(high_vals),
        "n_low": len(low_vals),
        "mean_high": ma,
        "mean_low": mb,
        "diff": diff,
        "ci_95_low": cil,
        "ci_95_high": cih,
        "statistically_significant": sig,
        "example_dates_high": high_dates[:4],
        "example_dates_low": low_dates[:4],
        "summary": summary,
    }
