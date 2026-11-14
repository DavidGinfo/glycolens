from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime
import math
import statistics
from typing import Any, Optional

from glycolens.schemas import (
    BolusEvent,
    CanonicalDataset,
    DataProfile,
    DayOfWeekStat,
    DayReplay,
    GlucosePoint,
    HourlyBucket,
    WalkModelWeights,
    WalkSimulationRequest,
    WalkSimulationResponse,
    WorkoutSession,
)

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse_date_safe(d_str: str) -> Optional[date]:
    if not d_str or len(d_str) < 10:
        return None
    try:
        return date(int(d_str[0:4]), int(d_str[5:7]), int(d_str[8:10]))
    except ValueError:
        try:
            return datetime.fromisoformat(d_str).date()
        except ValueError:
            return None


def parse_ts_min(ts: str) -> Optional[int]:
    """Convert local wall-clock timestamp 'YYYY-MM-DD HH:MM' to monotonic ordinal minutes."""
    if not ts or len(ts) < 16:
        return None
    try:
        y = int(ts[0:4])
        m = int(ts[5:7])
        d = int(ts[8:10])
        hh = int(ts[11:13])
        mm = int(ts[14:16])
        return date(y, m, d).toordinal() * 1440 + hh * 60 + mm
    except ValueError:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", ""))
            return dt.date().toordinal() * 1440 + dt.hour * 60 + dt.minute
        except ValueError:
            return None


def ts_date_str(ts: str) -> str:
    return ts[:10] if len(ts) >= 10 else ""


def ts_hour_int(ts: str) -> int:
    if len(ts) >= 13:
        try:
            return int(ts[11:13])
        except ValueError:
            pass
    dt_min = parse_ts_min(ts)
    return (dt_min % 1440) // 60 if dt_min is not None else 0


def ts_hour_float(ts: str) -> float:
    if len(ts) >= 16:
        try:
            return int(ts[11:13]) + int(ts[14:16]) / 60.0
        except ValueError:
            pass
    dt_min = parse_ts_min(ts)
    return (dt_min % 1440) / 60.0 if dt_min is not None else 0.0


def percentile_sorted(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile on an already-sorted non-empty float list (q in 0..100)."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_vals[0]
    pos = (q / 100.0) * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


class GlucoseIndex:
    """Fast bisect-backed wall-clock index over CGM readings."""

    def __init__(self, points: list[GlucosePoint]) -> None:
        parsed: list[tuple[int, float, GlucosePoint]] = []
        for p in points:
            m = parse_ts_min(p.ts)
            if m is not None and math.isfinite(p.mg_dl):
                parsed.append((m, float(p.mg_dl), p))
        parsed.sort(key=lambda x: x[0])
        self.mins: list[int] = [x[0] for x in parsed]
        self.vals: list[float] = [x[1] for x in parsed]
        self.points: list[GlucosePoint] = [x[2] for x in parsed]

    def __len__(self) -> int:
        return len(self.vals)

    def window_vals(self, start_min: int, end_min: int) -> list[float]:
        lo = bisect_left(self.mins, start_min)
        hi = bisect_right(self.mins, end_min)
        return self.vals[lo:hi]

    def window_pairs(self, start_min: int, end_min: int) -> list[tuple[int, float]]:
        lo = bisect_left(self.mins, start_min)
        hi = bisect_right(self.mins, end_min)
        return list(zip(self.mins[lo:hi], self.vals[lo:hi]))

    def nearest_val(self, target_min: int, max_tol_min: int = 15) -> Optional[float]:
        pair = self.nearest_pair(target_min, max_tol_min=max_tol_min)
        return pair[1] if pair is not None else None

    def nearest_pair(self, target_min: int, max_tol_min: int = 15) -> Optional[tuple[int, float]]:
        n = len(self.mins)
        if n == 0:
            return None
        idx = bisect_left(self.mins, target_min)
        best_idx = -1
        best_dist = max_tol_min + 1
        for cand in (idx - 1, idx):
            if 0 <= cand < n:
                dist = abs(self.mins[cand] - target_min)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = cand
        if best_idx >= 0 and best_dist <= max_tol_min:
            return (self.mins[best_idx], self.vals[best_idx])
        return None

    def nearest_point(self, target_min: int, max_tol_min: int = 15) -> Optional[GlucosePoint]:
        n = len(self.mins)
        if n == 0:
            return None
        idx = bisect_left(self.mins, target_min)
        best_idx = -1
        best_dist = max_tol_min + 1
        for cand in (idx - 1, idx):
            if 0 <= cand < n:
                dist = abs(self.mins[cand] - target_min)
                if dist < best_dist:
                    best_dist = dist
                    best_idx = cand
        if best_idx >= 0 and best_dist <= max_tol_min:
            return self.points[best_idx]
        return None


def build_bolus_index(boluses: list[BolusEvent]) -> tuple[list[int], list[BolusEvent]]:
    parsed: list[tuple[int, BolusEvent]] = []
    for b in boluses:
        m = parse_ts_min(b.ts)
        if m is not None:
            parsed.append((m, b))
    parsed.sort(key=lambda x: x[0])
    return [x[0] for x in parsed], [x[1] for x in parsed]


def estimate_bolus_iob(target_min: int, b_mins: list[int], b_events: list[BolusEvent], dia_min: int = 240) -> float:
    """Estimate active bolus IOB via standard smooth 4-hour insulin decay curve."""
    if not b_mins:
        return 0.0
    lo = bisect_left(b_mins, target_min - dia_min)
    hi = bisect_right(b_mins, target_min)
    iob = 0.0
    for idx in range(lo, hi):
        dt = target_min - b_mins[idx]
        if 0 <= dt < dia_min and b_events[idx].units > 0:
            rem = 1.0 - (dt / float(dia_min))
            # Smooth cubic S-curve active insulin decay
            frac = rem * rem * (3.0 - 2.0 * rem)
            iob += b_events[idx].units * frac
    return round(iob, 2)


def enrich_workout_metrics(
    w: WorkoutSession,
    g_idx: GlucoseIndex,
    b_mins: list[int],
    b_events: list[BolusEvent],
    trust_zero_iob: bool,
) -> dict[str, Any]:
    """Return resolved workout metrics without mutating frozen input models unexpectedly."""
    s_min = parse_ts_min(w.start_ts)
    e_min = parse_ts_min(w.end_ts)
    if e_min is None and s_min is not None:
        e_min = s_min + int(round(max(w.duration_min, 0.0)))

    start_cgm = w.start_cgm
    if start_cgm is None and s_min is not None:
        start_cgm = g_idx.nearest_val(s_min, max_tol_min=15)

    end_cgm = w.end_cgm
    if end_cgm is None and e_min is not None:
        end_cgm = g_idx.nearest_val(e_min, max_tol_min=15)

    delta_cgm = w.delta_cgm
    if delta_cgm is None and start_cgm is not None and end_cgm is not None:
        delta_cgm = end_cgm - start_cgm

    nadir_90m = w.nadir_90m_cgm
    if nadir_90m is None and s_min is not None and e_min is not None:
        win = g_idx.window_vals(s_min, e_min + 90)
        if win:
            nadir_90m = min(win)
        elif end_cgm is not None:
            nadir_90m = min(start_cgm, end_cgm) if start_cgm is not None else end_cgm

    iob = w.iob_at_start
    if iob == 0.0 and not trust_zero_iob and s_min is not None:
        pt = g_idx.nearest_point(s_min, max_tol_min=10)
        if pt is not None and pt.iob_units > 0:
            iob = pt.iob_units
        else:
            iob = estimate_bolus_iob(s_min, b_mins, b_events)

    steps = w.steps
    if steps <= 0 and "walk" in w.activity_type.lower() and w.duration_min > 0:
        steps = int(round(w.duration_min * (w.cadence if w.cadence > 40 else 95.0)))

    return {
        "workout": w,
        "start_min": s_min,
        "end_min": e_min,
        "start_cgm": start_cgm,
        "end_cgm": end_cgm,
        "delta_cgm": delta_cgm,
        "nadir_90m_cgm": nadir_90m,
        "iob_at_start": float(iob),
        "steps": int(steps),
    }


def _solve_linear_system(mat: list[list[float]], rhs: list[float]) -> list[float]:
    """Solve KxK linear system via Gaussian elimination with partial pivoting."""
    k = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(mat)]
    for col in range(k):
        pivot_row = max(range(col, k), key=lambda r: abs(a[r][col]))
        if abs(a[pivot_row][col]) < 1e-12:
            continue
        if pivot_row != col:
            a[col], a[pivot_row] = a[pivot_row], a[col]
        piv = a[col][col]
        for j in range(col, k + 1):
            a[col][j] /= piv
        for r in range(k):
            if r != col and abs(a[r][col]) > 1e-15:
                factor = a[r][col]
                for j in range(col, k + 1):
                    a[r][j] -= factor * a[col][j]
    return [a[i][k] if abs(a[i][i]) > 1e-12 else 0.0 for i in range(k)]


def _fit_ridge_4feat(
    samples: list[tuple[float, float, float, float]],
    alpha: float = 1.5,
) -> tuple[dict[str, float], list[float]]:
    """
    Fit standardized 4-feature Ridge regression:
      y = bias + w_g0*z_g0 + w_iob*z_iob + w_steps*z_steps + w_inter*z_steps_x_iob
    where z_steps_x_iob = (steps * iob - mean_steps * mean_iob) / (sd_steps * sd_iob),
    preserving physical monotonicity across the entire non-negative (steps, iob) domain.
    Strictly computes feature standardization parameters ONLY on `samples`.
    """
    g0_list = [s[0] for s in samples]
    iob_list = [s[1] for s in samples]
    steps_list = [s[2] for s in samples]
    y_list = [s[3] for s in samples]

    mean_g0 = statistics.fmean(g0_list)
    sd_g0 = statistics.stdev(g0_list) if len(g0_list) > 1 else 1.0
    if sd_g0 < 1e-4:
        sd_g0 = 1.0

    mean_iob = statistics.fmean(iob_list)
    sd_iob = statistics.stdev(iob_list) if len(iob_list) > 1 else 1.0
    if sd_iob < 1e-4:
        sd_iob = 1.0

    mean_steps = statistics.fmean(steps_list)
    sd_steps = statistics.stdev(steps_list) if len(steps_list) > 1 else 1.0
    if sd_steps < 1e-4:
        sd_steps = 1.0

    inter_center = mean_steps * mean_iob
    inter_scale = max(sd_steps * sd_iob, 1e-4)

    xtx = [[0.0] * 5 for _ in range(5)]
    xty = [0.0] * 5

    for g0, iob, steps, y in samples:
        zg = (g0 - mean_g0) / sd_g0
        zi = (iob - mean_iob) / sd_iob
        zs = (steps - mean_steps) / sd_steps
        z_inter = (steps * iob - inter_center) / inter_scale
        row = [1.0, zg, zi, zs, z_inter]
        for i in range(5):
            xty[i] += row[i] * y
            for j in range(5):
                xtx[i][j] += row[i] * row[j]

    # L2 ridge regularization on standardized feature weights (intercept unpenalized)
    for idx in range(1, 5):
        xtx[idx][idx] += alpha

    weights = _solve_linear_system(xtx, xty)
    stats_dict = {
        "mean_g0": mean_g0,
        "sd_g0": sd_g0,
        "mean_iob": mean_iob,
        "sd_iob": sd_iob,
        "mean_steps": mean_steps,
        "sd_steps": sd_steps,
    }
    return stats_dict, weights


def _predict_ridge_4feat(
    g0: float,
    iob: float,
    steps: float,
    stats_dict: dict[str, float],
    weights: list[float],
) -> float:
    zg = (g0 - stats_dict["mean_g0"]) / max(stats_dict["sd_g0"], 1e-6)
    zi = (iob - stats_dict["mean_iob"]) / max(stats_dict["sd_iob"], 1e-6)
    zs = (steps - stats_dict["mean_steps"]) / max(stats_dict["sd_steps"], 1e-6)
    inter_center = stats_dict["mean_steps"] * stats_dict["mean_iob"]
    inter_scale = max(stats_dict["sd_steps"] * stats_dict["sd_iob"], 1e-6)
    z_inter = (steps * iob - inter_center) / inter_scale
    return (
        weights[0]
        + weights[1] * zg
        + weights[2] * zi
        + weights[3] * zs
        + weights[4] * z_inter
    )


def _pearson_r(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = sum((x - mx) ** 2 for x in xs)
    den_y = sum((y - my) ** 2 for y in ys)
    den = math.sqrt(den_x * den_y)
    if den < 1e-9:
        return 0.0
    return max(-1.0, min(1.0, num / den))


def fit_walk_model(
    ds: CanonicalDataset,
    g_idx: Optional[GlucoseIndex] = None,
) -> Optional[WalkModelWeights]:
    """
    Fit 4-feature standardized Ridge regression model for Walk Rx:
    Features: StartCGM, IOB, Steps, Steps*IOB (standardized)
    Validation: Chronological 75/25 holdout split with zero leakage (scaler fit on train only).
    """
    if g_idx is None:
        g_idx = GlucoseIndex(ds.glucose)
    b_mins, b_events = build_bolus_index(ds.boluses)
    trust_zero_iob = any(w.iob_at_start > 0 for w in ds.workouts)

    enriched = [
        enrich_workout_metrics(w, g_idx, b_mins, b_events, trust_zero_iob)
        for w in ds.workouts
    ]
    walk_rows = [
        r
        for r in enriched
        if (
            "walk" in r["workout"].activity_type.lower()
            or "hike" in r["workout"].activity_type.lower()
        )
        and r["start_cgm"] is not None
        and r["delta_cgm"] is not None
        and r["steps"] > 0
        and r["start_min"] is not None
    ]
    if len(walk_rows) < 6:
        # Fallback to any aerobic low-HR step-bearing session if explicit 'Walking' labels are sparse
        walk_rows = [
            r
            for r in enriched
            if (r["workout"].mean_hr == 0 or r["workout"].mean_hr < 150)
            and r["start_cgm"] is not None
            and r["delta_cgm"] is not None
            and r["steps"] >= 300
            and r["start_min"] is not None
        ]

    if len(walk_rows) < 6:
        return None

    walk_rows.sort(key=lambda r: r["start_min"])
    samples: list[tuple[float, float, float, float]] = [
        (
            float(r["start_cgm"]),
            float(r["iob_at_start"]),
            float(r["steps"]),
            float(r["delta_cgm"]),
        )
        for r in walk_rows
    ]

    n = len(samples)
    split_idx = max(4, min(n - 2, int(round(n * 0.75))))
    train_samples = samples[:split_idx]
    holdout_samples = samples[split_idx:]

    # Strict featurization ordering: fit standardization & weights ONLY on chronological train 75%
    train_stats, train_weights = _fit_ridge_4feat(train_samples, alpha=1.0)
    y_true = [s[3] for s in holdout_samples]
    y_pred = [
        _predict_ridge_4feat(s[0], s[1], s[2], train_stats, train_weights)
        for s in holdout_samples
    ]
    mae = statistics.fmean(abs(yt - yp) for yt, yp in zip(y_true, y_pred)) if y_true else 0.0
    holdout_r = _pearson_r(y_true, y_pred) if len(y_true) >= 2 else 0.0

    # Refit final model weights on full dataset for deployment predictions
    full_stats, full_weights = _fit_ridge_4feat(samples, alpha=1.0)

    return WalkModelWeights(
        bias=round(full_weights[0], 3),
        w_start_cgm=round(full_weights[1], 3),
        w_iob=round(full_weights[2], 3),
        w_steps=round(full_weights[3], 3),
        w_steps_x_iob=round(full_weights[4], 3),
        mean_g0=round(full_stats["mean_g0"], 2),
        sd_g0=round(full_stats["sd_g0"], 2),
        mean_iob=round(full_stats["mean_iob"], 3),
        sd_iob=round(full_stats["sd_iob"], 3),
        mean_steps=round(full_stats["mean_steps"], 1),
        sd_steps=round(full_stats["sd_steps"], 1),
        holdout_r=round(holdout_r, 3),
        mae_mg_dl=round(mae, 1),
        sample_count=n,
    )


def _format_ratio(val: float) -> str:
    rounded = round(val, 1)
    if abs(rounded - round(rounded)) < 0.05:
        return f"1:{int(round(rounded))}g"
    return f"1:{rounded:.1f}g"


def _compute_programmed_ratios_by_hour(boluses: list[BolusEvent]) -> dict[str, str]:
    by_hour: dict[int, list[float]] = defaultdict(list)
    for b in boluses:
        if b.carb_ratio and b.carb_ratio > 0:
            by_hour[ts_hour_int(b.ts)].append(round(float(b.carb_ratio), 1))

    if not by_hour:
        return {}

    hourly_mode: dict[int, float] = {}
    for h, vals in by_hour.items():
        counts = Counter(vals)
        max_cnt = max(counts.values())
        candidates = [v for v, c in counts.items() if c == max_cnt]
        hourly_mode[h] = statistics.median(candidates)

    # Reconstruct 24h pump schedule via forward-fill from most recent active setpoint
    sorted_known = sorted(hourly_mode.keys())
    last_val = hourly_mode[sorted_known[-1]]
    full_24: list[float] = []
    for h in range(24):
        if h in hourly_mode:
            last_val = hourly_mode[h]
        full_24.append(last_val)

    # Collapse contiguous identical schedule blocks for clean readability
    blocks: dict[str, str] = {}
    seg_start = 0
    for h in range(1, 25):
        if h == 24 or abs(full_24[h] - full_24[seg_start]) >= 0.1:
            end_h = h - 1
            key = f"{seg_start:02d}:00-{end_h:02d}:59"
            blocks[key] = _format_ratio(full_24[seg_start])
            seg_start = h
    return blocks


def _compute_undisturbed_7am_median(
    g_idx: GlucoseIndex,
    b_mins: list[int],
) -> Optional[float]:
    if len(g_idx) == 0:
        return None

    date_ordinals = sorted({m // 1440 for m in g_idx.mins})
    undisturbed_vals: list[float] = []
    all_7am_vals: list[float] = []

    for d_ord in date_ordinals:
        target_7am = d_ord * 1440 + 7 * 60
        g_7am = g_idx.nearest_val(target_7am, max_tol_min=30)
        if g_7am is None:
            continue
        all_7am_vals.append(g_7am)
        # Check if any bolus occurred between 03:00 and 07:05 on this morning
        b_lo = bisect_left(b_mins, d_ord * 1440 + 3 * 60)
        b_hi = bisect_right(b_mins, d_ord * 1440 + 7 * 60 + 5)
        if b_lo == b_hi:
            undisturbed_vals.append(g_7am)

    if undisturbed_vals:
        return round(statistics.median(undisturbed_vals), 1)
    if all_7am_vals:
        return round(statistics.median(all_7am_vals), 1)
    return None


def build_data_profile(ds: CanonicalDataset) -> DataProfile:
    """Compute comprehensive deterministic glycemic, insulin, vitals, AGP, and Walk Rx profile."""
    g_idx = GlucoseIndex(ds.glucose)
    b_mins, _ = build_bolus_index(ds.boluses)

    n_cgm = len(g_idx)
    if n_cgm > 0:
        vals = g_idx.vals
        raw_mean = statistics.fmean(vals)
        mean_cgm = round(raw_mean, 1)
        median_cgm = round(statistics.median(vals), 1)
        raw_sd = statistics.stdev(vals) if n_cgm > 1 else 0.0
        sd_cgm = round(raw_sd, 1)
        cv_pct = round((raw_sd / raw_mean) * 100.0, 1) if raw_mean > 0 else 0.0
        gmi_pct = round(3.31 + 0.02392 * raw_mean, 2)
        tir_pct = round(sum(1 for v in vals if 70.0 <= v <= 180.0) * 100.0 / n_cgm, 1)
        tbr_pct = round(sum(1 for v in vals if v < 70.0) * 100.0 / n_cgm, 1)
        tar_pct = round(sum(1 for v in vals if v > 180.0) * 100.0 / n_cgm, 1)
        very_high_pct = round(sum(1 for v in vals if v > 250.0) * 100.0 / n_cgm, 1)
        span_days = (g_idx.mins[-1] - g_idx.mins[0]) / 1440.0
        unique_cgm_days = len({m // 1440 for m in g_idx.mins})
        days_covered = round(max(span_days, unique_cgm_days * 0.9, 0.1), 1)
    else:
        mean_cgm = median_cgm = sd_cgm = cv_pct = gmi_pct = 0.0
        tir_pct = tbr_pct = tar_pct = very_high_pct = 0.0
        d1 = parse_date_safe(ds.start_date)
        d2 = parse_date_safe(ds.end_date)
        days_covered = float((d2 - d1).days + 1) if (d1 and d2 and d2 >= d1) else 0.0

    start_date = ds.start_date
    end_date = ds.end_date
    if (not start_date or not end_date) and n_cgm > 0:
        start_date = start_date or ts_date_str(g_idx.points[0].ts)
        end_date = end_date or ts_date_str(g_idx.points[-1].ts)

    bolus_count = len(ds.boluses)
    pct_zero_carb = (
        round(sum(1 for b in ds.boluses if b.carbs_g <= 0.0) * 100.0 / bolus_count, 1)
        if bolus_count > 0
        else 0.0
    )
    temp_target_count = sum(
        1 for ev in ds.basal_events if "temp" in ev.event_type.lower()
    )

    valid_tdd = [d.total_units for d in ds.daily_insulin if d.total_units > 0]
    valid_basal = [d.basal_units for d in ds.daily_insulin if d.basal_units > 0 or d.total_units > 0]
    mean_tdd_units = round(statistics.fmean(valid_tdd), 1) if valid_tdd else 0.0
    mean_basal_units = round(statistics.fmean(valid_basal), 1) if valid_basal else 0.0

    if mean_tdd_units == 0.0 and bolus_count > 0 and days_covered > 0:
        mean_tdd_units = round(sum(b.units for b in ds.boluses) / max(days_covered, 1.0), 1)

    bp_count = sum(
        1 for v in ds.daily_vitals if v.systolic_bp is not None and v.systolic_bp > 0
    )
    has_skin_temp = any(v.wrist_temp_c is not None for v in ds.daily_vitals)
    has_resp_rate = any(v.resp_rate_brpm is not None for v in ds.daily_vitals)

    # 24-hour AGP buckets
    hour_vals: dict[int, list[float]] = defaultdict(list)
    for m, v in zip(g_idx.mins, g_idx.vals):
        hour_vals[(m % 1440) // 60].append(v)

    hourly_agp: list[HourlyBucket] = []
    for hr in range(24):
        hv = sorted(hour_vals.get(hr, []))
        if hv:
            cnt = len(hv)
            hourly_agp.append(
                HourlyBucket(
                    hour=hr,
                    median_cgm=round(percentile_sorted(hv, 50.0), 1),
                    p25=round(percentile_sorted(hv, 25.0), 1),
                    p75=round(percentile_sorted(hv, 75.0), 1),
                    p10=round(percentile_sorted(hv, 10.0), 1),
                    p90=round(percentile_sorted(hv, 90.0), 1),
                    tir_pct=round(sum(1 for x in hv if 70.0 <= x <= 180.0) * 100.0 / cnt, 1),
                    low_pct=round(sum(1 for x in hv if x < 70.0) * 100.0 / cnt, 1),
                    very_high_pct=round(sum(1 for x in hv if x > 250.0) * 100.0 / cnt, 1),
                )
            )
        else:
            hourly_agp.append(
                HourlyBucket(
                    hour=hr,
                    median_cgm=median_cgm,
                    p25=median_cgm,
                    p75=median_cgm,
                    p10=median_cgm,
                    p90=median_cgm,
                    tir_pct=tir_pct,
                    low_pct=tbr_pct,
                    very_high_pct=very_high_pct,
                )
            )

    # Day-of-week statistics (Mon..Sun)
    dow_cgm: dict[int, list[float]] = defaultdict(list)
    for m, v in zip(g_idx.mins, g_idx.vals):
        dt = date.fromordinal(m // 1440)
        dow_cgm[dt.weekday()].append(v)

    dow_sleep: dict[int, list[float]] = defaultdict(list)
    for s in ds.sleep_sessions:
        sd = parse_date_safe(s.wake_date) or parse_date_safe(ts_date_str(s.end_ts))
        if sd is not None and s.total_sleep_min > 0:
            dow_sleep[sd.weekday()].append(s.total_sleep_min / 60.0)

    dow_steps: dict[int, list[int]] = defaultdict(list)
    for v in ds.daily_vitals:
        vd = parse_date_safe(v.date)
        if vd is not None and v.steps >= 0:
            dow_steps[vd.weekday()].append(v.steps)

    dow_basal: dict[int, list[float]] = defaultdict(list)
    for ins in ds.daily_insulin:
        id_dt = parse_date_safe(ins.date)
        if id_dt is not None and (ins.basal_units > 0 or ins.total_units > 0):
            dow_basal[id_dt.weekday()].append(ins.basal_units)

    day_of_week: list[DayOfWeekStat] = []
    for wd in range(7):
        gv = dow_cgm.get(wd, [])
        sv = dow_sleep.get(wd, [])
        stv = dow_steps.get(wd, [])
        bv = dow_basal.get(wd, [])
        day_of_week.append(
            DayOfWeekStat(
                day=WEEKDAY_NAMES[wd],
                tir_pct=round(sum(1 for x in gv if 70.0 <= x <= 180.0) * 100.0 / len(gv), 1) if gv else 0.0,
                mean_cgm=round(statistics.fmean(gv), 1) if gv else 0.0,
                sleep_hours=round(statistics.fmean(sv), 2) if sv else 0.0,
                steps=int(round(statistics.fmean(stv))) if stv else 0,
                basal_units=round(statistics.fmean(bv), 2) if bv else 0.0,
            )
        )

    programmed_ratios = _compute_programmed_ratios_by_hour(ds.boluses)
    undisturbed_7am = _compute_undisturbed_7am_median(g_idx, b_mins)

    walk_model = fit_walk_model(ds, g_idx=g_idx)
    ds.walk_model = walk_model

    return DataProfile(
        dataset_id=ds.dataset_id,
        title=ds.title,
        description=ds.description,
        source_tags=list(ds.source_tags),
        start_date=start_date,
        end_date=end_date,
        days_covered=days_covered,
        cgm_count=n_cgm,
        mean_cgm=mean_cgm,
        median_cgm=median_cgm,
        sd_cgm=sd_cgm,
        cv_pct=cv_pct,
        gmi_pct=gmi_pct,
        tir_pct=tir_pct,
        tbr_pct=tbr_pct,
        tar_pct=tar_pct,
        very_high_pct=very_high_pct,
        bolus_count=bolus_count,
        pct_zero_carb_boluses=pct_zero_carb,
        temp_target_count=temp_target_count,
        mean_tdd_units=mean_tdd_units,
        mean_basal_units=mean_basal_units,
        workout_count=len(ds.workouts),
        sleep_count=len(ds.sleep_sessions),
        bp_count=bp_count,
        has_skin_temp=has_skin_temp,
        has_resp_rate=has_resp_rate,
        hourly_agp=hourly_agp,
        day_of_week=day_of_week,
        programmed_ratios_by_hour=programmed_ratios,
        undisturbed_7am_median=undisturbed_7am,
        walk_model=walk_model,
    )


def _downsample_day_cgm(pairs: list[tuple[int, float]], day_start_min: int) -> list[tuple[float, float]]:
    """Downsample 24h CGM to ~10-15 min spacing while preserving peaks and nadirs."""
    if not pairs:
        return []
    n = len(pairs)
    selected: list[tuple[float, float]] = []
    last_kept_min = -10_000

    for i, (m, v) in enumerate(pairs):
        is_endpoint = i == 0 or i == n - 1
        is_extremum = False
        if 0 < i < n - 1:
            prev_v = pairs[i - 1][1]
            next_v = pairs[i + 1][1]
            if (v >= prev_v and v > next_v and v >= 180) or (v <= prev_v and v < next_v and v <= 80):
                is_extremum = True
        if is_endpoint or is_extremum or (m - last_kept_min) >= 10:
            hr_float = round((m - day_start_min) / 60.0, 2)
            hr_float = max(0.0, min(24.0, hr_float))
            if selected and selected[-1][0] == hr_float:
                selected[-1] = (hr_float, round(v, 1))
            else:
                selected.append((hr_float, round(v, 1)))
            last_kept_min = m
    return selected


def build_day_replays(ds: CanonicalDataset, max_replays: int = 4) -> list[DayReplay]:
    """Select up to `max_replays` illustrative 24h day replays highlighting signature physiology."""
    if max_replays <= 0 or not ds.glucose:
        return []

    g_idx = GlucoseIndex(ds.glucose)
    if len(g_idx) == 0:
        return []

    b_mins, b_events = build_bolus_index(ds.boluses)
    trust_zero_iob = any(w.iob_at_start > 0 for w in ds.workouts)
    enriched_workouts = [
        enrich_workout_metrics(w, g_idx, b_mins, b_events, trust_zero_iob)
        for w in ds.workouts
    ]

    # Group CGM by calendar date
    cgm_by_date: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for m, v, pt in zip(g_idx.mins, g_idx.vals, g_idx.points):
        cgm_by_date[ts_date_str(pt.ts)].append((m, v))

    min_pts = 24 if any(len(v) >= 24 for v in cgm_by_date.values()) else 4
    valid_dates = {d for d, pts in cgm_by_date.items() if len(pts) >= min_pts and parse_date_safe(d) is not None}
    if not valid_dates:
        return []

    boluses_by_date: dict[str, list[BolusEvent]] = defaultdict(list)
    for b in ds.boluses:
        boluses_by_date[ts_date_str(b.ts)].append(b)

    workouts_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ew in enriched_workouts:
        workouts_by_date[ts_date_str(ew["workout"].start_ts)].append(ew)

    sleep_by_wake_date: dict[str, Any] = {}
    for s in ds.sleep_sessions:
        wd = s.wake_date or ts_date_str(s.end_ts)
        if wd:
            sleep_by_wake_date[wd] = s

    vitals_by_date = {v.date: v for v in ds.daily_vitals}

    sorted_dates = sorted(valid_dates)
    archetype_lists: list[list[tuple[float, str, str]]] = []

    # 1. Largest Walk Glucose Rescue
    rescue_cands: list[tuple[float, str, str]] = []
    for d in sorted_dates:
        for ew in workouts_by_date.get(d, []):
            w = ew["workout"]
            s_cgm = ew["start_cgm"]
            delta = ew["delta_cgm"]
            if (
                ("walk" in w.activity_type.lower() or w.mean_hr < 150)
                and s_cgm is not None
                and delta is not None
                and s_cgm >= 165.0
                and delta <= -20.0
            ):
                score = -delta + (0.2 * max(0.0, s_cgm - 180.0))
                label = f"{d} · Post-Meal Walk Rescue ({int(round(s_cgm))} → {int(round(s_cgm + delta))} mg/dL)"
                rescue_cands.append((score, d, label))
    if rescue_cands:
        rescue_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
        archetype_lists.append(rescue_cands)

    # 2. Largest Anaerobic / HIIT Workout Adrenaline Spike
    spike_cands: list[tuple[float, str, str]] = []
    for d in sorted_dates:
        for ew in workouts_by_date.get(d, []):
            w = ew["workout"]
            delta = ew["delta_cgm"]
            if delta is not None and delta >= 18.0 and (w.mean_hr >= 145.0 or "walk" not in w.activity_type.lower()):
                score = delta + (0.5 * max(0.0, w.mean_hr - 145.0))
                hr_tag = f" at {int(round(w.mean_hr))} bpm" if w.mean_hr > 0 else ""
                label = f"{d} · {w.activity_type} Adrenaline Spike (+{int(round(delta))} mg/dL{hr_tag})"
                spike_cands.append((score, d, label))
    if spike_cands:
        spike_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
        archetype_lists.append(spike_cands)

    # 3. Sharpest Bolus-Stacking Hypo -> Rebound Day
    rebound_cands: list[tuple[float, str, str]] = []
    for d in sorted_dates:
        pts = cgm_by_date[d]
        best_for_day: Optional[tuple[float, str, str]] = None
        for idx, (m_low, v_low) in enumerate(pts):
            if v_low < 70.0:
                subsequent = [v for m, v in pts[idx:] if 0 < (m - m_low) <= 210]
                if subsequent:
                    peak = max(subsequent)
                    if peak > 185.0:
                        swing = peak - v_low + (15.0 if len(boluses_by_date.get(d, [])) >= 3 else 0.0)
                        if best_for_day is None or swing > best_for_day[0]:
                            label = f"{d} · Hypo-to-Rebound Swing ({int(round(v_low))} → {int(round(peak))} mg/dL)"
                            best_for_day = (swing, d, label)
        if best_for_day is not None:
            rebound_cands.append(best_for_day)
    if rebound_cands:
        rebound_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
        archetype_lists.append(rebound_cands)

    # 4. Highest Smartwatch Blood Pressure Surge Day
    bp_cands: list[tuple[float, str, str]] = []
    for d in sorted_dates:
        vit = vitals_by_date.get(d)
        if vit is not None and vit.systolic_bp is not None and vit.systolic_bp >= 130.0:
            sys_v = float(vit.systolic_bp)
            dia_v = float(vit.diastolic_bp) if vit.diastolic_bp is not None else 82.0
            label = f"{d} · Smartwatch BP Surge ({int(round(sys_v))}/{int(round(dia_v))} mmHg)"
            bp_cands.append((sys_v, d, label))
    if bp_cands:
        bp_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
        archetype_lists.append(bp_cands)

    # 5. Morning Wake-Up Foot-on-Floor Surge Day (fallback archetype)
    dawn_cands: list[tuple[float, str, str]] = []
    for d in sorted_dates:
        s = sleep_by_wake_date.get(d)
        if s is not None:
            wake_g = s.wake_cgm
            surge = s.surge_3h_mg_dl
            if (wake_g is None or surge is None) and parse_ts_min(s.end_ts) is not None:
                em = parse_ts_min(s.end_ts)
                assert em is not None
                wake_g = wake_g if wake_g is not None else g_idx.nearest_val(em, 20)
                w3 = g_idx.window_vals(em, em + 180)
                if wake_g is not None and w3:
                    surge = max(w3) - wake_g
            if surge is not None and surge >= 45.0:
                label = f"{d} · Morning Wake Surge (+{int(round(surge))} mg/dL in 3h)"
                dawn_cands.append((surge, d, label))
    if dawn_cands:
        dawn_cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
        archetype_lists.append(dawn_cands)

    # Pick unique dates across archetypes in priority order, then fill remaining slots
    chosen: list[tuple[str, str]] = []
    used_dates: set[str] = set()
    for arch_list in archetype_lists:
        if len(chosen) >= max_replays:
            break
        for _, d, lbl in arch_list:
            if d not in used_dates:
                chosen.append((d, lbl))
                used_dates.add(d)
                break

    if len(chosen) < max_replays:
        day_swings: list[tuple[float, float, str]] = []
        for d in sorted(valid_dates):
            if d in used_dates:
                continue
            vals = [v for _, v in cgm_by_date[d]]
            tir = sum(1 for v in vals if 70.0 <= v <= 180.0) * 100.0 / len(vals)
            day_swings.append((max(vals) - min(vals), tir, d))

        # Add highest excursion day(s), and if space remains, one clean high-TIR reference day
        by_swing = sorted(day_swings, key=lambda x: x[0], reverse=True)
        for swing, _, d in by_swing:
            if len(chosen) >= max_replays:
                break
            if d not in used_dates:
                vals = [v for _, v in cgm_by_date[d]]
                chosen.append((d, f"{d} · High Glycemic Excursion ({int(round(min(vals)))}–{int(round(max(vals)))} mg/dL)"))
                used_dates.add(d)
                if len(chosen) == max_replays - 1:
                    by_tir = [x for x in day_swings if x[2] not in used_dates and x[1] >= 80.0]
                    if by_tir:
                        best_clean = max(by_tir, key=lambda x: x[1])
                        chosen.append((best_clean[2], f"{best_clean[2]} · Steady In-Range Day ({int(round(best_clean[1]))}% TIR)"))
                        used_dates.add(best_clean[2])
                        break

    replays: list[DayReplay] = []
    for d, label in chosen:
        d_obj = parse_date_safe(d)
        if d_obj is None:
            continue
        day_start_min = d_obj.toordinal() * 1440
        cgm_pts = _downsample_day_cgm(cgm_by_date[d], day_start_min)

        day_boluses: list[dict[str, Any]] = []
        for b in sorted(boluses_by_date.get(d, []), key=lambda x: x.ts):
            bm = parse_ts_min(b.ts)
            b_hr = round(ts_hour_float(b.ts), 2)
            g_at_b = b.glucose_at_bolus
            if g_at_b is None and bm is not None:
                g_at_b = g_idx.nearest_val(bm, max_tol_min=15)
            day_boluses.append(
                {
                    "hour": b_hr,
                    "ts": b.ts,
                    "units": round(b.units, 2),
                    "carbs_g": round(b.carbs_g, 1),
                    "glucose_at_bolus": round(g_at_b, 1) if g_at_b is not None else None,
                }
            )

        day_walks: list[dict[str, Any]] = []
        for ew in sorted(workouts_by_date.get(d, []), key=lambda x: x["workout"].start_ts):
            w = ew["workout"]
            s_hr = round(ts_hour_float(w.start_ts), 2)
            e_hr = round(min(24.0, s_hr + max(w.duration_min, 5.0) / 60.0), 2)
            day_walks.append(
                {
                    "hour": s_hr,
                    "start_hour": s_hr,
                    "end_hour": e_hr,
                    "activity_type": w.activity_type,
                    "duration_min": round(w.duration_min, 1),
                    "steps": ew["steps"],
                    "mean_hr": round(w.mean_hr, 1),
                    "start_cgm": round(ew["start_cgm"], 1) if ew["start_cgm"] is not None else None,
                    "end_cgm": round(ew["end_cgm"], 1) if ew["end_cgm"] is not None else None,
                    "delta_cgm": round(ew["delta_cgm"], 1) if ew["delta_cgm"] is not None else None,
                    "iob_at_start": round(ew["iob_at_start"], 2),
                }
            )

        sleep_end_hour: Optional[float] = None
        s_sess = sleep_by_wake_date.get(d)
        if s_sess is not None and ts_date_str(s_sess.end_ts) == d:
            sleep_end_hour = round(ts_hour_float(s_sess.end_ts), 2)

        bp_events: list[dict[str, Any]] = []
        vit = vitals_by_date.get(d)
        if vit is not None and vit.systolic_bp is not None and vit.systolic_bp > 0:
            bp_hr = round(min(23.5, (sleep_end_hour + 0.5) if sleep_end_hour is not None else 8.5), 2)
            bp_events.append(
                {
                    "hour": bp_hr,
                    "systolic_bp": round(vit.systolic_bp, 0),
                    "diastolic_bp": round(vit.diastolic_bp, 0) if vit.diastolic_bp is not None else 80.0,
                    "pulse_bpm": round(vit.pulse_bpm, 0) if vit.pulse_bpm is not None else None,
                }
            )

        replays.append(
            DayReplay(
                date=d,
                label=label,
                cgm=cgm_pts,
                boluses=day_boluses,
                walks=day_walks,
                sleep_end_hour=sleep_end_hour,
                bp_events=bp_events,
            )
        )

    return replays


def simulate_walk(ds: CanonicalDataset, req: WalkSimulationRequest) -> WalkSimulationResponse:
    """Predict walk glycemic delta, ending CGM, hypo risk tier, and pre-walk safety guidance."""
    model = ds.walk_model
    if model is None and len(ds.workouts) >= 10:
        model = fit_walk_model(ds)
        ds.walk_model = model

    if model is not None and model.sample_count >= 10:
        zg = (req.start_cgm - model.mean_g0) / max(model.sd_g0, 1e-6)
        zi = (req.active_iob - model.mean_iob) / max(model.sd_iob, 1e-6)
        zs = (req.planned_steps - model.mean_steps) / max(model.sd_steps, 1e-6)
        inter_center = model.mean_steps * model.mean_iob
        inter_scale = max(model.sd_steps * model.sd_iob, 1e-6)
        z_inter = (req.planned_steps * req.active_iob - inter_center) / inter_scale
        raw_delta = (
            model.bias
            + model.w_start_cgm * zg
            + model.w_iob * zi
            + model.w_steps * zs
            + model.w_steps_x_iob * z_inter
        )
        model_r = model.holdout_r
    else:
        # Physiological GLUT4 + IOB interaction fallback when <10 walks are available
        step_scale = max(0.0, req.planned_steps) / 2500.0
        base_drop = -14.0 * step_scale
        excess_bg_drop = -0.12 * max(0.0, req.start_cgm - 130.0) * min(step_scale, 2.0)
        iob_synergy_drop = -16.0 * max(0.0, req.active_iob) * step_scale
        raw_delta = base_drop + excess_bg_drop + iob_synergy_drop
        model_r = model.holdout_r if model is not None else 0.0

    pred_end = max(40.0, min(400.0, req.start_cgm + raw_delta))
    pred_end_rounded = round(pred_end, 1)
    pred_delta_rounded = round(pred_end_rounded - req.start_cgm, 1)

    if pred_end_rounded < 80.0 or (req.start_cgm < 150.0 and req.active_iob >= 1.0):
        risk: Any = "high"
        if req.active_iob >= 1.0:
            rec = (
                f"High hypoglycemia risk: {req.active_iob:.1f}U active bolus IOB strongly amplifies "
                f"walking glucose uptake (projected end ~{pred_end_rounded:.0f} mg/dL). Take 15–20g "
                f"uncovered fast-acting carbs before starting, enable Activity Temp Target 30–60 min "
                f"ahead if possible, or wait until active bolus IOB decays below 0.8U."
            )
        else:
            rec = (
                f"High hypoglycemia risk: projected ending glucose is ~{pred_end_rounded:.0f} mg/dL "
                f"after {req.planned_steps:,} steps. Take 15g fast-acting carbs before walking and "
                f"keep rapid glucose tabs accessible."
            )
    elif pred_end_rounded < 105.0:
        risk = "moderate"
        rec = (
            f"Moderate drop buffer: glucose is projected to finish near {pred_end_rounded:.0f} mg/dL "
            f"({pred_delta_rounded:+.0f} mg/dL). Consider an 8–12g uncovered carb snack or enabling "
            f"Activity Mode before stepping out, and carry fast-acting carbs."
        )
    else:
        risk = "low"
        if req.start_cgm >= 180.0:
            rec = (
                f"Effective glucose-rescue window: {req.planned_steps:,} steps is projected to bring "
                f"{req.start_cgm:.0f} mg/dL down to ~{pred_end_rounded:.0f} mg/dL ({pred_delta_rounded:+.0f} mg/dL) "
                f"without stacking extra correction insulin. Still carry fast carbs if IOB is active."
            )
        else:
            rec = (
                f"Low hypoglycemia risk: glucose is projected to remain comfortably in range near "
                f"~{pred_end_rounded:.0f} mg/dL ({pred_delta_rounded:+.0f} mg/dL). Standard precaution: "
                f"keep fast-acting carbs in your pocket."
            )

    return WalkSimulationResponse(
        predicted_delta_mg_dl=pred_delta_rounded,
        predicted_end_cgm=pred_end_rounded,
        hypo_risk_level=risk,
        recommendation=rec,
        model_r=round(model_r, 3),
    )
