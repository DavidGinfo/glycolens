from __future__ import annotations

import csv
import io
import os
import statistics
import zipfile
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from glycolens.adapters.glooko_adapter import (
    DIA_HOURS,
    compute_iob_at,
    lookup_cgm_near,
    snap_5min,
)
from glycolens.schemas import (
    DailyVitalsSummary,
    PersonalThresholds,
    SleepSessionSummary,
    SleepStageInterval,
    WalkModelWeights,
    WorkoutSession,
)

EXERCISE_TYPE_NAMES: dict[str, str] = {
    "1001": "Walking",
    "1002": "Running",
    "11007": "Cycling",
    "13001": "Hiking",
    "14001": "Swimming",
    "15002": "Elliptical",
    "15005": "Rowing",
    "15006": "Circuit Training",
    "10001": "Weightlifting",
    "10002": "Strength Training",
    "10004": "HIIT",
    "0": "Workout",
}

SLEEP_STAGE_MAP: dict[str, str] = {
    "40001": "Awake",
    "40002": "Light",
    "40003": "Deep",
    "40004": "REM",
    "Awake": "Awake",
    "Light": "Light",
    "Deep": "Deep",
    "REM": "REM",
}


def parse_sh_utc_to_local(ts_str: str, offset_str: str = "") -> Optional[datetime]:
    """Convert Samsung Health UTC timestamp + time_offset (e.g. UTC+0100, UTC+0200) to local datetime."""
    s = (ts_str or "").strip()
    if not s:
        return None
    ts: Optional[datetime] = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            ts = datetime.strptime(s[: len(fmt) + 2].strip()[:19], fmt)
            break
        except ValueError:
            continue
    if ts is None:
        return None

    off = (offset_str or "").strip()
    if off.startswith("UTC"):
        off = off[3:]
    off_clean = off.replace(":", "")
    if len(off_clean) >= 5 and off_clean[0] in ("+", "-"):
        try:
            sign = 1 if off_clean[0] == "+" else -1
            hh = int(off_clean[1:3])
            mm = int(off_clean[3:5])
            return ts + timedelta(minutes=sign * (hh * 60 + mm))
        except ValueError:
            pass
    return ts + timedelta(hours=1)


def _safe_float(val: Optional[str], default: float = 0.0) -> float:
    if val is None:
        return default
    s = val.strip()
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _iter_watch_csvs(
    watch_bytes: Optional[bytes] = None,
    watch_dir: Optional[str] = None,
) -> Iterable[tuple[str, str]]:
    """Yield (basename, decoded_text) for Samsung Health CSVs, ignoring duplicate (1).csv files."""
    if watch_bytes is not None:
        buf = io.BytesIO(watch_bytes)
        if zipfile.is_zipfile(buf):
            buf.seek(0)
            with zipfile.ZipFile(buf, "r") as zf:
                for info in sorted(zf.infolist(), key=lambda x: x.filename):
                    if info.is_dir():
                        continue
                    name = info.filename
                    base = os.path.basename(name)
                    if (
                        "__MACOSX" in name
                        or base.startswith("._")
                        or base.startswith(".")
                        or "(1)" in base
                    ):
                        continue
                    if base.lower().endswith(".csv"):
                        yield base, zf.read(info.filename).decode("utf-8-sig", errors="replace")
                    elif base.lower().endswith(".zip"):
                        yield from _iter_watch_csvs(watch_bytes=zf.read(info.filename))
        else:
            yield "uploaded_watch.csv", watch_bytes.decode("utf-8-sig", errors="replace")

    if watch_dir and os.path.isdir(watch_dir):
        for root, _, files in os.walk(watch_dir):
            for fname in sorted(files):
                if fname.startswith("._") or fname.startswith(".") or "(1)" in fname:
                    continue
                if fname.lower().endswith(".csv"):
                    full = os.path.join(root, fname)
                    with open(full, "r", encoding="utf-8-sig", errors="replace") as f:
                        yield fname, f.read()


def _parse_samsung_csv(text: str) -> list[dict[str, str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    first_cells = next(csv.reader([lines[0]]), [])
    first_token = first_cells[0].lstrip("\ufeff").strip() if first_cells else ""
    # Samsung Health CSVs have a metadata banner on line 1 starting with com.samsung.*
    has_meta_banner = first_token.startswith("com.samsung.") and len(lines) >= 2 and (
        len(first_cells) <= 6 or first_cells[1].strip().isdigit()
    )
    header_idx = 1 if has_meta_banner else 0
    if len(lines) <= header_idx:
        return []

    raw_header = next(csv.reader([lines[header_idx]]), [])
    short_header = [col.split(".")[-1].strip() for col in raw_header]
    rows: list[dict[str, str]] = []
    for raw_row in csv.reader(lines[header_idx + 1 :]):
        if not raw_row:
            continue
        rows.append(
            {
                short_header[i]: raw_row[i].strip()
                for i in range(min(len(short_header), len(raw_row)))
                if short_header[i]
            }
        )
    return rows


def enrich_workouts_with_cgm(
    raw_workouts: list[tuple[datetime, datetime, WorkoutSession]],
    cgm_by_min: dict[datetime, float],
    bolus_times: list[datetime],
    bolus_units: list[float],
) -> list[WorkoutSession]:
    """Attach matched start_cgm, end_cgm, delta_cgm, nadir_90m_cgm, and iob_at_start."""
    enriched: list[WorkoutSession] = []
    for st_dt, en_dt, ws in sorted(raw_workouts, key=lambda x: x[0]):
        start_cgm = lookup_cgm_near(cgm_by_min, st_dt, max_tolerance_min=10)
        end_cgm = lookup_cgm_near(cgm_by_min, en_dt, max_tolerance_min=10)
        delta_cgm = (
            round(end_cgm - start_cgm, 1)
            if (start_cgm is not None and end_cgm is not None)
            else None
        )

        st_grid = snap_5min(st_dt)
        en_plus_90 = snap_5min(en_dt) + timedelta(minutes=90)
        win_vals: list[float] = []
        cur = st_grid
        while cur <= en_plus_90:
            if cur in cgm_by_min:
                win_vals.append(cgm_by_min[cur])
            cur += timedelta(minutes=5)
        nadir_90m = round(min(win_vals), 1) if win_vals else None

        iob_start = compute_iob_at(st_dt, bolus_times, bolus_units, DIA_HOURS)

        enriched.append(
            ws.model_copy(
                update={
                    "start_cgm": round(start_cgm, 1) if start_cgm is not None else None,
                    "end_cgm": round(end_cgm, 1) if end_cgm is not None else None,
                    "delta_cgm": delta_cgm,
                    "nadir_90m_cgm": nadir_90m,
                    "iob_at_start": iob_start,
                }
            )
        )
    return enriched


def aggregate_and_enrich_sleep(
    raw_intervals: list[tuple[str, datetime, datetime, str, float]],
    cgm_by_min: dict[datetime, float],
    alarm_times: list[datetime],
    start_date_filter: Optional[date] = None,
    end_date_filter: Optional[date] = None,
) -> tuple[list[SleepStageInterval], list[SleepSessionSummary]]:
    """Build SleepStageInterval list and aggregate overnight SleepSessionSummary records."""
    sessions_acc: dict[str, dict[str, object]] = {}
    stage_intervals: list[tuple[datetime, SleepStageInterval]] = []

    for sid, st_dt, en_dt, stage, dur_min in raw_intervals:
        if dur_min <= 0.0 or dur_min >= 600.0:
            continue

        in_stage_window = True
        if start_date_filter and end_date_filter:
            in_stage_window = (
                start_date_filter <= st_dt.date() <= end_date_filter
                or start_date_filter <= en_dt.date() <= end_date_filter
            )
        if in_stage_window:
            stage_intervals.append(
                (
                    st_dt,
                    SleepStageInterval(
                        sleep_id=sid,
                        start_ts=st_dt.strftime("%Y-%m-%d %H:%M"),
                        end_ts=en_dt.strftime("%Y-%m-%d %H:%M"),
                        stage=stage,  # type: ignore[arg-type]
                        duration_min=round(dur_min, 1),
                    ),
                )
            )

        acc = sessions_acc.get(sid)
        if acc is None:
            acc = {
                "Awake": 0.0,
                "Light": 0.0,
                "Deep": 0.0,
                "REM": 0.0,
                "start": st_dt,
                "end": en_dt,
            }
            sessions_acc[sid] = acc
        acc[stage] = float(acc[stage]) + dur_min  # type: ignore[arg-type]
        if st_dt < acc["start"]:  # type: ignore[operator]
            acc["start"] = st_dt
        if en_dt > acc["end"]:  # type: ignore[operator]
            acc["end"] = en_dt

    stage_intervals.sort(key=lambda x: x[0])
    sorted_stages = [iv for _, iv in stage_intervals]

    alarm_times_sorted = sorted(alarm_times)

    candidates_all: list[tuple[str, datetime, datetime, float, float, float, float]] = []
    for sid, acc in sessions_acc.items():
        st_dt = acc["start"]  # type: ignore[assignment]
        en_dt = acc["end"]  # type: ignore[assignment]
        light_m = float(acc["Light"])  # type: ignore[arg-type]
        deep_m = float(acc["Deep"])  # type: ignore[arg-type]
        rem_m = float(acc["REM"])  # type: ignore[arg-type]
        awake_m = float(acc["Awake"])  # type: ignore[arg-type]
        total_sleep = light_m + deep_m + rem_m
        if total_sleep < 180.0:
            continue
        if start_date_filter and end_date_filter:
            if not (start_date_filter <= en_dt.date() <= end_date_filter):
                continue
        candidates_all.append((sid, st_dt, en_dt, total_sleep, deep_m, rem_m, light_m, awake_m))  # type: ignore[arg-type]

    overnight_candidates = [
        c for c in candidates_all if (c[1].hour >= 20 or c[1].hour <= 5)
    ]
    selected = overnight_candidates if overnight_candidates else candidates_all
    selected.sort(key=lambda c: c[1])

    summaries: list[SleepSessionSummary] = []
    for sid, st_dt, en_dt, total_sleep, deep_m, rem_m, light_m, awake_m in selected:  # type: ignore[misc]
        slp_cgm: list[float] = []
        cur = snap_5min(st_dt)
        end_grid = snap_5min(en_dt)
        while cur <= end_grid:
            if cur in cgm_by_min:
                slp_cgm.append(cgm_by_min[cur])
            cur += timedelta(minutes=5)

        if slp_cgm:
            n_pts = len(slp_cgm)
            ov_tir: Optional[float] = round(
                sum(1 for v in slp_cgm if 70.0 <= v <= 180.0) / n_pts * 100.0, 1
            )
            ov_tar180: Optional[float] = round(
                sum(1 for v in slp_cgm if v > 180.0) / n_pts * 100.0, 1
            )
            ov_tbr70: Optional[float] = round(
                sum(1 for v in slp_cgm if v < 70.0) / n_pts * 100.0, 1
            )
        else:
            ov_tir = None
            ov_tar180 = None
            ov_tbr70 = None

        wake_cgm = lookup_cgm_near(cgm_by_min, en_dt, max_tolerance_min=10)
        post_wake = [
            cgm_by_min[end_grid + timedelta(minutes=5 * k)]
            for k in range(1, 37)
            if (end_grid + timedelta(minutes=5 * k)) in cgm_by_min
        ]
        surge_3h = (
            round(max(post_wake) - wake_cgm, 1)
            if (wake_cgm is not None and post_wake)
            else None
        )

        lo_a = bisect_left(alarm_times_sorted, st_dt)
        hi_a = bisect_right(alarm_times_sorted, en_dt)
        overnight_alarms = max(0, hi_a - lo_a)

        summaries.append(
            SleepSessionSummary(
                sleep_id=sid,
                wake_date=en_dt.strftime("%Y-%m-%d"),
                start_ts=st_dt.strftime("%Y-%m-%d %H:%M"),
                end_ts=en_dt.strftime("%Y-%m-%d %H:%M"),
                total_sleep_min=round(total_sleep, 1),
                deep_min=round(deep_m, 1),
                rem_min=round(rem_m, 1),
                light_min=round(light_m, 1),
                awake_min=round(awake_m, 1),
                wake_cgm=round(wake_cgm, 1) if wake_cgm is not None else None,
                surge_3h_mg_dl=surge_3h,
                overnight_tir_pct=ov_tir,
                overnight_tar180_pct=ov_tar180,
                overnight_tbr70_pct=ov_tbr70,
                overnight_alarms=overnight_alarms,
            )
        )

    return sorted_stages, summaries


def _solve_5x5(mat: list[list[float]], rhs: list[float]) -> list[float]:
    k = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(mat)]
    for col in range(k):
        piv_r = max(range(col, k), key=lambda r: abs(a[r][col]))
        if abs(a[piv_r][col]) < 1e-12:
            continue
        if piv_r != col:
            a[col], a[piv_r] = a[piv_r], a[col]
        piv = a[col][col]
        for j in range(col, k + 1):
            a[col][j] /= piv
        for r in range(k):
            if r != col and abs(a[r][col]) > 1e-15:
                fac = a[r][col]
                for j in range(col, k + 1):
                    a[r][j] -= fac * a[col][j]
    return [a[i][k] if abs(a[i][i]) > 1e-12 else 0.0 for i in range(k)]


def _fit_ridge_5(
    rows: list[WorkoutSession], alpha: float = 1.0
) -> tuple[dict[str, float], list[float]]:
    g0_tr = [float(w.start_cgm or 0.0) for w in rows]
    iob_tr = [float(w.iob_at_start) for w in rows]
    st_tr = [float(w.steps) for w in rows]
    y_tr = [float(w.delta_cgm or 0.0) for w in rows]

    mean_g0 = statistics.fmean(g0_tr)
    sd_g0 = statistics.stdev(g0_tr) if len(g0_tr) > 1 else 1.0
    if sd_g0 < 1e-4:
        sd_g0 = 1.0

    mean_iob = statistics.fmean(iob_tr)
    sd_iob = statistics.stdev(iob_tr) if len(iob_tr) > 1 else 1.0
    if sd_iob < 1e-4:
        sd_iob = 1.0

    mean_st = statistics.fmean(st_tr)
    sd_st = statistics.stdev(st_tr) if len(st_tr) > 1 else 1.0
    if sd_st < 1e-4:
        sd_st = 1.0

    inter_center = mean_st * mean_iob
    inter_scale = max(sd_st * sd_iob, 1e-4)

    xtx = [[0.0] * 5 for _ in range(5)]
    xty = [0.0] * 5
    for g0, iob, st, y in zip(g0_tr, iob_tr, st_tr, y_tr):
        zg = (g0 - mean_g0) / sd_g0
        zi = (iob - mean_iob) / sd_iob
        zs = (st - mean_st) / sd_st
        z_inter = (st * iob - inter_center) / inter_scale
        feat = [1.0, zg, zi, zs, z_inter]
        for i in range(5):
            xty[i] += feat[i] * y
            for j in range(5):
                xtx[i][j] += feat[i] * feat[j]
    for idx in range(1, 5):
        xtx[idx][idx] += alpha

    weights = _solve_5x5(xtx, xty)
    stats_d = {
        "mean_g0": mean_g0,
        "sd_g0": sd_g0,
        "mean_iob": mean_iob,
        "sd_iob": sd_iob,
        "mean_st": mean_st,
        "sd_st": sd_st,
    }
    return stats_d, weights


def _pred_ridge_5(w: WorkoutSession, stats_d: dict[str, float], weights: list[float]) -> float:
    g0 = float(w.start_cgm or stats_d["mean_g0"])
    iob = float(w.iob_at_start)
    st = float(w.steps)
    zg = (g0 - stats_d["mean_g0"]) / max(stats_d["sd_g0"], 1e-6)
    zi = (iob - stats_d["mean_iob"]) / max(stats_d["sd_iob"], 1e-6)
    zs = (st - stats_d["mean_st"]) / max(stats_d["sd_st"], 1e-6)
    z_inter = (st * iob - stats_d["mean_st"] * stats_d["mean_iob"]) / max(
        stats_d["sd_st"] * stats_d["sd_iob"], 1e-6
    )
    return (
        weights[0]
        + weights[1] * zg
        + weights[2] * zi
        + weights[3] * zs
        + weights[4] * z_inter
    )


def fit_walk_model_weights(workouts: list[WorkoutSession]) -> Optional[WalkModelWeights]:
    """Fit 4-feature standardized walk glucose response model on chronological 75/25 split."""
    samples = [
        w
        for w in workouts
        if w.duration_min >= 10.0
        and w.steps > 0
        and w.start_cgm is not None
        and w.delta_cgm is not None
        and w.activity_type in ("Walking", "Hiking", "Running")
    ]
    samples.sort(key=lambda w: w.start_ts)
    if len(samples) < 12:
        return None

    split = max(8, int(len(samples) * 0.75))
    train = samples[:split]
    test = samples[split:]

    tr_stats, tr_weights = _fit_ridge_5(train, alpha=1.0)
    eval_set = test if len(test) >= 3 else train
    y_ev = [float(w.delta_cgm or 0.0) for w in eval_set]
    preds = [_pred_ridge_5(w, tr_stats, tr_weights) for w in eval_set]
    mae = statistics.fmean(abs(y - p) for y, p in zip(y_ev, preds))
    if len(eval_set) >= 3 and statistics.stdev(y_ev) > 1e-6 and statistics.stdev(preds) > 1e-6:
        r_val = statistics.correlation(y_ev, preds)
    else:
        r_val = 0.0

    full_stats, full_weights = _fit_ridge_5(samples, alpha=1.0)

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
        mean_steps=round(full_stats["mean_st"], 1),
        sd_steps=round(full_stats["sd_st"], 1),
        holdout_r=round(r_val, 3),
        mae_mg_dl=round(mae, 2),
        sample_count=len(samples),
    )


class SamsungWatchParsedData:
    def __init__(
        self,
        workouts: list[WorkoutSession],
        sleep_stages: list[SleepStageInterval],
        sleep_sessions: list[SleepSessionSummary],
        daily_vitals: list[DailyVitalsSummary],
        thresholds: PersonalThresholds,
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> None:
        self.workouts = workouts
        self.sleep_stages = sleep_stages
        self.sleep_sessions = sleep_sessions
        self.daily_vitals = daily_vitals
        self.thresholds = thresholds
        self.start_date = start_date
        self.end_date = end_date


def parse_samsung_watch_export(
    watch_bytes: Optional[bytes] = None,
    watch_dir: Optional[str] = None,
    cgm_by_min: Optional[dict[datetime, float]] = None,
    bolus_times: Optional[list[datetime]] = None,
    bolus_units: Optional[list[float]] = None,
    alarm_times: Optional[list[datetime]] = None,
    start_date_filter: Optional[date] = None,
    end_date_filter: Optional[date] = None,
) -> SamsungWatchParsedData:
    cgm_map = cgm_by_min or {}
    b_times = bolus_times or []
    b_units = bolus_units or []
    a_times = alarm_times or []

    thresholds = PersonalThresholds()
    raw_workouts: list[tuple[datetime, datetime, WorkoutSession]] = []
    seen_workouts: set[tuple[str, str, int]] = set()
    raw_sleep_intervals: list[tuple[str, datetime, datetime, str, float]] = []
    seen_sleep_iv: set[tuple[str, str, str]] = set()

    best_act_by_day: dict[date, tuple[tuple[int, float], dict[str, str]]] = {}
    best_cal_by_day: dict[date, float] = {}
    temp_by_day: dict[date, tuple[float, Optional[float]]] = {}
    resp_by_day: dict[date, float] = {}
    bp_by_day: dict[date, list[tuple[float, float, Optional[float]]]] = defaultdict(list)

    for fname, text in _iter_watch_csvs(watch_bytes, watch_dir):
        low = fname.lower()
        rows = _parse_samsung_csv(text)
        if not rows:
            continue

        if "exercise.hr_zone" in low:
            last = rows[-1]
            at = int(_safe_float(last.get("at"), 131.0))
            ant = int(_safe_float(last.get("ant"), 179.0))
            max_custom = int(_safe_float(last.get("max_hr_custom"), 0.0))
            max_auto = int(_safe_float(last.get("max_hr_auto"), 209.0))
            thresholds = PersonalThresholds(
                aerobic_hr=at if at > 50 else 131,
                anaerobic_hr=ant if ant > 80 else 179,
                max_hr=max_custom if max_custom > 100 else (max_auto if max_auto > 100 else 209),
            )

        elif (
            ("shealth.exercise." in low or "health.exercise." in low)
            and "custom_exercise" not in low
            and "extension" not in low
            and "hr_zone" not in low
            and "max_heart_rate" not in low
        ):
            for r in rows:
                st_dt = parse_sh_utc_to_local(r.get("start_time", ""), r.get("time_offset", ""))
                en_dt = parse_sh_utc_to_local(r.get("end_time", ""), r.get("time_offset", ""))
                if st_dt is None or en_dt is None:
                    continue
                if start_date_filter and end_date_filter:
                    if not (start_date_filter <= st_dt.date() <= end_date_filter):
                        continue
                dur_ms = _safe_float(r.get("duration"))
                dur_min = dur_ms / 60000.0 if dur_ms > 0 else (en_dt - st_dt).total_seconds() / 60.0
                if dur_min <= 0.0:
                    continue
                ex_code = (r.get("exercise_type") or "").strip()
                custom_title = (r.get("title") or "").strip()
                steps = int(round(_safe_float(r.get("count"))))
                act_name = custom_title or EXERCISE_TYPE_NAMES.get(
                    ex_code, "Walking" if steps > 0 else "Workout"
                )

                st_str = st_dt.strftime("%Y-%m-%d %H:%M")
                en_str = en_dt.strftime("%Y-%m-%d %H:%M")
                dedup_key = (st_str, en_str, steps)
                if dedup_key in seen_workouts:
                    continue
                seen_workouts.add(dedup_key)

                ws = WorkoutSession(
                    start_ts=st_str,
                    end_ts=en_str,
                    activity_type=act_name,
                    duration_min=round(dur_min, 1),
                    steps=max(0, steps),
                    cadence=round(max(0.0, _safe_float(r.get("mean_cadence"))), 1),
                    speed_mps=round(max(0.0, _safe_float(r.get("mean_speed"))), 2),
                    mean_hr=round(max(0.0, _safe_float(r.get("mean_heart_rate"))), 1),
                    max_hr=round(max(0.0, _safe_float(r.get("max_heart_rate"))), 1),
                    calories=round(
                        max(0.0, _safe_float(r.get("calorie") or r.get("total_calorie"))), 1
                    ),
                )
                raw_workouts.append((st_dt, en_dt, ws))

        elif "sleep_stage" in low:
            for r in rows:
                sid = (r.get("sleep_id") or "").strip()
                stg = SLEEP_STAGE_MAP.get((r.get("stage") or "").strip())
                if not sid or not stg:
                    continue
                st_dt = parse_sh_utc_to_local(r.get("start_time", ""), r.get("time_offset", ""))
                en_dt = parse_sh_utc_to_local(r.get("end_time", ""), r.get("time_offset", ""))
                if st_dt is None or en_dt is None or en_dt <= st_dt:
                    continue
                st_str = st_dt.strftime("%Y-%m-%d %H:%M")
                en_str = en_dt.strftime("%Y-%m-%d %H:%M")
                key = (sid, st_str, en_str)
                if key in seen_sleep_iv:
                    continue
                seen_sleep_iv.add(key)
                dur_min = (en_dt - st_dt).total_seconds() / 60.0
                raw_sleep_intervals.append((sid, st_dt, en_dt, stg, dur_min))

        elif "activity.day_summary" in low:
            for r in rows:
                dt_s = (r.get("day_time") or "").strip()
                if len(dt_s) < 10:
                    continue
                try:
                    d = datetime.strptime(dt_s[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                steps_f = _safe_float(r.get("step_count"), 0.0)
                idle_f = _safe_float(r.get("longest_idle_time"), -1.0)
                score_key = (1 if idle_f > 0 else 0, steps_f)
                prev = best_act_by_day.get(d)
                if prev is None or score_key > prev[0]:
                    best_act_by_day[d] = (score_key, r)

        elif "calories_burned.details" in low:
            for r in rows:
                dt_s = (r.get("day_time") or "").strip()
                if len(dt_s) < 10:
                    continue
                try:
                    d = datetime.strptime(dt_s[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                ac = _safe_float(r.get("active_calorie"), 0.0)
                if ac > best_cal_by_day.get(d, -1.0):
                    best_cal_by_day[d] = ac

        elif "skin_temperature" in low:
            for r in rows:
                en_dt = parse_sh_utc_to_local(
                    r.get("end_time") or r.get("start_time") or "",
                    r.get("time_offset", ""),
                )
                if en_dt is None:
                    continue
                mean_t = _safe_float(r.get("stat_m1") or r.get("temperature"), -999.0)
                min_t_raw = r.get("min", "")
                min_t = _safe_float(min_t_raw, -999.0) if min_t_raw else -999.0
                if 20.0 <= mean_t <= 45.0:
                    temp_by_day[en_dt.date()] = (
                        round(mean_t, 2),
                        round(min_t, 2) if 20.0 <= min_t <= 45.0 else None,
                    )

        elif "respiratory_rate" in low:
            for r in rows:
                en_dt = parse_sh_utc_to_local(
                    r.get("end_time") or r.get("start_time") or "",
                    r.get("time_offset", ""),
                )
                if en_dt is None:
                    continue
                avg_rr = _safe_float(r.get("average"), 0.0)
                if 5.0 < avg_rr <= 45.0:
                    resp_by_day[en_dt.date()] = round(avg_rr, 2)

        elif (
            ("shealth.blood_pressure" in low or "health.blood_pressure" in low)
            and "calibration_blood_pressure" not in low
        ):
            for r in rows:
                st_dt = parse_sh_utc_to_local(r.get("start_time", ""), r.get("time_offset", ""))
                if st_dt is None:
                    continue
                sys_v = _safe_float(r.get("systolic"), 0.0)
                dia_v = _safe_float(r.get("diastolic"), 0.0)
                pls_v = _safe_float(r.get("pulse"), 0.0)
                if sys_v > 50.0 and dia_v > 30.0:
                    bp_by_day[st_dt.date()].append(
                        (sys_v, dia_v, pls_v if pls_v > 25.0 else None)
                    )

    workouts = enrich_workouts_with_cgm(raw_workouts, cgm_map, b_times, b_units)
    sleep_stages, sleep_sessions = aggregate_and_enrich_sleep(
        raw_sleep_intervals,
        cgm_map,
        a_times,
        start_date_filter=start_date_filter,
        end_date_filter=end_date_filter,
    )

    all_vital_dates = (
        set(best_act_by_day.keys())
        | set(best_cal_by_day.keys())
        | set(temp_by_day.keys())
        | set(resp_by_day.keys())
        | set(bp_by_day.keys())
    )
    if start_date_filter and end_date_filter:
        all_vital_dates = {
            d for d in all_vital_dates if start_date_filter <= d <= end_date_filter
        }

    daily_vitals: list[DailyVitalsSummary] = []
    for d in sorted(all_vital_dates):
        act_row = best_act_by_day[d][1] if d in best_act_by_day else {}
        steps_i = int(max(0.0, _safe_float(act_row.get("step_count"), 0.0)))
        walk_m = round(max(0.0, _safe_float(act_row.get("walk_time"), 0.0)) / 60000.0, 1)
        run_m = round(max(0.0, _safe_float(act_row.get("run_time"), 0.0)) / 60000.0, 1)
        act_cal = max(
            0.0,
            _safe_float(act_row.get("calorie"), 0.0),
            best_cal_by_day.get(d, 0.0),
        )
        move_hr = int(max(0.0, _safe_float(act_row.get("move_hourly_count"), 0.0)))
        floors_f = round(max(0.0, _safe_float(act_row.get("floor_count"), 0.0)), 1)

        w_temp = temp_by_day[d][0] if d in temp_by_day else None
        w_temp_min = temp_by_day[d][1] if d in temp_by_day else None
        rr_val = resp_by_day.get(d)

        if d in bp_by_day and bp_by_day[d]:
            bp_entries = bp_by_day[d]
            sys_bp: Optional[float] = round(statistics.mean(x[0] for x in bp_entries), 1)
            dia_bp: Optional[float] = round(statistics.mean(x[1] for x in bp_entries), 1)
            pulses = [x[2] for x in bp_entries if x[2] is not None]
            pls_bpm: Optional[float] = round(statistics.mean(pulses), 1) if pulses else None
        else:
            sys_bp = None
            dia_bp = None
            pls_bpm = None

        daily_vitals.append(
            DailyVitalsSummary(
                date=d.strftime("%Y-%m-%d"),
                steps=steps_i,
                walk_min=walk_m,
                run_min=run_m,
                active_calories=round(act_cal, 1),
                move_hourly_count=move_hr,
                floors=floors_f,
                wrist_temp_c=w_temp,
                wrist_temp_min_c=w_temp_min,
                resp_rate_brpm=rr_val,
                systolic_bp=sys_bp,
                diastolic_bp=dia_bp,
                pulse_bpm=pls_bpm,
                hrv_ms=None,
            )
        )

    w_dates = [datetime.strptime(v.date, "%Y-%m-%d").date() for v in daily_vitals]
    start_d = min(w_dates) if w_dates else None
    end_d = max(w_dates) if w_dates else None

    return SamsungWatchParsedData(
        workouts=workouts,
        sleep_stages=sleep_stages,
        sleep_sessions=sleep_sessions,
        daily_vitals=daily_vitals,
        thresholds=thresholds,
        start_date=start_d,
        end_date=end_d,
    )
