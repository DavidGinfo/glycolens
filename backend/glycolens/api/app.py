from __future__ import annotations

import asyncio
import inspect
import json
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from glycolens.adapters import (
    list_available_personas,
    load_glooko_and_watch_zips,
    load_persona_dataset,
)
from glycolens.agents import (
    answer_data_question,
    get_gemini_config,
    has_gemini_api_key,
    set_runtime_api_key,
    stream_autonomous_discovery,
)
from glycolens.api.store import SessionRecord, session_store
from glycolens.schemas import (
    BasalEvent,
    BolusEvent,
    CanonicalDataset,
    ChatRequest,
    ChatResponse,
    DailyVitalsSummary,
    DataProfile,
    DayReplay,
    DeviceAlarm,
    DiscoveryReport,
    GlucosePoint,
    PersonaInfo,
    SleepSessionSummary,
    SwarmStreamEvent,
    WalkSimulationRequest,
    WalkSimulationResponse,
    WorkoutSession,
)
from glycolens.stats import (
    build_data_profile,
    build_day_replays,
    simulate_walk,
)

PERSIST_ENV_PATH = str(Path(__file__).resolve().parents[2] / ".env")
load_dotenv(PERSIST_ENV_PATH, override=False)

_ITER_SENTINEL = object()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ApiKeyRequest(BaseModel):
    api_key: str


class SessionBundleResponse(BaseModel):
    session_id: str
    profile: DataProfile
    replays: list[DayReplay]


class PeriodicSyncBatchRequest(BaseModel):
    """Pluggable webhook payload for periodic CGM / wearable cloud API sync."""

    model_config = ConfigDict(extra="allow")

    session_id: Optional[str] = None
    source: str = "cloud_wearable_cgm_sync"
    device_id: Optional[str] = None
    glucose: list[GlucosePoint] = Field(default_factory=list)
    boluses: list[BolusEvent] = Field(default_factory=list)
    basal_events: list[BasalEvent] = Field(default_factory=list)
    alarms: list[DeviceAlarm] = Field(default_factory=list)
    workouts: list[WorkoutSession] = Field(default_factory=list)
    sleep_sessions: list[SleepSessionSummary] = Field(default_factory=list)
    daily_vitals: list[DailyVitalsSummary] = Field(default_factory=list)


class PeriodicSyncBatchResponse(BaseModel):
    status: str = "accepted"
    sync_id: str
    received_at: str
    source: str
    session_id: Optional[str] = None
    session_updated: bool = False
    records_received: dict[str, int]
    total_records: int
    next_poll_seconds: int = 300


app = FastAPI(
    title="GlycoLens API",
    description="Headless FastAPI + SSE backend for GlycoLens autonomous metabolic pattern discovery.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _call_maybe_async(fn: Any, *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    result = await asyncio.to_thread(fn, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _require_session(session_id: str) -> SessionRecord:
    record = session_store.get_session(session_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found.",
        )
    return record


async def _build_health_dict() -> dict[str, Any]:
    raw_cfg = await _call_maybe_async(get_gemini_config)
    if isinstance(raw_cfg, BaseModel):
        cfg_dict = raw_cfg.model_dump()
    elif isinstance(raw_cfg, dict):
        cfg_dict = dict(raw_cfg)
    else:
        cfg_dict = {"config": raw_cfg}

    has_key = bool(await _call_maybe_async(has_gemini_api_key))
    if "has_api_key" not in cfg_dict:
        cfg_dict["has_api_key"] = has_key

    return {
        **cfg_dict,
        "status": "ok",
    }


def _load_zips_flexible(
    glooko_bytes: bytes,
    watch_bytes: Optional[bytes],
    title: str,
) -> CanonicalDataset:
    """Invoke load_glooko_and_watch_zips whether it expects file paths or raw bytes."""
    sig = inspect.signature(load_glooko_and_watch_zips)
    params = list(sig.parameters.values())
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

    extra_kwargs: dict[str, Any] = {}
    if "title" in sig.parameters or has_var_kw:
        extra_kwargs["title"] = title

    first_name = params[0].name.lower() if params else ""
    first_ann = str(params[0].annotation) if params else ""

    prefers_bytes = "bytes" in first_name or (
        "bytes" in first_ann and "Path" not in first_ann and "str" not in first_ann
    )
    prefers_str_only = (
        "str" in first_ann and "Path" not in first_ann and "bytes" not in first_ann
    )

    with tempfile.TemporaryDirectory(prefix="glycolens_upload_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        glooko_path = tmp_root / "glooko_upload.zip"
        glooko_path.write_bytes(glooko_bytes)

        watch_path: Optional[Path] = None
        if watch_bytes:
            watch_path = tmp_root / "watch_upload.zip"
            watch_path.write_bytes(watch_bytes)

        path_args: tuple[Any, Any]
        if prefers_str_only:
            path_args = (str(glooko_path), str(watch_path) if watch_path else None)
        else:
            path_args = (glooko_path, watch_path)
        byte_args = (glooko_bytes, watch_bytes)

        attempts = [byte_args, path_args] if prefers_bytes else [path_args, byte_args]
        last_type_err: Optional[TypeError] = None
        ds: Optional[CanonicalDataset] = None

        for g_arg, w_arg in attempts:
            try:
                ds = load_glooko_and_watch_zips(g_arg, w_arg, **extra_kwargs)
                break
            except TypeError as exc:
                last_type_err = exc

        if ds is None:
            assert last_type_err is not None
            raise last_type_err

    if title and ds.title != title:
        ds = ds.model_copy(update={"title": title})
    return ds


def _next_or_sentinel(iterator: Any) -> Any:
    return next(iterator, _ITER_SENTINEL)


def _open_discovery_stream(
    session_id: str,
    dataset: CanonicalDataset,
    profile: DataProfile,
) -> Any:
    sig = inspect.signature(stream_autonomous_discovery)
    params = list(sig.parameters.values())
    if params and params[0].name in {"ds", "dataset"}:
        if len(params) >= 3:
            return stream_autonomous_discovery(dataset, profile, session_id)
        return stream_autonomous_discovery(dataset, profile)
    return stream_autonomous_discovery(session_id, dataset, profile)


def _serialize_sse_event(
    session_id: str,
    event: Any,
) -> str:
    if isinstance(event, SwarmStreamEvent):
        if event.report is not None:
            report = event.report
            if not report.session_id:
                report = report.model_copy(update={"session_id": session_id})
            session_store.set_report(session_id, report)
        return event.model_dump_json()

    if isinstance(event, BaseModel):
        maybe_report = getattr(event, "report", None)
        if isinstance(maybe_report, DiscoveryReport):
            session_store.set_report(session_id, maybe_report)
        return event.model_dump_json()

    if isinstance(event, dict):
        raw_report = event.get("report")
        if raw_report is not None:
            report = (
                raw_report
                if isinstance(raw_report, DiscoveryReport)
                else DiscoveryReport.model_validate(raw_report)
            )
            session_store.set_report(session_id, report)
        return json.dumps(event)

    if isinstance(event, str):
        stripped = event.strip()
        if stripped.startswith("data:"):
            stripped = stripped[5:].strip()
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict) and parsed.get("report"):
                session_store.set_report(
                    session_id, DiscoveryReport.model_validate(parsed["report"])
                )
        except json.JSONDecodeError:
            pass
        return stripped

    return json.dumps({"event": str(event)})


@app.get("/api/health")
async def get_health() -> dict[str, Any]:
    return await _build_health_dict()


@app.post("/api/config/apikey")
async def configure_api_key(req: ApiKeyRequest) -> dict[str, Any]:
    cleaned_key = req.api_key.strip()
    sig = inspect.signature(set_runtime_api_key)
    has_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if "persist_env_path" in sig.parameters or has_var_kw:
        await _call_maybe_async(
            set_runtime_api_key,
            cleaned_key,
            persist_env_path=PERSIST_ENV_PATH,
        )
    elif len(sig.parameters) >= 2:
        await _call_maybe_async(set_runtime_api_key, cleaned_key, PERSIST_ENV_PATH)
    else:
        await _call_maybe_async(set_runtime_api_key, cleaned_key)
    return await _build_health_dict()


@app.get("/api/personas", response_model=list[PersonaInfo])
async def get_personas() -> list[PersonaInfo]:
    return await _call_maybe_async(list_available_personas)


@app.post("/api/sessions/persona/{persona_id}", response_model=SessionBundleResponse)
async def create_persona_session(persona_id: str) -> SessionBundleResponse:
    try:
        ds = await _call_maybe_async(load_persona_dataset, persona_id)
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    profile = await _call_maybe_async(build_data_profile, ds)
    replays = await _call_maybe_async(build_day_replays, ds)
    record = session_store.create_session(dataset=ds, profile=profile, replays=replays)
    return SessionBundleResponse(
        session_id=record.session_id,
        profile=record.profile,
        replays=record.replays,
    )


@app.post("/api/sessions/upload", response_model=SessionBundleResponse)
async def create_upload_session(
    glooko_file: UploadFile = File(...),
    watch_file: Optional[UploadFile] = File(None),
    title: str = Form("Custom Upload"),
) -> SessionBundleResponse:
    glooko_bytes = await glooko_file.read()
    if not glooko_bytes:
        raise HTTPException(
            status_code=400, detail="Uploaded Glooko ZIP file is empty."
        )

    watch_bytes: Optional[bytes] = None
    if watch_file is not None:
        raw_watch = await watch_file.read()
        if raw_watch:
            watch_bytes = raw_watch

    clean_title = (title or "Custom Upload").strip() or "Custom Upload"

    try:
        ds = await _call_maybe_async(
            _load_zips_flexible, glooko_bytes, watch_bytes, clean_title
        )
    except (zipfile.BadZipFile, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unable to parse uploaded archive(s): {exc}",
        ) from exc

    profile = await _call_maybe_async(build_data_profile, ds)
    replays = await _call_maybe_async(build_day_replays, ds)
    record = session_store.create_session(dataset=ds, profile=profile, replays=replays)
    return SessionBundleResponse(
        session_id=record.session_id,
        profile=record.profile,
        replays=record.replays,
    )


@app.get("/api/sessions/{session_id}/profile", response_model=DataProfile)
async def get_session_profile(session_id: str) -> DataProfile:
    record = _require_session(session_id)
    return record.profile


@app.get("/api/sessions/{session_id}/replays", response_model=list[DayReplay])
async def get_session_replays(session_id: str) -> list[DayReplay]:
    record = _require_session(session_id)
    return record.replays


@app.get("/api/sessions/{session_id}/discover/stream")
async def stream_session_discovery(session_id: str) -> StreamingResponse:
    record = _require_session(session_id)
    ds = record.dataset
    profile = record.profile

    async def sse_generator() -> AsyncIterator[str]:
        try:
            stream_obj = _open_discovery_stream(session_id, ds, profile)
            if inspect.isawaitable(stream_obj) and not inspect.isasyncgen(stream_obj):
                stream_obj = await stream_obj

            if inspect.isasyncgen(stream_obj) or hasattr(stream_obj, "__aiter__"):
                async for event in stream_obj:
                    payload = _serialize_sse_event(session_id, event)
                    yield f"data: {payload}\n\n"
            else:
                iterator = iter(stream_obj)
                while True:
                    event = await asyncio.to_thread(_next_or_sentinel, iterator)
                    if event is _ITER_SENTINEL:
                        break
                    payload = _serialize_sse_event(session_id, event)
                    yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            err_event = SwarmStreamEvent(
                event_type="error",
                agent_name="api_stream",
                status="error",
                message=f"Discovery stream error: {exc}",
            )
            yield f"data: {err_event.model_dump_json()}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.get("/api/sessions/{session_id}/report", response_model=DiscoveryReport)
async def get_session_report(session_id: str) -> DiscoveryReport:
    record = _require_session(session_id)
    if record.report is None:
        raise HTTPException(
            status_code=404,
            detail=f"Discovery report not yet generated for session '{session_id}'.",
        )
    return record.report


@app.post(
    "/api/sessions/{session_id}/simulate/walk",
    response_model=WalkSimulationResponse,
)
async def run_walk_simulation(
    session_id: str,
    req: WalkSimulationRequest,
) -> WalkSimulationResponse:
    record = _require_session(session_id)
    return await _call_maybe_async(simulate_walk, record.dataset, req)


@app.post("/api/sessions/{session_id}/chat", response_model=ChatResponse)
async def chat_with_session(
    session_id: str,
    req: ChatRequest,
) -> ChatResponse:
    record = _require_session(session_id)
    return await _call_maybe_async(
        answer_data_question,
        record.dataset,
        record.profile,
        record.report,
        req,
    )


def _merge_by_key(existing: list[Any], incoming: list[Any], key_attr: str) -> list[Any]:
    if not incoming:
        return existing
    merged = {getattr(item, key_attr): item for item in existing}
    for item in incoming:
        merged[getattr(item, key_attr)] = item
    return sorted(merged.values(), key=lambda x: getattr(x, key_attr))


@app.post("/api/webhooks/periodic-sync", response_model=PeriodicSyncBatchResponse)
async def receive_periodic_sync(
    batch: PeriodicSyncBatchRequest,
) -> PeriodicSyncBatchResponse:
    sync_id = f"sync_{uuid.uuid4().hex[:10]}"
    received_at = datetime.now(timezone.utc).isoformat()

    counts: dict[str, int] = {
        "glucose": len(batch.glucose),
        "boluses": len(batch.boluses),
        "basal_events": len(batch.basal_events),
        "alarms": len(batch.alarms),
        "workouts": len(batch.workouts),
        "sleep_sessions": len(batch.sleep_sessions),
        "daily_vitals": len(batch.daily_vitals),
    }

    extra_fields = batch.model_extra or {}
    extra_records = 0
    for key, val in extra_fields.items():
        if isinstance(val, list):
            counts[key] = len(val)
            extra_records += len(val)

    canonical_total = (
        len(batch.glucose)
        + len(batch.boluses)
        + len(batch.basal_events)
        + len(batch.alarms)
        + len(batch.workouts)
        + len(batch.sleep_sessions)
        + len(batch.daily_vitals)
    )
    total_records = canonical_total + extra_records

    session_updated = False
    if batch.session_id and canonical_total > 0:
        record = session_store.get_session(batch.session_id)
        if record is not None:
            ds = record.dataset
            updated_glucose = _merge_by_key(ds.glucose, batch.glucose, "ts")
            updated_boluses = _merge_by_key(ds.boluses, batch.boluses, "ts")
            updated_basal = _merge_by_key(ds.basal_events, batch.basal_events, "ts")
            updated_alarms = _merge_by_key(ds.alarms, batch.alarms, "ts")
            updated_workouts = _merge_by_key(ds.workouts, batch.workouts, "start_ts")
            updated_sleep = _merge_by_key(
                ds.sleep_sessions, batch.sleep_sessions, "sleep_id"
            )
            updated_vitals = _merge_by_key(
                ds.daily_vitals, batch.daily_vitals, "date"
            )

            all_dates = [p.ts[:10] for p in updated_glucose if len(p.ts) >= 10]
            start_date = min(all_dates) if all_dates else ds.start_date
            end_date = max(all_dates) if all_dates else ds.end_date

            updated_ds = ds.model_copy(
                update={
                    "start_date": start_date,
                    "end_date": end_date,
                    "glucose": updated_glucose,
                    "boluses": updated_boluses,
                    "basal_events": updated_basal,
                    "alarms": updated_alarms,
                    "workouts": updated_workouts,
                    "sleep_sessions": updated_sleep,
                    "daily_vitals": updated_vitals,
                }
            )
            new_profile = await _call_maybe_async(build_data_profile, updated_ds)
            new_replays = await _call_maybe_async(build_day_replays, updated_ds)
            session_store.update_session_data(
                batch.session_id,
                dataset=updated_ds,
                profile=new_profile,
                replays=new_replays,
                increment_sync=True,
            )
            session_updated = True

    event_summary = {
        "sync_id": sync_id,
        "received_at": received_at,
        "source": batch.source,
        "device_id": batch.device_id,
        "session_id": batch.session_id,
        "session_updated": session_updated,
        "records_received": counts,
        "total_records": total_records,
    }
    session_store.record_sync_event(event_summary)

    return PeriodicSyncBatchResponse(
        status="accepted",
        sync_id=sync_id,
        received_at=received_at,
        source=batch.source,
        session_id=batch.session_id,
        session_updated=session_updated,
        records_received=counts,
        total_records=total_records,
        next_poll_seconds=300,
    )
