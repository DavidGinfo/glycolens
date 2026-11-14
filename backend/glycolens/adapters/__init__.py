from __future__ import annotations

import os
import re
import uuid
from datetime import date
from typing import Optional

from glycolens.adapters.glooko_adapter import (
    bilinear_iob_fraction,
    compute_iob_at,
    parse_glooko_export,
)
from glycolens.adapters.personas import (
    LOCAL_GLOOKO_DIR,
    LOCAL_WATCH_DIR,
    LOCAL_WATCH_FALLBACK_DIR,
    SYNTHETIC_DIR,
    generate_active_walker_dataset,
    generate_bp_shiftworker_dataset,
    generate_hiit_lifter_dataset,
    get_persona_list,
    save_all_synthetic_personas,
)
from glycolens.adapters.samsung_watch_adapter import (
    fit_walk_model_weights,
    parse_samsung_watch_export,
    parse_sh_utc_to_local,
)
from glycolens.schemas import CanonicalDataset, PersonaInfo

_SAFE_PERSONA_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def load_glooko_and_watch_zips(
    glooko_bytes: bytes | None = None,
    watch_bytes: bytes | None = None,
    glooko_dir: str | None = None,
    watch_dir: str | None = None,
    title: str = "Uploaded Export",
) -> CanonicalDataset:
    """Parse and fuse Glooko CGM/pump exports with Samsung Health smartwatch exports."""
    glooko_data = parse_glooko_export(glooko_bytes=glooko_bytes, glooko_dir=glooko_dir)

    watch_data = parse_samsung_watch_export(
        watch_bytes=watch_bytes,
        watch_dir=watch_dir,
        cgm_by_min=glooko_data.cgm_by_min,
        bolus_times=glooko_data.bolus_times,
        bolus_units=glooko_data.bolus_units,
        alarm_times=glooko_data.alarm_times,
        start_date_filter=glooko_data.start_date,
        end_date_filter=glooko_data.end_date,
    )

    has_any_records = bool(
        glooko_data.glucose
        or glooko_data.boluses
        or glooko_data.daily_insulin
        or glooko_data.basal_events
        or glooko_data.alarms
        or watch_data.workouts
        or watch_data.sleep_sessions
        or watch_data.daily_vitals
    )
    if not has_any_records:
        raise ValueError("No valid Glooko or smartwatch records found in uploaded archive(s).")

    source_tags: list[str] = []
    if glooko_data.glucose:
        source_tags.append("glooko_cgm")
    if glooko_data.boluses or glooko_data.daily_insulin:
        source_tags.append("insulin_pump")
    if (
        watch_data.workouts
        or watch_data.sleep_sessions
        or watch_data.daily_vitals
    ):
        source_tags.append("samsung_watch")
    if any(v.systolic_bp is not None for v in watch_data.daily_vitals):
        source_tags.append("blood_pressure")

    start_d: Optional[date] = glooko_data.start_date or watch_data.start_date
    end_d: Optional[date] = glooko_data.end_date or watch_data.end_date
    today_str = date.today().strftime("%Y-%m-%d")
    start_str = start_d.strftime("%Y-%m-%d") if start_d else today_str
    end_str = end_d.strftime("%Y-%m-%d") if end_d else today_str

    walk_model = fit_walk_model_weights(watch_data.workouts)

    dataset_id = f"upload_{uuid.uuid4().hex[:8]}"
    description = (
        f"Fused multimodal export ({start_str} to {end_str}): "
        f"{len(glooko_data.glucose)} CGM points, {len(glooko_data.boluses)} boluses, "
        f"{len(watch_data.workouts)} workouts, {len(watch_data.sleep_sessions)} overnight sleeps."
    )

    return CanonicalDataset(
        dataset_id=dataset_id,
        title=title,
        description=description,
        source_tags=source_tags,
        start_date=start_str,
        end_date=end_str,
        glucose=glooko_data.glucose,
        boluses=glooko_data.boluses,
        daily_insulin=glooko_data.daily_insulin,
        basal_events=glooko_data.basal_events,
        alarms=glooko_data.alarms,
        workouts=watch_data.workouts,
        sleep_stages=watch_data.sleep_stages,
        sleep_sessions=watch_data.sleep_sessions,
        daily_vitals=watch_data.daily_vitals,
        thresholds=watch_data.thresholds,
        walk_model=walk_model,
    )


def list_available_personas() -> list[PersonaInfo]:
    """Return available synthetic personas, prepending local_real_export when present on disk."""
    return get_persona_list()


def load_persona_dataset(persona_id: str) -> CanonicalDataset:
    """Load either the local real export dynamically or a pre-generated synthetic persona JSON."""
    if persona_id == "local_real_export":
        if not os.path.isdir(LOCAL_GLOOKO_DIR):
            raise FileNotFoundError(
                f"Local Glooko export directory not found at {LOCAL_GLOOKO_DIR}"
            )
        w_dir = (
            LOCAL_WATCH_DIR
            if os.path.isdir(LOCAL_WATCH_DIR)
            else (LOCAL_WATCH_FALLBACK_DIR if os.path.isdir(LOCAL_WATCH_FALLBACK_DIR) else None)
        )
        ds = load_glooko_and_watch_zips(
            glooko_dir=LOCAL_GLOOKO_DIR,
            watch_dir=w_dir,
            title="Local Real Export (Omnipod 5 + Galaxy Watch)",
        )
        return ds.model_copy(
            update={
                "dataset_id": "local_real_export",
                "description": "90 days real local export (gitignored, stays on device)",
            }
        )

    if not _SAFE_PERSONA_RE.match(persona_id):
        raise ValueError(f"Invalid persona_id: {persona_id!r}")

    json_path = SYNTHETIC_DIR / f"{persona_id}.json"
    if json_path.is_file():
        return CanonicalDataset.model_validate_json(json_path.read_text(encoding="utf-8"))

    fallback_generators = {
        "active_walker": generate_active_walker_dataset,
        "hiit_lifter": generate_hiit_lifter_dataset,
        "bp_shiftworker": generate_bp_shiftworker_dataset,
    }
    if persona_id in fallback_generators:
        return fallback_generators[persona_id]()

    raise FileNotFoundError(f"Unknown persona_id {persona_id!r} (looked in {json_path})")


__all__ = [
    "bilinear_iob_fraction",
    "compute_iob_at",
    "fit_walk_model_weights",
    "list_available_personas",
    "load_glooko_and_watch_zips",
    "load_persona_dataset",
    "parse_sh_utc_to_local",
    "save_all_synthetic_personas",
]
