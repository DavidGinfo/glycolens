from __future__ import annotations

import math
import os
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from glycolens.adapters.glooko_adapter import enrich_glucose_and_boluses
from glycolens.adapters.samsung_watch_adapter import (
    aggregate_and_enrich_sleep,
    enrich_workouts_with_cgm,
    fit_walk_model_weights,
)
from glycolens.schemas import (
    BasalEvent,
    CanonicalDataset,
    DailyInsulinSummary,
    DailyVitalsSummary,
    DeviceAlarm,
    PersonalThresholds,
    PersonaInfo,
    WorkoutSession,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC_DIR = REPO_ROOT / "sample_data" / "synthetic"

_SIBLING_DATA_ROOT = REPO_ROOT.parent / "glooko-watch-analysis" / "data"


def _resolve_local_watch_dir(data_root: Path) -> str:
    env_override = os.getenv("GLYCOLENS_LOCAL_WATCH_DIR", "").strip()
    if env_override:
        return env_override
    if data_root.is_dir():
        for child in sorted(data_root.glob("watch_data*")):
            nested = child / "watch_data"
            if nested.is_dir():
                return str(nested)
            if child.is_dir():
                return str(child)
    return str(data_root / "watch")


LOCAL_GLOOKO_DIR = (
    os.getenv("GLYCOLENS_LOCAL_GLOOKO_DIR", "").strip()
    or str(_SIBLING_DATA_ROOT / "glooko")
)
LOCAL_WATCH_DIR = _resolve_local_watch_dir(_SIBLING_DATA_ROOT)
LOCAL_WATCH_FALLBACK_DIR = str(_SIBLING_DATA_ROOT / "watch")

SYNTHETIC_PERSONA_INFOS: list[PersonaInfo] = [
    PersonaInfo(
        id="active_walker",
        title="Active Walker (AID Pump + Smartwatch)",
        subtitle="32d synthetic twin: 98% manual-unit boluses, brisk walk × IOB drops & +90 mg/dL wake surges",
        badges=["9.2k CGM", "48 Walks", "98% 0g Carbs"],
        is_local_real=False,
    ),
    PersonaInfo(
        id="hiit_lifter",
        title="HIIT & Strength Athlete (Zone-5 vs Zone-2)",
        subtitle="32d synthetic twin: +65 mg/dL evening HIIT adrenaline spikes vs -35 mg/dL morning runs",
        badges=["9.2k CGM", "22 HIIT/Lift", "20 Zone-2 Runs"],
        is_local_real=False,
    ),
    PersonaInfo(
        id="bp_shiftworker",
        title="Shift Worker (T2D Basal + Daily BP & Watch)",
        subtitle="32d synthetic twin: <5.5h shift-sleep +14 mmHg BP & +32 mg/dL fasting spikes vs dinner walks",
        badges=["9.2k CGM", "32 BP Logs", "Shift Sleep"],
        is_local_real=False,
    ),
]


def get_persona_list() -> list[PersonaInfo]:
    personas = list(SYNTHETIC_PERSONA_INFOS)
    if os.path.isdir(LOCAL_GLOOKO_DIR):
        personas.insert(
            0,
            PersonaInfo(
                id="local_real_export",
                title="Local Real Export (Omnipod 5 + Galaxy Watch)",
                subtitle="90 days real local export (gitignored, stays on device)",
                badges=["25.8k CGM", "172 Walks", "90 Sleeps"],
                is_local_real=True,
            ),
        )
    return personas


def _clamp_cgm(v: float) -> float:
    return round(max(42.0, min(385.0, v)), 1)


def _add_gaussian_bump(
    grid: dict[datetime, float],
    center: datetime,
    amp: float,
    width_min: float,
    asym_right: float = 1.35,
) -> None:
    span_m = int(max(width_min, width_min * asym_right) * 3.2)
    start = center - timedelta(minutes=(span_m // 5) * 5)
    end = center + timedelta(minutes=(span_m // 5) * 5)
    cur = start
    while cur <= end:
        if cur in grid:
            dt_m = (cur - center).total_seconds() / 60.0
            sigma = width_min if dt_m < 0 else width_min * asym_right
            grid[cur] += amp * math.exp(-0.5 * (dt_m / max(sigma, 5.0)) ** 2)
        cur += timedelta(minutes=5)


def _build_sleep_stage_cycles(
    sleep_id: str,
    start_dt: datetime,
    total_sleep_min: int,
    awake_budget_min: int,
    deep_frac: float,
    rem_frac: float,
    rng: random.Random,
) -> list[tuple[str, datetime, datetime, str, float]]:
    """Decompose an overnight sleep window into realistic 90-min sleep stage intervals."""
    total_bed_min = total_sleep_min + awake_budget_min
    n_cycles = max(3, round(total_sleep_min / 90.0))
    deep_total = round(total_sleep_min * deep_frac)
    rem_total = round(total_sleep_min * rem_frac)
    light_total = max(30, total_sleep_min - deep_total - rem_total)

    intervals: list[tuple[str, datetime, datetime, str, float]] = []
    cur = start_dt
    rem_deep = deep_total
    rem_rem = rem_total
    rem_light = light_total
    rem_awake = awake_budget_min

    for cyc in range(n_cycles):
        is_last = cyc == n_cycles - 1
        # Deep sleep concentrates in early cycles; REM concentrates in later cycles
        deep_weight = 0.45 if cyc == 0 else (0.30 if cyc == 1 else 0.25 / max(1, n_cycles - 2))
        rem_weight = 0.12 if cyc == 0 else (0.88 / max(1, n_cycles - 1))

        d_m = rem_deep if is_last else min(rem_deep, max(5, round(deep_total * deep_weight / 5) * 5))
        r_m = rem_rem if is_last else min(rem_rem, max(5, round(rem_total * rem_weight / 5) * 5))
        l_m = rem_light if is_last else min(rem_light, max(10, round(light_total / n_cycles / 5) * 5))
        aw_m = (
            rem_awake
            if is_last
            else (min(rem_awake, rng.choice([5, 10])) if rem_awake >= 5 and cyc > 0 else 0)
        )

        rem_deep -= d_m
        rem_rem -= r_m
        rem_light -= l_m
        rem_awake -= aw_m

        parts: list[tuple[str, int]] = []
        l1 = l_m // 2
        l2 = l_m - l1
        if l1 > 0:
            parts.append(("Light", l1))
        if d_m > 0:
            parts.append(("Deep", d_m))
        if l2 > 0:
            parts.append(("Light", l2))
        if r_m > 0:
            parts.append(("REM", r_m))
        if aw_m > 0:
            parts.append(("Awake", aw_m))

        for stg, mins in parts:
            nxt = cur + timedelta(minutes=mins)
            intervals.append((sleep_id, cur, nxt, stg, float(mins)))
            cur = nxt

    expected_end = start_dt + timedelta(minutes=total_bed_min)
    if cur < expected_end:
        diff = (expected_end - cur).total_seconds() / 60.0
        intervals.append((sleep_id, cur, expected_end, "Light", diff))
    return intervals


def generate_active_walker_dataset() -> CanonicalDataset:
    """Persona 1: AID pump user dialing manual bolus units (98% 0g carbs), strong brisk walk x IOB drops,
    +90 mg/dL wake surges, late-eating overnight hypos, and 80% post-hypo over-treatment rebounds.
    """
    rng = random.Random(101)
    start_d = date(2026, 10, 1)
    n_days = 32
    end_d = start_d + timedelta(days=n_days - 1)

    grid: dict[datetime, float] = {}
    t0 = datetime.combine(start_d, datetime.min.time())
    for i in range(n_days * 288):
        dt = t0 + timedelta(minutes=5 * i)
        # Mild circadian baseline around 126 mg/dL + smooth AR noise
        hr_frac = dt.hour + dt.minute / 60.0
        circ = 8.0 * math.sin((hr_frac - 5.0) / 24.0 * 2.0 * math.pi)
        grid[dt] = 126.0 + circ + rng.gauss(0.0, 2.8)

    raw_boluses: list[tuple[datetime, float, float, float, float]] = []
    raw_workouts: list[tuple[datetime, datetime, WorkoutSession]] = []
    raw_sleep_iv: list[tuple[str, datetime, datetime, str, float]] = []
    basal_events: list[BasalEvent] = []
    alarms_raw: list[tuple[datetime, DeviceAlarm]] = []
    daily_insulin: list[DailyInsulinSummary] = []
    daily_vitals: list[DailyVitalsSummary] = []

    hypo_counter = 0
    bolus_counter = 0
    illness_days = {start_d + timedelta(days=k) for k in (9, 10, 22, 23)}
    late_meal_offsets = {1, 3, 6, 8, 11, 14, 16, 19, 21, 25, 27, 30}

    for day_idx in range(n_days):
        d = start_d + timedelta(days=day_idx)
        d_base = datetime.combine(d, datetime.min.time())
        is_ill = d in illness_days
        if is_ill:
            for m_idx in range(288):
                grid[d_base + timedelta(minutes=5 * m_idx)] += 26.0

        # 1. Overnight sleep ending this morning (for day_idx >= 1)
        prev_late = (day_idx - 1) in late_meal_offsets
        if day_idx >= 1:
            short_sleep = (day_idx % 4 == 1) or prev_late
            bed_dt = (
                d_base + timedelta(minutes=rng.choice([30, 50, 75]))
                if short_sleep
                else d_base - timedelta(minutes=rng.choice([45, 30, 15]))
            )
            wake_min_of_day = rng.choice([395, 410, 425]) if short_sleep else rng.choice([435, 450, 465])
            wake_dt = d_base + timedelta(minutes=wake_min_of_day)
            tot_bed = int((wake_dt - bed_dt).total_seconds() // 60)
            awake_m = rng.choice([35, 45]) if prev_late else rng.choice([15, 20])
            tot_slp = max(210, tot_bed - awake_m)
            sid = f"aw-sleep-{day_idx:02d}"
            raw_sleep_iv.extend(
                _build_sleep_stage_cycles(
                    sid,
                    bed_dt,
                    tot_slp,
                    awake_m,
                    deep_frac=0.14 if prev_late else 0.21,
                    rem_frac=0.20 if prev_late else 0.24,
                    rng=rng,
                )
            )

            # Anchor wake CGM ~118 mg/dL and create +90 mg/dL 3h foot-on-floor + breakfast surge!
            surge_amp = rng.uniform(98.0, 114.0) if short_sleep else rng.uniform(76.0, 90.0)
            _add_gaussian_bump(
                grid,
                wake_dt + timedelta(minutes=95),
                amp=surge_amp,
                width_min=45.0,
                asym_right=1.4,
            )
            b_morn_dt = wake_dt + timedelta(minutes=rng.choice([25, 35, 45]))
            bolus_counter += 1
            carbs_m = 45.0 if bolus_counter in (17, 68, 121) else 0.0
            raw_boluses.append((b_morn_dt, round(rng.uniform(4.2, 5.8), 1), carbs_m, 0.0, 8.0))
        else:
            wake_dt = d_base + timedelta(hours=7)
            _add_gaussian_bump(grid, wake_dt + timedelta(minutes=90), amp=88.0, width_min=45.0)
            bolus_counter += 1
            raw_boluses.append((wake_dt + timedelta(minutes=30), 4.8, 0.0, 0.0, 8.0))

        # 2. Midday meal + bolus (alternate on-time vs late spike-chasing bolus)
        lunch_dt = d_base + timedelta(hours=12, minutes=rng.choice([20, 35, 50]))
        late_lunch = day_idx % 3 == 1
        if late_lunch:
            _add_gaussian_bump(grid, lunch_dt + timedelta(minutes=45), amp=108.0, width_min=40.0)
            b_lunch_dt = lunch_dt + timedelta(minutes=30)  # bolused while already rising >+25 mg/dL!
        else:
            _add_gaussian_bump(grid, lunch_dt + timedelta(minutes=55), amp=52.0, width_min=42.0)
            b_lunch_dt = lunch_dt - timedelta(minutes=5)
        bolus_counter += 1
        carbs_l = 60.0 if bolus_counter in (17, 68, 121) else 0.0
        u_lunch = round(rng.uniform(4.5, 6.8), 1)
        raw_boluses.append((b_lunch_dt, u_lunch, carbs_l, 0.0, 8.0))

        # 3. Afternoon / post-lunch Walk #1 (with active IOB ~22 days, or zero IOB late afternoon ~10 days)
        has_iob_walk = day_idx % 3 != 2
        if has_iob_walk:
            w_st = b_lunch_dt + timedelta(minutes=rng.choice([45, 55, 65]))
            dur_m = rng.choice([25, 30, 35, 40])
            steps_w = int(dur_m * rng.uniform(98.0, 112.0))
            # Steep walk x IOB drop! Some start <155 and dip <70 with 80% rebound!
            unprotected_low = day_idx in (2, 5, 12, 17, 24, 29)
            if unprotected_low:
                # Flatten lunch peak beforehand so walk starts around 138 mg/dL with high IOB -> crashes to ~58!
                _add_gaussian_bump(grid, w_st, amp=-34.0, width_min=25.0)
                drop_mag = rng.uniform(74.0, 84.0)
            else:
                drop_mag = 18.0 + 0.010 * steps_w + 6.0 * (u_lunch * 0.65)
            # Apply negative step-down across walk + 45m tail
            cur_w = w_st
            w_en = w_st + timedelta(minutes=dur_m)
            while cur_w <= w_en + timedelta(minutes=75):
                if cur_w in grid:
                    prog = min(1.0, max(0.0, (cur_w - w_st).total_seconds() / (dur_m * 60.0)))
                    tail_decay = (
                        1.0
                        if cur_w <= w_en
                        else max(0.0, 1.0 - (cur_w - w_en).total_seconds() / 5400.0)
                    )
                    grid[cur_w] -= drop_mag * prog * tail_decay
                cur_w += timedelta(minutes=5)

            if unprotected_low:
                nadir_dt = w_en + timedelta(minutes=10)
                grid[nadir_dt] = min(grid.get(nadir_dt, 60.0), rng.uniform(53.0, 63.0))
                grid[nadir_dt - timedelta(minutes=5)] = min(
                    grid.get(nadir_dt - timedelta(minutes=5), 65.0), 66.0
                )
                alarms_raw.append(
                    (
                        nadir_dt,
                        DeviceAlarm(
                            ts=nadir_dt.strftime("%Y-%m-%d %H:%M"),
                            label="dexcom_low_glucose_alert",
                            category="low_glucose",
                        ),
                    )
                )
                basal_events.append(
                    BasalEvent(
                        ts=nadir_dt.strftime("%Y-%m-%d %H:%M"),
                        event_type="Suspend",
                        duration_min=30.0,
                        rate=0.0,
                    )
                )
                hypo_counter += 1
                if hypo_counter % 5 != 0:
                    # 80% over-treatment rebound spike >235 mg/dL!
                    _add_gaussian_bump(
                        grid,
                        nadir_dt + timedelta(minutes=85),
                        amp=rng.uniform(135.0, 160.0),
                        width_min=38.0,
                    )
                    alarms_raw.append(
                        (
                            nadir_dt + timedelta(minutes=85),
                            DeviceAlarm(
                                ts=(nadir_dt + timedelta(minutes=85)).strftime("%Y-%m-%d %H:%M"),
                                label="dexcom_high_glucose_alert",
                                category="high_glucose",
                            ),
                        )
                    )
        else:
            # Zero-IOB walk around 16:45 (>4h after lunch bolus -> 0.0U IOB, mild -18 mg/dL drop)
            w_st = d_base + timedelta(hours=16, minutes=rng.choice([35, 45, 55]))
            dur_m = rng.choice([20, 25, 30])
            w_en = w_st + timedelta(minutes=dur_m)
            steps_w = int(dur_m * rng.uniform(92.0, 104.0))
            drop_mag = rng.uniform(14.0, 24.0)
            cur_w = w_st
            while cur_w <= w_en + timedelta(minutes=45):
                if cur_w in grid:
                    prog = min(1.0, (cur_w - w_st).total_seconds() / (dur_m * 60.0))
                    tail = (
                        1.0
                        if cur_w <= w_en
                        else max(0.0, 1.0 - (cur_w - w_en).total_seconds() / 3600.0)
                    )
                    grid[cur_w] -= drop_mag * prog * tail
                cur_w += timedelta(minutes=5)

        raw_workouts.append(
            (
                w_st,
                w_en,
                WorkoutSession(
                    start_ts=w_st.strftime("%Y-%m-%d %H:%M"),
                    end_ts=w_en.strftime("%Y-%m-%d %H:%M"),
                    activity_type="Walking",
                    duration_min=float(dur_m),
                    steps=steps_w,
                    cadence=round(steps_w / dur_m, 1),
                    speed_mps=round(steps_w * 0.76 / (dur_m * 60.0), 2),
                    mean_hr=round(rng.uniform(109.0, 126.0), 1),
                    max_hr=round(rng.uniform(128.0, 142.0), 1),
                    calories=round(dur_m * rng.uniform(4.4, 5.4), 1),
                ),
            )
        )

        # Extra zero-IOB morning/evening stroll on 16 days to reach 48 walks
        if day_idx % 2 == 0:
            w2_st = d_base + timedelta(hours=17, minutes=20) if has_iob_walk else d_base + timedelta(hours=11, minutes=15)
            dur2 = rng.choice([18, 22, 26])
            w2_en = w2_st + timedelta(minutes=dur2)
            steps2 = int(dur2 * rng.uniform(90.0, 102.0))
            cur_w = w2_st
            while cur_w <= w2_en + timedelta(minutes=30):
                if cur_w in grid:
                    prog = min(1.0, (cur_w - w2_st).total_seconds() / (dur2 * 60.0))
                    grid[cur_w] -= 16.0 * prog * (0.6 if cur_w > w2_en else 1.0)
                cur_w += timedelta(minutes=5)
            raw_workouts.append(
                (
                    w2_st,
                    w2_en,
                    WorkoutSession(
                        start_ts=w2_st.strftime("%Y-%m-%d %H:%M"),
                        end_ts=w2_en.strftime("%Y-%m-%d %H:%M"),
                        activity_type="Walking",
                        duration_min=float(dur2),
                        steps=steps2,
                        cadence=round(steps2 / dur2, 1),
                        speed_mps=round(steps2 * 0.74 / (dur2 * 60.0), 2),
                        mean_hr=round(rng.uniform(104.0, 118.0), 1),
                        max_hr=round(rng.uniform(120.0, 133.0), 1),
                        calories=round(dur2 * 4.2, 1),
                    ),
                )
            )

        # 4. Dinner (18:45) + Late-eating / stacked bolus nights (22:00) causing overnight hypos & 80% rebounds!
        din_dt = d_base + timedelta(hours=18, minutes=rng.choice([35, 50]))
        _add_gaussian_bump(grid, din_dt + timedelta(minutes=55), amp=58.0, width_min=45.0)
        bolus_counter += 1
        carbs_d = 55.0 if bolus_counter in (17, 68, 121) else 0.0
        u_din = round(rng.uniform(4.8, 6.4), 1)
        raw_boluses.append((din_dt, u_din, carbs_d, 0.0, 10.0))

        u_late_total = 0.0
        if day_idx in late_meal_offsets and day_idx < n_days - 1:
            late_dt = d_base + timedelta(hours=22, minutes=rng.choice([10, 25, 40]))
            _add_gaussian_bump(grid, late_dt + timedelta(minutes=35), amp=72.0, width_min=32.0)
            bolus_counter += 1
            u_l1 = round(rng.uniform(3.8, 5.0), 1)
            raw_boluses.append((late_dt, u_l1, 0.0, 0.0, 10.0))
            u_late_total += u_l1

            # Stack a correction bolus 65m later on 9 of the 12 late nights -> triggers overnight hypo!
            if day_idx in {1, 3, 6, 11, 14, 19, 21, 25, 30}:
                stack_dt = late_dt + timedelta(minutes=65)
                bolus_counter += 1
                u_l2 = round(rng.uniform(2.2, 3.2), 1)
                raw_boluses.append((stack_dt, u_l2, 0.0, 0.0, 10.0))
                u_late_total += u_l2

                hypo_dt = stack_dt + timedelta(minutes=105)
                _add_gaussian_bump(grid, hypo_dt, amp=-88.0, width_min=28.0)
                if hypo_dt in grid:
                    grid[hypo_dt] = min(grid[hypo_dt], rng.uniform(52.0, 62.0))
                if (hypo_dt + timedelta(minutes=5)) in grid:
                    grid[hypo_dt + timedelta(minutes=5)] = min(
                        grid[hypo_dt + timedelta(minutes=5)], 64.0
                    )
                alarms_raw.append(
                    (
                        hypo_dt,
                        DeviceAlarm(
                            ts=hypo_dt.strftime("%Y-%m-%d %H:%M"),
                            label="dexcom_low_glucose_alert",
                            category="low_glucose",
                        ),
                    )
                )
                basal_events.append(
                    BasalEvent(
                        ts=hypo_dt.strftime("%Y-%m-%d %H:%M"),
                        event_type="Suspend",
                        duration_min=35.0,
                        rate=0.0,
                    )
                )
                hypo_counter += 1
                if hypo_counter % 5 != 0:
                    # 80% post-hypo over-treatment rebound >230 mg/dL!
                    reb_dt = hypo_dt + timedelta(minutes=90)
                    _add_gaussian_bump(grid, reb_dt, amp=rng.uniform(142.0, 168.0), width_min=42.0)
                    alarms_raw.append(
                        (
                            reb_dt,
                            DeviceAlarm(
                                ts=reb_dt.strftime("%Y-%m-%d %H:%M"),
                                label="dexcom_high_glucose_alert",
                                category="high_glucose",
                            ),
                        )
                    )

        bas_u = round(rng.uniform(28.5, 31.5) if is_ill else rng.uniform(22.5, 25.8), 1)
        bol_u = round(5.0 + u_lunch + u_din + u_late_total, 1)
        daily_insulin.append(
            DailyInsulinSummary(
                date=d.strftime("%Y-%m-%d"),
                total_units=round(bas_u + bol_u, 1),
                basal_units=bas_u,
                bolus_units=bol_u,
            )
        )

        day_steps = sum(
            ws.steps for st_w, _, ws in raw_workouts if st_w.date() == d
        ) + rng.randint(4600, 6900)
        daily_vitals.append(
            DailyVitalsSummary(
                date=d.strftime("%Y-%m-%d"),
                steps=day_steps,
                walk_min=round(day_steps / 102.0, 1),
                run_min=0.0,
                active_calories=round(day_steps * 0.044 + rng.uniform(60.0, 110.0), 1),
                move_hourly_count=rng.randint(8, 13),
                floors=float(rng.randint(4, 14)),
                wrist_temp_c=round(rng.uniform(34.55, 34.92) if is_ill else rng.uniform(33.85, 34.22), 2),
                wrist_temp_min_c=round(rng.uniform(32.1, 32.8), 2),
                resp_rate_brpm=round(rng.uniform(15.2, 16.1) if is_ill else rng.uniform(13.8, 14.5), 2),
                pulse_bpm=round(rng.uniform(74.0, 81.0) if is_ill else rng.uniform(62.0, 69.0), 1),
                hrv_ms=round(rng.uniform(34.0, 42.0) if is_ill else rng.uniform(49.0, 64.0), 1),
            )
        )

    cgm_by_min = {dt: _clamp_cgm(v) for dt, v in grid.items()}
    glucose, boluses, b_times, b_units = enrich_glucose_and_boluses(cgm_by_min, raw_boluses)
    alarms_raw.sort(key=lambda x: x[0])
    alarm_times = [dt for dt, _ in alarms_raw]
    alarms = [ev for _, ev in alarms_raw]

    workouts = enrich_workouts_with_cgm(raw_workouts, cgm_by_min, b_times, b_units)
    sleep_stages, sleep_sessions = aggregate_and_enrich_sleep(
        raw_sleep_iv, cgm_by_min, alarm_times, start_d, end_d
    )
    walk_model = fit_walk_model_weights(workouts)

    return CanonicalDataset(
        dataset_id="active_walker",
        title="Active Walker (AID Pump + Smartwatch)",
        description=(
            "32-day synthetic AID pump + smartwatch twin featuring 98% zero-carb manual unit bolusing, "
            "steep brisk-walk × active-IOB glucose drops, +90 mg/dL post-wake surges, late-night bolus "
            "hypoglycemia, and 80% post-hypo over-treatment rebounds."
        ),
        source_tags=["glooko_cgm", "aid_pump", "galaxy_watch", "synthetic"],
        start_date=start_d.strftime("%Y-%m-%d"),
        end_date=end_d.strftime("%Y-%m-%d"),
        glucose=glucose,
        boluses=boluses,
        daily_insulin=daily_insulin,
        basal_events=sorted(basal_events, key=lambda b: b.ts),
        alarms=alarms,
        workouts=workouts,
        sleep_stages=sleep_stages,
        sleep_sessions=sleep_sessions,
        daily_vitals=daily_vitals,
        thresholds=PersonalThresholds(aerobic_hr=131, anaerobic_hr=179, max_hr=209),
        walk_model=walk_model,
    )


def generate_hiit_lifter_dataset() -> CanonicalDataset:
    """Persona 2: Athlete doing evening Zone-5 HIIT/weightlifting (HR 165-185 bpm) triggering +65 mg/dL
    adrenaline spikes vs morning Zone-2 runs dropping glucose -35 mg/dL, plus weekend sleep-in gains.
    """
    rng = random.Random(202)
    start_d = date(2026, 10, 1)
    n_days = 32
    end_d = start_d + timedelta(days=n_days - 1)

    grid: dict[datetime, float] = {}
    t0 = datetime.combine(start_d, datetime.min.time())
    for i in range(n_days * 288):
        dt = t0 + timedelta(minutes=5 * i)
        is_wknd = dt.weekday() >= 5
        base = 119.0 if is_wknd else 142.0
        hr_frac = dt.hour + dt.minute / 60.0
        circ = (5.0 if is_wknd else 12.0) * math.sin((hr_frac - 6.0) / 24.0 * 2.0 * math.pi)
        grid[dt] = base + circ + rng.gauss(0.0, 2.5)

    raw_boluses: list[tuple[datetime, float, float, float, float]] = []
    raw_workouts: list[tuple[datetime, datetime, WorkoutSession]] = []
    raw_sleep_iv: list[tuple[str, datetime, datetime, str, float]] = []
    alarms_raw: list[tuple[datetime, DeviceAlarm]] = []
    daily_insulin: list[DailyInsulinSummary] = []
    daily_vitals: list[DailyVitalsSummary] = []

    hiit_days_count = 0
    run_days_count = 0

    for day_idx in range(n_days):
        d = start_d + timedelta(days=day_idx)
        d_base = datetime.combine(d, datetime.min.time())
        is_wknd = d.weekday() >= 5

        # 1. Overnight sleep: Weekday 6.0h early alarm vs Weekend 8.2h restorative sleep-in!
        if day_idx >= 1:
            if is_wknd:
                bed_dt = d_base - timedelta(minutes=rng.choice([45, 30]))
                wake_dt = d_base + timedelta(hours=7, minutes=rng.choice([30, 45, 55]))
                awake_m = 12
                deep_f, rem_f = 0.23, 0.25
            else:
                bed_dt = d_base - timedelta(minutes=rng.choice([10, 0]))
                wake_dt = d_base + timedelta(hours=5, minutes=rng.choice([50, 55, 60]))
                awake_m = 24
                deep_f, rem_f = 0.16, 0.19
            tot_bed = int((wake_dt - bed_dt).total_seconds() // 60)
            tot_slp = max(240, tot_bed - awake_m)
            raw_sleep_iv.extend(
                _build_sleep_stage_cycles(
                    f"hl-sleep-{day_idx:02d}",
                    bed_dt,
                    tot_slp,
                    awake_m,
                    deep_frac=deep_f,
                    rem_frac=rem_f,
                    rng=rng,
                )
            )
            _add_gaussian_bump(
                grid,
                wake_dt + timedelta(minutes=85),
                amp=48.0 if is_wknd else 68.0,
                width_min=38.0,
            )
        else:
            wake_dt = d_base + timedelta(hours=6)

        # Breakfast + Lunch carb-counted boluses
        bf_dt = wake_dt + timedelta(minutes=35)
        raw_boluses.append((bf_dt, 4.5, 45.0, 0.0, 10.0))

        ln_dt = d_base + timedelta(hours=12, minutes=45)
        _add_gaussian_bump(
            grid,
            ln_dt + timedelta(minutes=50),
            amp=58.0 if is_wknd else 82.0,
            width_min=44.0,
        )
        raw_boluses.append((ln_dt - timedelta(minutes=10), 6.0, 60.0, 0.0, 10.0))

        day_run_m = 0.0
        day_walk_m = 25.0
        day_steps = rng.randint(5200, 7400)

        # 2. Morning Zone-2 Run (20 sessions across the 32 days -> drops glucose -35 mg/dL!)
        if run_days_count < 20 and (day_idx % 3 != 1 or is_wknd):
            run_days_count += 1
            r_st = (
                d_base + timedelta(hours=9, minutes=30)
                if is_wknd
                else d_base + timedelta(hours=7, minutes=35)
            )
            r_dur = rng.choice([35, 40])
            r_en = r_st + timedelta(minutes=r_dur)
            r_steps = int(r_dur * rng.uniform(158.0, 166.0))
            target_drop = rng.uniform(33.0, 38.0)

            # Anchor run window cleanly from ~152 -> ~117 mg/dL (-35 mg/dL Zone-2 aerobic drop)
            g_start_run = rng.uniform(148.0, 156.0)
            cur_r = r_st
            while cur_r <= r_en + timedelta(minutes=35):
                if cur_r in grid:
                    if cur_r <= r_en:
                        prog = (cur_r - r_st).total_seconds() / (r_dur * 60.0)
                        grid[cur_r] = g_start_run - target_drop * prog + rng.gauss(0.0, 1.5)
                    else:
                        tail = max(0.0, 1.0 - (cur_r - r_en).total_seconds() / 2400.0)
                        grid[cur_r] = (
                            (g_start_run - target_drop) * tail
                            + grid[cur_r] * (1.0 - tail)
                        )
                cur_r += timedelta(minutes=5)

            raw_workouts.append(
                (
                    r_st,
                    r_en,
                    WorkoutSession(
                        start_ts=r_st.strftime("%Y-%m-%d %H:%M"),
                        end_ts=r_en.strftime("%Y-%m-%d %H:%M"),
                        activity_type="Running",
                        duration_min=float(r_dur),
                        steps=r_steps,
                        cadence=round(r_steps / r_dur, 1),
                        speed_mps=round(rng.uniform(2.85, 3.25), 2),
                        mean_hr=round(rng.uniform(125.0, 134.0), 1),
                        max_hr=round(rng.uniform(140.0, 147.0), 1),
                        calories=round(r_dur * rng.uniform(10.2, 11.8), 1),
                    ),
                )
            )
            day_run_m += r_dur
            day_steps += r_steps

        # 3. Evening Zone-5 HIIT / Weightlifting (22 sessions -> HR 165-185 bpm triggers +65 mg/dL spike!)
        if hiit_days_count < 22 and not is_wknd:
            hiit_days_count += 1
            h_st = d_base + timedelta(hours=17, minutes=rng.choice([45, 55, 65]))
            h_dur = rng.choice([40, 45])
            h_en = h_st + timedelta(minutes=h_dur)
            act_t = "HIIT" if hiit_days_count % 2 == 1 else "Weightlifting"
            h_steps = rng.randint(1100, 1850) if act_t == "HIIT" else rng.randint(350, 680)
            spike_mag = rng.uniform(59.0, 71.0)  # mean +65 mg/dL!

            # Catecholamine surge rising across session to +65 mg/dL at h_en, peaking +10m later
            cur_h = h_st
            while cur_h <= h_en + timedelta(minutes=95):
                if cur_h in grid:
                    if cur_h <= h_en:
                        prog = (cur_h - h_st).total_seconds() / (h_dur * 60.0)
                        grid[cur_h] += spike_mag * prog
                    else:
                        over_m = (cur_h - h_en).total_seconds() / 60.0
                        decay = math.exp(-0.5 * (max(0.0, over_m - 10.0) / 35.0) ** 2)
                        grid[cur_h] += (spike_mag + 8.0) * decay
                cur_h += timedelta(minutes=5)

            ev_peak_dt = h_en + timedelta(minutes=10)
            if grid.get(ev_peak_dt, 0.0) > 195.0:
                alarms_raw.append(
                    (
                        ev_peak_dt,
                        DeviceAlarm(
                            ts=ev_peak_dt.strftime("%Y-%m-%d %H:%M"),
                            label="dexcom_high_glucose_alert",
                            category="high_glucose",
                        ),
                    )
                )

            raw_workouts.append(
                (
                    h_st,
                    h_en,
                    WorkoutSession(
                        start_ts=h_st.strftime("%Y-%m-%d %H:%M"),
                        end_ts=h_en.strftime("%Y-%m-%d %H:%M"),
                        activity_type=act_t,
                        duration_min=float(h_dur),
                        steps=h_steps,
                        cadence=round(h_steps / h_dur, 1) if act_t == "HIIT" else 0.0,
                        speed_mps=0.0,
                        mean_hr=round(rng.uniform(167.0, 182.0), 1),
                        max_hr=round(rng.uniform(184.0, 194.0), 1),
                        calories=round(h_dur * rng.uniform(11.5, 13.8), 1),
                    ),
                )
            )
            day_steps += h_steps

        # Dinner meal + bolus
        din_dt = d_base + timedelta(hours=20, minutes=10)
        _add_gaussian_bump(
            grid,
            din_dt + timedelta(minutes=50),
            amp=28.0 if is_wknd else 44.0,
            width_min=42.0,
        )
        raw_boluses.append((din_dt - timedelta(minutes=10), 6.5, 65.0, 0.0, 10.0))

        bas_u = 21.0 if is_wknd else 23.5
        bol_u = 17.0
        daily_insulin.append(
            DailyInsulinSummary(
                date=d.strftime("%Y-%m-%d"),
                total_units=round(bas_u + bol_u, 1),
                basal_units=bas_u,
                bolus_units=bol_u,
            )
        )
        daily_vitals.append(
            DailyVitalsSummary(
                date=d.strftime("%Y-%m-%d"),
                steps=day_steps,
                walk_min=round(day_walk_m, 1),
                run_min=round(day_run_m, 1),
                active_calories=round(day_steps * 0.05 + (480.0 if not is_wknd else 390.0), 1),
                move_hourly_count=rng.randint(10, 14),
                floors=float(rng.randint(8, 18)),
                wrist_temp_c=round(rng.uniform(33.9, 34.25), 2),
                wrist_temp_min_c=round(rng.uniform(32.2, 32.7), 2),
                resp_rate_brpm=round(rng.uniform(13.2, 14.1) if is_wknd else rng.uniform(14.3, 15.1), 2),
                pulse_bpm=round(rng.uniform(52.0, 57.0) if is_wknd else rng.uniform(59.0, 65.0), 1),
                hrv_ms=round(rng.uniform(66.0, 76.0) if is_wknd else rng.uniform(41.0, 49.0), 1),
            )
        )

    cgm_by_min = {dt: _clamp_cgm(v) for dt, v in grid.items()}
    glucose, boluses, b_times, b_units = enrich_glucose_and_boluses(cgm_by_min, raw_boluses)
    alarms_raw.sort(key=lambda x: x[0])
    alarm_times = [dt for dt, _ in alarms_raw]
    alarms = [ev for _, ev in alarms_raw]

    workouts = enrich_workouts_with_cgm(raw_workouts, cgm_by_min, b_times, b_units)
    sleep_stages, sleep_sessions = aggregate_and_enrich_sleep(
        raw_sleep_iv, cgm_by_min, alarm_times, start_d, end_d
    )
    walk_model = fit_walk_model_weights(workouts)

    return CanonicalDataset(
        dataset_id="hiit_lifter",
        title="HIIT & Strength Athlete (Zone-5 vs Zone-2)",
        description=(
            "32-day synthetic athlete dataset contrasting evening Zone-5 HIIT/weightlifting (HR 165-185 bpm) "
            "+65 mg/dL catecholamine spikes against morning Zone-2 aerobic runs (-35 mg/dL), plus weekend "
            "sleep-in recovery gains."
        ),
        source_tags=["glooko_cgm", "insulin_bolus", "galaxy_watch", "hr_zones", "synthetic"],
        start_date=start_d.strftime("%Y-%m-%d"),
        end_date=end_d.strftime("%Y-%m-%d"),
        glucose=glucose,
        boluses=boluses,
        daily_insulin=daily_insulin,
        basal_events=[],
        alarms=alarms,
        workouts=workouts,
        sleep_stages=sleep_stages,
        sleep_sessions=sleep_sessions,
        daily_vitals=daily_vitals,
        thresholds=PersonalThresholds(aerobic_hr=135, anaerobic_hr=168, max_hr=196),
        walk_model=walk_model,
    )


def generate_bp_shiftworker_dataset() -> CanonicalDataset:
    """Persona 3: Type 2 / basal-only smartwatch user with daily Blood Pressure logs, showing
    +14 mmHg systolic BP & +32 mg/dL fasting glucose spikes after <5.5h shift-work sleep nights,
    and post-dinner 20-min walks cutting evening meal peaks by -45 mg/dL.
    """
    rng = random.Random(303)
    start_d = date(2026, 10, 1)
    n_days = 32
    end_d = start_d + timedelta(days=n_days - 1)

    shift_short_days = {
        start_d + timedelta(days=k)
        for k in (2, 3, 6, 7, 10, 11, 15, 16, 19, 20, 24, 25, 28, 29)
    }

    grid: dict[datetime, float] = {}
    t0 = datetime.combine(start_d, datetime.min.time())
    for i in range(n_days * 288):
        dt = t0 + timedelta(minutes=5 * i)
        # Baseline around 132 mg/dL on rested days, elevated on short-sleep shift days
        is_short = dt.date() in shift_short_days
        base = 148.0 if is_short else 128.0
        grid[dt] = base + rng.gauss(0.0, 2.2)

    raw_workouts: list[tuple[datetime, datetime, WorkoutSession]] = []
    raw_sleep_iv: list[tuple[str, datetime, datetime, str, float]] = []
    alarms_raw: list[tuple[datetime, DeviceAlarm]] = []
    daily_insulin: list[DailyInsulinSummary] = []
    daily_vitals: list[DailyVitalsSummary] = []

    for day_idx in range(n_days):
        d = start_d + timedelta(days=day_idx)
        d_base = datetime.combine(d, datetime.min.time())
        is_short = d in shift_short_days

        # 1. Overnight sleep: <5.5h shift night (~4.8h) vs >=7.2h rested night (~7.5h)
        if day_idx >= 1:
            if is_short:
                bed_dt = d_base + timedelta(hours=1, minutes=rng.choice([10, 20, 30]))
                wake_dt = d_base + timedelta(hours=6, minutes=rng.choice([15, 25]))
                awake_m = 20
                deep_f, rem_f = 0.13, 0.17
            else:
                bed_dt = d_base - timedelta(hours=1, minutes=rng.choice([15, 5]))
                wake_dt = d_base + timedelta(hours=6, minutes=rng.choice([30, 45]))
                awake_m = 15
                deep_f, rem_f = 0.21, 0.23

            tot_bed = int((wake_dt - bed_dt).total_seconds() // 60)
            tot_slp = min(310, tot_bed - awake_m) if is_short else max(435, tot_bed - awake_m)
            raw_sleep_iv.extend(
                _build_sleep_stage_cycles(
                    f"bp-sleep-{day_idx:02d}",
                    bed_dt,
                    tot_slp,
                    awake_m,
                    deep_frac=deep_f,
                    rem_frac=rem_f,
                    rng=rng,
                )
            )
        else:
            wake_dt = d_base + timedelta(hours=6, minutes=30)

        # Anchor morning fasting glucose (05:30 - 09:00): ~164 mg/dL after <5.5h sleep vs ~132 mg/dL rested (+32 mg/dL!)
        fasting_target = rng.uniform(161.0, 167.0) if is_short else rng.uniform(129.0, 135.0)
        cur_f = d_base + timedelta(hours=5, minutes=30)
        while cur_f <= d_base + timedelta(hours=9):
            if cur_f in grid:
                grid[cur_f] = fasting_target + rng.gauss(0.0, 1.8)
            cur_f += timedelta(minutes=5)

        # Breakfast (09:15) & Lunch (13:15) T2D excursions (higher insulin resistance after short shift sleep!)
        _add_gaussian_bump(
            grid,
            d_base + timedelta(hours=10, minutes=10),
            amp=58.0 if is_short else 28.0,
            width_min=46.0,
        )
        _add_gaussian_bump(
            grid,
            d_base + timedelta(hours=14, minutes=10),
            amp=62.0 if is_short else 32.0,
            width_min=48.0,
        )

        # 2. Dinner at 18:45: 16 evenings WITH a 20-min post-dinner walk (-45 mg/dL peak cut!) vs 16 sedentary evenings!
        has_dinner_walk = day_idx % 2 == 0
        din_center = d_base + timedelta(hours=19, minutes=55)
        if has_dinner_walk:
            w_st = d_base + timedelta(hours=19, minutes=20)
            w_dur = rng.choice([20, 22, 24])
            w_en = w_st + timedelta(minutes=w_dur)
            w_steps = int(w_dur * rng.uniform(102.0, 112.0))

            # Meal rises higher on short-sleep resistant days (~192 mg/dL vs ~163 mg/dL rested),
            # and a 20-min post-dinner walk rescues high glucose by ~-44 mg/dL vs gentler -18 mg/dL in-range drop!
            pre_walk_amp = 44.0 if is_short else 35.0
            walk_drop_mag = 44.0 if is_short else 18.0
            _add_gaussian_bump(grid, w_st, amp=pre_walk_amp, width_min=24.0, asym_right=0.8)
            cur_w = w_st + timedelta(minutes=5)
            while cur_w <= w_en + timedelta(minutes=60):
                if cur_w in grid:
                    prog = min(1.0, (cur_w - w_st).total_seconds() / (w_dur * 60.0))
                    tail = (
                        1.0
                        if cur_w <= w_en
                        else max(0.0, 1.0 - (cur_w - w_en).total_seconds() / 3600.0)
                    )
                    grid[cur_w] -= walk_drop_mag * prog * tail
                cur_w += timedelta(minutes=5)

            raw_workouts.append(
                (
                    w_st,
                    w_en,
                    WorkoutSession(
                        start_ts=w_st.strftime("%Y-%m-%d %H:%M"),
                        end_ts=w_en.strftime("%Y-%m-%d %H:%M"),
                        activity_type="Walking",
                        duration_min=float(w_dur),
                        steps=w_steps,
                        cadence=round(w_steps / w_dur, 1),
                        speed_mps=round(w_steps * 0.74 / (w_dur * 60.0), 2),
                        mean_hr=round(rng.uniform(108.0, 117.0), 1),
                        max_hr=round(rng.uniform(122.0, 131.0), 1),
                        calories=round(w_dur * 4.5, 1),
                    ),
                )
            )
            day_steps = w_steps + rng.randint(5100, 6800)
            day_walk_m = round(w_dur + 32.0, 1)
        else:
            # Unopposed sedentary post-dinner spike peaking ~219 mg/dL (+45 mg/dL higher than walked evenings!)
            _add_gaussian_bump(
                grid,
                din_center,
                amp=81.0 if not is_short else 73.0,
                width_min=42.0,
                asym_right=1.45,
            )
            if grid.get(din_center, 0.0) > 205.0:
                alarms_raw.append(
                    (
                        din_center,
                        DeviceAlarm(
                            ts=din_center.strftime("%Y-%m-%d %H:%M"),
                            label="dexcom_high_glucose_alert",
                            category="high_glucose",
                        ),
                    )
                )
            day_steps = rng.randint(3200, 4900)
            day_walk_m = 22.0

        # Basal-only once-daily long-acting insulin (26U, zero rapid boluses)
        daily_insulin.append(
            DailyInsulinSummary(
                date=d.strftime("%Y-%m-%d"),
                total_units=26.0,
                basal_units=26.0,
                bolus_units=0.0,
            )
        )

        # Daily Blood Pressure log: +14 mmHg systolic spike after <5.5h shift-work sleep!
        sys_bp = round(rng.uniform(139.5, 144.5) if is_short else rng.uniform(125.5, 130.5), 1)
        dia_bp = round(rng.uniform(88.5, 93.5) if is_short else rng.uniform(78.0, 82.0), 1)
        pls_bpm = round(rng.uniform(79.0, 85.0) if is_short else rng.uniform(66.0, 72.0), 1)

        daily_vitals.append(
            DailyVitalsSummary(
                date=d.strftime("%Y-%m-%d"),
                steps=day_steps,
                walk_min=day_walk_m,
                run_min=0.0,
                active_calories=round(day_steps * 0.042 + 95.0, 1),
                move_hourly_count=rng.randint(5, 8) if not has_dinner_walk else rng.randint(8, 11),
                floors=float(rng.randint(3, 9)),
                wrist_temp_c=round(rng.uniform(34.35, 34.62) if is_short else rng.uniform(33.92, 34.18), 2),
                wrist_temp_min_c=round(rng.uniform(32.3, 32.9), 2),
                resp_rate_brpm=round(rng.uniform(15.1, 15.9) if is_short else rng.uniform(13.9, 14.6), 2),
                systolic_bp=sys_bp,
                diastolic_bp=dia_bp,
                pulse_bpm=pls_bpm,
                hrv_ms=round(rng.uniform(28.0, 36.0) if is_short else rng.uniform(46.0, 56.0), 1),
            )
        )

    cgm_by_min = {dt: _clamp_cgm(v) for dt, v in grid.items()}
    glucose, boluses, b_times, b_units = enrich_glucose_and_boluses(cgm_by_min, [])
    alarms_raw.sort(key=lambda x: x[0])
    alarm_times = [dt for dt, _ in alarms_raw]
    alarms = [ev for _, ev in alarms_raw]

    workouts = enrich_workouts_with_cgm(raw_workouts, cgm_by_min, b_times, b_units)
    sleep_stages, sleep_sessions = aggregate_and_enrich_sleep(
        raw_sleep_iv, cgm_by_min, alarm_times, start_d, end_d
    )
    walk_model = fit_walk_model_weights(workouts)

    return CanonicalDataset(
        dataset_id="bp_shiftworker",
        title="Shift Worker (T2D Basal + Daily BP & Watch)",
        description=(
            "32-day synthetic Type 2 basal-only smartwatch dataset with daily blood pressure logs, "
            "demonstrating +14 mmHg systolic BP and +32 mg/dL fasting glucose spikes after <5.5h "
            "shift-work sleep nights, and -45 mg/dL evening peak reductions from 20-min post-dinner walks."
        ),
        source_tags=["glooko_cgm", "basal_only", "blood_pressure", "galaxy_watch", "synthetic"],
        start_date=start_d.strftime("%Y-%m-%d"),
        end_date=end_d.strftime("%Y-%m-%d"),
        glucose=glucose,
        boluses=boluses,
        daily_insulin=daily_insulin,
        basal_events=[],
        alarms=alarms,
        workouts=workouts,
        sleep_stages=sleep_stages,
        sleep_sessions=sleep_sessions,
        daily_vitals=daily_vitals,
        thresholds=PersonalThresholds(aerobic_hr=124, anaerobic_hr=158, max_hr=184),
        walk_model=walk_model,
    )


def save_all_synthetic_personas(out_dir: Optional[Path] = None) -> list[str]:
    target_dir = out_dir or SYNTHETIC_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    builders = [
        ("active_walker", generate_active_walker_dataset),
        ("hiit_lifter", generate_hiit_lifter_dataset),
        ("bp_shiftworker", generate_bp_shiftworker_dataset),
    ]
    written: list[str] = []
    for name, fn in builders:
        ds = fn()
        out_path = target_dir / f"{name}.json"
        out_path.write_text(ds.model_dump_json(), encoding="utf-8")
        written.append(str(out_path))
    return written


if __name__ == "__main__":
    paths = save_all_synthetic_personas()
    for p in paths:
        print(f"Saved synthetic persona -> {p}")
