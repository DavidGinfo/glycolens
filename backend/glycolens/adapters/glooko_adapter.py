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

from glycolens.schemas import (
    BasalEvent,
    BolusEvent,
    DailyInsulinSummary,
    DeviceAlarm,
    GlucosePoint,
)

DIA_HOURS: float = 3.5
PEAK_DECAY_HOUR: float = 1.0
PEAK_REMAINING_FRAC: float = 0.75


def bilinear_iob_fraction(dt_hours: float, dia_hours: float = DIA_HOURS) -> float:
    """Two-segment bilinear insulin-on-board decay over 3.5 hours.

    Models slower initial subcutaneous absorption during hour 0->1 (25% cleared)
    followed by linear active clearance from hour 1->3.5 (remaining 75% cleared).
    """
    if dt_hours < 0.0 or dt_hours >= dia_hours:
        return 0.0
    if dt_hours <= PEAK_DECAY_HOUR:
        return 1.0 - (1.0 - PEAK_REMAINING_FRAC) * (dt_hours / PEAK_DECAY_HOUR)
    remaining_span = max(dia_hours - PEAK_DECAY_HOUR, 1e-6)
    return PEAK_REMAINING_FRAC * (dia_hours - dt_hours) / remaining_span


def compute_iob_at(
    target_dt: datetime,
    bolus_times: list[datetime],
    bolus_units: list[float],
    dia_hours: float = DIA_HOURS,
) -> float:
    """Fast O(log N) active bolus IOB lookup using sorted bolus timestamps."""
    if not bolus_times:
        return 0.0
    lo = bisect_left(bolus_times, target_dt - timedelta(hours=dia_hours))
    hi = bisect_right(bolus_times, target_dt)
    total = 0.0
    for idx in range(lo, hi):
        dt_h = (target_dt - bolus_times[idx]).total_seconds() / 3600.0
        if 0.0 <= dt_h < dia_hours:
            total += bolus_units[idx] * bilinear_iob_fraction(dt_h, dia_hours)
    return round(total, 2)


def snap_5min(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)


def lookup_cgm_near(
    cgm_by_min: dict[datetime, float],
    target_dt: datetime,
    max_tolerance_min: int = 10,
) -> Optional[float]:
    grid = snap_5min(target_dt)
    if grid in cgm_by_min:
        return cgm_by_min[grid]
    steps = max_tolerance_min // 5
    for step in range(1, steps + 1):
        delta = timedelta(minutes=5 * step)
        before = grid - delta
        after = grid + delta
        if before in cgm_by_min and after in cgm_by_min:
            if abs((target_dt - before).total_seconds()) <= abs((after - target_dt).total_seconds()):
                return cgm_by_min[before]
            return cgm_by_min[after]
        if before in cgm_by_min:
            return cgm_by_min[before]
        if after in cgm_by_min:
            return cgm_by_min[after]
    return None


def parse_glooko_timestamp(raw: str) -> Optional[datetime]:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


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


def _iter_csv_texts_from_source(
    raw_bytes: Optional[bytes] = None,
    dir_path: Optional[str] = None,
) -> Iterable[tuple[str, str]]:
    """Yield (relative_path, decoded_text) for all CSV files in zip bytes or directory."""
    if raw_bytes is not None:
        buf = io.BytesIO(raw_bytes)
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
                        text = zf.read(info.filename).decode("utf-8-sig", errors="replace")
                        yield name, text
                    elif base.lower().endswith(".zip"):
                        nested_bytes = zf.read(info.filename)
                        yield from _iter_csv_texts_from_source(raw_bytes=nested_bytes)
        else:
            text = raw_bytes.decode("utf-8-sig", errors="replace")
            yield "uploaded_cgm_data_1.csv", text

    if dir_path and os.path.isdir(dir_path):
        for root, _, files in os.walk(dir_path):
            for fname in sorted(files):
                if fname.startswith("._") or fname.startswith(".") or "(1)" in fname:
                    continue
                if fname.lower().endswith(".csv"):
                    full = os.path.join(root, fname)
                    rel = os.path.relpath(full, dir_path)
                    with open(full, "r", encoding="utf-8-sig", errors="replace") as f:
                        yield rel, f.read()


def _parse_glooko_csv_rows(text: str) -> list[dict[str, str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    first_cells = next(csv.reader([lines[0]]), [])
    first_col = first_cells[0].lstrip("\ufeff").strip() if first_cells else ""
    start_idx = 0 if first_col == "Timestamp" else 1
    if len(lines) <= start_idx:
        return []
    reader = csv.DictReader(lines[start_idx:])
    out: list[dict[str, str]] = []
    for row in reader:
        if not row:
            continue
        cleaned = {
            (k.strip() if k else ""): (v.strip() if isinstance(v, str) else "")
            for k, v in row.items()
            if k is not None
        }
        out.append(cleaned)
    return out


def categorize_alarm(label: str) -> str:
    low = label.lower()
    if "low_glucose" in low or "urgent_low" in low or "below configured" in low or "hypo" in low:
        return "low_glucose"
    if "high_glucose" in low or "above configured" in low or "hyper" in low:
        return "high_glucose"
    if "pod" in low and ("expir" in low or "change pod" in low):
        return "pod_expiration"
    if (
        "signal_loss" in low
        or "not received" in low
        or "sensor_issue" in low
        or "no readings" in low
    ):
        return "signal_loss"
    return "other"


def normalize_basal_type(raw_type: str) -> str:
    low = (raw_type or "").strip().lower()
    if "suspend" in low:
        return "Suspend"
    if "temp" in low:
        return "Temporary"
    return "Scheduled"


def enrich_glucose_and_boluses(
    cgm_by_min: dict[datetime, float],
    raw_boluses: list[tuple[datetime, float, float, float, float]],
) -> tuple[list[GlucosePoint], list[BolusEvent], list[datetime], list[float]]:
    """Compute slope_15m & bilinear 3.5h IOB on GlucosePoint, and prior_25m_rise & glucose_at_bolus on BolusEvent."""
    raw_boluses_sorted = sorted(raw_boluses, key=lambda x: x[0])

    active_bolus_times: list[datetime] = []
    active_bolus_units: list[float] = []
    bolus_events: list[BolusEvent] = []

    for dt, units, carbs_g, bg_input, carb_ratio in raw_boluses_sorted:
        if units > 0.0:
            active_bolus_times.append(dt)
            active_bolus_units.append(units)

        g_now = lookup_cgm_near(cgm_by_min, dt, max_tolerance_min=10)
        g_prev25 = lookup_cgm_near(cgm_by_min, dt - timedelta(minutes=25), max_tolerance_min=5)
        prior_rise = (
            round(g_now - g_prev25, 1)
            if (g_now is not None and g_prev25 is not None)
            else 0.0
        )
        bolus_events.append(
            BolusEvent(
                ts=dt.strftime("%Y-%m-%d %H:%M"),
                units=round(units, 2),
                carbs_g=round(carbs_g, 1),
                bg_input=round(bg_input, 1),
                carb_ratio=round(carb_ratio, 2),
                prior_25m_rise=prior_rise,
                glucose_at_bolus=round(g_now, 1) if g_now is not None else None,
            )
        )

    glucose_points: list[GlucosePoint] = []
    for dt in sorted(cgm_by_min.keys()):
        val = round(cgm_by_min[dt], 1)
        prev_15 = cgm_by_min.get(dt - timedelta(minutes=15))
        if prev_15 is not None:
            slope_15m = round(val - prev_15, 1)
        elif (dt - timedelta(minutes=10)) in cgm_by_min:
            slope_15m = round((val - cgm_by_min[dt - timedelta(minutes=10)]) * 1.5, 1)
        elif (dt - timedelta(minutes=20)) in cgm_by_min:
            slope_15m = round((val - cgm_by_min[dt - timedelta(minutes=20)]) * 0.75, 1)
        else:
            slope_15m = 0.0

        iob_u = compute_iob_at(dt, active_bolus_times, active_bolus_units, DIA_HOURS)
        glucose_points.append(
            GlucosePoint(
                ts=dt.strftime("%Y-%m-%d %H:%M"),
                mg_dl=val,
                slope_15m=slope_15m,
                iob_units=iob_u,
            )
        )

    return glucose_points, bolus_events, active_bolus_times, active_bolus_units


class GlookoParsedData:
    def __init__(
        self,
        cgm_by_min: dict[datetime, float],
        glucose: list[GlucosePoint],
        boluses: list[BolusEvent],
        bolus_times: list[datetime],
        bolus_units: list[float],
        daily_insulin: list[DailyInsulinSummary],
        basal_events: list[BasalEvent],
        alarms: list[DeviceAlarm],
        alarm_times: list[datetime],
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> None:
        self.cgm_by_min = cgm_by_min
        self.glucose = glucose
        self.boluses = boluses
        self.bolus_times = bolus_times
        self.bolus_units = bolus_units
        self.daily_insulin = daily_insulin
        self.basal_events = basal_events
        self.alarms = alarms
        self.alarm_times = alarm_times
        self.start_date = start_date
        self.end_date = end_date


def parse_glooko_export(
    glooko_bytes: Optional[bytes] = None,
    glooko_dir: Optional[str] = None,
) -> GlookoParsedData:
    cgm_buckets: dict[datetime, list[float]] = defaultdict(list)
    seen_boluses: set[tuple[str, float, float]] = set()
    raw_boluses: list[tuple[datetime, float, float, float, float]] = []
    raw_daily_ins: list[tuple[datetime, float, float, float]] = []
    seen_basal: set[tuple[str, str, float, float]] = set()
    basal_events_raw: list[tuple[datetime, BasalEvent]] = []
    seen_alarms: set[tuple[str, str]] = set()
    alarms_raw: list[tuple[datetime, DeviceAlarm]] = []

    for rel_path, text in _iter_csv_texts_from_source(glooko_bytes, glooko_dir):
        base = os.path.basename(rel_path).lower()
        rows = _parse_glooko_csv_rows(text)
        if not rows:
            continue

        if base.startswith("cgm_data") and not base.startswith("cgm_carbs"):
            for r in rows:
                ts = parse_glooko_timestamp(r.get("Timestamp", ""))
                if ts is None:
                    continue
                val_str = (
                    r.get("CGM Glucose Value (mg/dl)")
                    or r.get("CGM Glucose Value (mg/dL)")
                    or ""
                ).strip()
                if not val_str:
                    continue
                try:
                    val = float(val_str)
                except ValueError:
                    continue
                if 20.0 <= val <= 600.0:
                    cgm_buckets[snap_5min(ts)].append(val)

        elif base.startswith("bolus_data"):
            for r in rows:
                ts = parse_glooko_timestamp(r.get("Timestamp", ""))
                if ts is None:
                    continue
                units = _safe_float(r.get("Insulin Delivered (U)"))
                carbs = _safe_float(r.get("Carbs Input (g)"))
                bg_in = _safe_float(
                    r.get("Blood Glucose Input (mg/dl)")
                    or r.get("Blood Glucose Input (mg/dL)")
                )
                cr = _safe_float(r.get("Carbs Ratio"))
                if units <= 0.0 and carbs <= 0.0:
                    continue
                key = (ts.strftime("%Y-%m-%d %H:%M"), round(units, 2), round(carbs, 1))
                if key not in seen_boluses:
                    seen_boluses.add(key)
                    raw_boluses.append((ts, units, carbs, bg_in, cr))

        elif base.startswith("insulin_data"):
            for r in rows:
                ts = parse_glooko_timestamp(r.get("Timestamp", ""))
                if ts is None:
                    continue
                tot = _safe_float(r.get("Total Insulin (U)"))
                bas = _safe_float(r.get("Total Basal (U)"))
                bol = _safe_float(r.get("Total Bolus (U)"))
                if tot > 0.0 or bas > 0.0 or bol > 0.0:
                    if tot <= 0.0:
                        tot = bas + bol
                    raw_daily_ins.append((ts, tot, bas, bol))

        elif base.startswith("basal_data"):
            for r in rows:
                ts = parse_glooko_timestamp(r.get("Timestamp", ""))
                if ts is None:
                    continue
                ev_type = normalize_basal_type(r.get("Insulin Type", ""))
                dur = _safe_float(r.get("Duration (minutes)"))
                rate = _safe_float(r.get("Rate"))
                ts_str = ts.strftime("%Y-%m-%d %H:%M")
                key = (ts_str, ev_type, round(dur, 1), round(rate, 3))
                if key not in seen_basal:
                    seen_basal.add(key)
                    basal_events_raw.append(
                        (
                            ts,
                            BasalEvent(
                                ts=ts_str,
                                event_type=ev_type,
                                duration_min=round(dur, 1),
                                rate=round(rate, 3),
                            ),
                        )
                    )

        elif base.startswith("alarms_data"):
            for r in rows:
                ts = parse_glooko_timestamp(r.get("Timestamp", ""))
                label = (r.get("Alarm/Event") or "").strip()
                if ts is None or not label:
                    continue
                ts_str = ts.strftime("%Y-%m-%d %H:%M")
                key = (ts_str, label)
                if key not in seen_alarms:
                    seen_alarms.add(key)
                    alarms_raw.append(
                        (
                            ts,
                            DeviceAlarm(
                                ts=ts_str,
                                label=label,
                                category=categorize_alarm(label),  # type: ignore[arg-type]
                            ),
                        )
                    )

    cgm_by_min: dict[datetime, float] = {
        dt: round(statistics.mean(vals), 1) for dt, vals in cgm_buckets.items()
    }

    glucose, boluses, bolus_times, bolus_units = enrich_glucose_and_boluses(
        cgm_by_min, raw_boluses
    )

    has_23h_rows = any(ts.hour == 23 for ts, _, _, _ in raw_daily_ins)
    ins_by_date: dict[date, tuple[datetime, float, float, float]] = {}
    for ts, tot, bas, bol in raw_daily_ins:
        if has_23h_rows and ts.hour != 23:
            continue
        d = ts.date()
        prev = ins_by_date.get(d)
        if prev is None or (ts, tot) > (prev[0], prev[1]):
            ins_by_date[d] = (ts, tot, bas, bol)

    daily_insulin = [
        DailyInsulinSummary(
            date=d.strftime("%Y-%m-%d"),
            total_units=round(vals[1], 2),
            basal_units=round(vals[2], 2),
            bolus_units=round(vals[3], 2),
        )
        for d, vals in sorted(ins_by_date.items())
    ]

    basal_events_raw.sort(key=lambda x: x[0])
    basal_events = [ev for _, ev in basal_events_raw]

    alarms_raw.sort(key=lambda x: x[0])
    alarm_times = [dt for dt, _ in alarms_raw]
    alarms = [ev for _, ev in alarms_raw]

    if cgm_by_min:
        start_d: Optional[date] = min(cgm_by_min.keys()).date()
        end_d: Optional[date] = max(cgm_by_min.keys()).date()
    elif bolus_times:
        start_d = min(bolus_times).date()
        end_d = max(bolus_times).date()
    else:
        start_d = None
        end_d = None

    return GlookoParsedData(
        cgm_by_min=cgm_by_min,
        glucose=glucose,
        boluses=boluses,
        bolus_times=bolus_times,
        bolus_units=bolus_units,
        daily_insulin=daily_insulin,
        basal_events=basal_events,
        alarms=alarms,
        alarm_times=alarm_times,
        start_date=start_d,
        end_date=end_d,
    )
