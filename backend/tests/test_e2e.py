from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import importlib

import glycolens.agents.config as agent_config
from glycolens.adapters.personas import LOCAL_GLOOKO_DIR
from glycolens.api.app import app
from glycolens.api.store import session_store

api_app_module = importlib.import_module("glycolens.api.app")


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def deterministic_statistical_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Force fast, deterministic statistical mode without mutating real backend/.env."""
    saved_runtime_key = agent_config._runtime_api_key
    saved_env_gemini = os.environ.get("GEMINI_API_KEY")
    saved_env_google = os.environ.get("GOOGLE_API_KEY")

    temp_env = tmp_path / ".env"
    monkeypatch.setattr(api_app_module, "PERSIST_ENV_PATH", str(temp_env))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    agent_config.set_runtime_api_key("")

    try:
        yield temp_env
    finally:
        agent_config._runtime_api_key = saved_runtime_key
        if saved_env_gemini is not None:
            os.environ["GEMINI_API_KEY"] = saved_env_gemini
        else:
            os.environ.pop("GEMINI_API_KEY", None)
        if saved_env_google is not None:
            os.environ["GOOGLE_API_KEY"] = saved_env_google
        else:
            os.environ.pop("GOOGLE_API_KEY", None)


def _collect_sse_events(client: TestClient, session_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with client.stream("GET", f"/api/sessions/{session_id}/discover/stream") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("data:"):
                payload = line[5:].strip()
                events.append(json.loads(payload))
    return events


def _build_in_memory_glooko_zip(days: int = 8) -> bytes:
    """Create a realistic multi-CSV Glooko export .zip archive entirely in memory."""
    base_dt = datetime(2026, 10, 1, 0, 0)

    cgm_lines = [
        "Name:Synthetic Test User,Date Range:2026-10-01 - 2026-10-08",
        "Timestamp,CGM Glucose Value (mg/dl),Serial Number",
    ]
    bolus_lines = [
        "Name:Synthetic Test User,Date Range:2026-10-01 - 2026-10-08",
        "Timestamp,Insulin Type,Blood Glucose Input (mg/dl),Carbs Input (g),Carbs Ratio,Insulin Delivered (U)",
    ]
    insulin_lines = [
        "Name:Synthetic Test User,Date Range:2026-10-01 - 2026-10-08",
        "Timestamp,Total Basal (U),Total Bolus (U),Total Insulin (U)",
    ]
    alarms_lines = [
        "Name:Synthetic Test User,Date Range:2026-10-01 - 2026-10-08",
        "Timestamp,Alarm/Event",
    ]

    for d in range(days):
        day_start = base_dt + timedelta(days=d)
        # 5-minute CGM trace across each day
        for step in range(288):
            ts = day_start + timedelta(minutes=5 * step)
            hr = ts.hour + ts.minute / 60.0
            # Post-breakfast and post-dinner rises + afternoon post-walk dip
            mg_dl = 118.0
            if 7.5 <= hr <= 10.5:
                mg_dl += 72.0 * (1.0 - abs(hr - 9.0) / 1.5)
            elif 12.5 <= hr <= 14.0:
                mg_dl += 45.0 * (1.0 - abs(hr - 13.2) / 0.8)
            elif 14.0 < hr <= 15.5:
                mg_dl -= 32.0 * (1.0 - abs(hr - 14.75) / 0.75)
            elif 19.0 <= hr <= 21.5:
                mg_dl += 58.0 * (1.0 - abs(hr - 20.0) / 1.3)
            cgm_lines.append(f"{ts.strftime('%Y-%m-%d %H:%M')},{round(mg_dl, 1)},DX-TEST-01")

        # Meal boluses at 08:00, 12:30, 19:00
        bolus_lines.append(
            f"{(day_start + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')},Normal,118,45,9,{5.0:.1f}"
        )
        bolus_lines.append(
            f"{(day_start + timedelta(hours=12, minutes=30)).strftime('%Y-%m-%d %H:%M')},Normal,125,55,10,{5.5:.1f}"
        )
        bolus_lines.append(
            f"{(day_start + timedelta(hours=19)).strftime('%Y-%m-%d %H:%M')},Normal,130,60,10,{6.0:.1f}"
        )

        # Daily insulin summary at 23:59
        insulin_lines.append(
            f"{(day_start + timedelta(hours=23, minutes=59)).strftime('%Y-%m-%d %H:%M')},22.4,16.5,38.9"
        )

        if d % 3 == 0:
            alarms_lines.append(
                f"{(day_start + timedelta(hours=9, minutes=10)).strftime('%Y-%m-%d %H:%M')},dexcom_high_glucose_alert"
            )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("cgm_data_1.csv", "\n".join(cgm_lines) + "\n")
        zf.writestr("bolus_data_1.csv", "\n".join(bolus_lines) + "\n")
        zf.writestr("insulin_data_1.csv", "\n".join(insulin_lines) + "\n")
        zf.writestr("alarms_data_1.csv", "\n".join(alarms_lines) + "\n")
    return buf.getvalue()


def _build_in_memory_watch_zip(days: int = 8) -> bytes:
    """Create a realistic Samsung Health smartwatch export .zip archive in memory."""
    base_dt = datetime(2026, 10, 1, 0, 0)

    exercise_lines = [
        "com.samsung.shealth.exercise,1,0",
        "com.samsung.health.exercise.start_time,com.samsung.health.exercise.end_time,"
        "com.samsung.health.exercise.time_offset,com.samsung.health.exercise.duration,"
        "com.samsung.health.exercise.exercise_type,com.samsung.health.exercise.count,"
        "com.samsung.health.exercise.mean_cadence,com.samsung.health.exercise.mean_speed,"
        "com.samsung.health.exercise.mean_heart_rate,com.samsung.health.exercise.max_heart_rate,"
        "com.samsung.health.exercise.calorie",
    ]
    sleep_lines = [
        "com.samsung.shealth.sleep_stage,1,0",
        "sleep_id,start_time,end_time,time_offset,stage",
    ]
    day_summary_lines = [
        "com.samsung.shealth.activity.day_summary,1,0",
        "day_time,step_count,walk_time,run_time,calorie,move_hourly_count,floor_count,longest_idle_time",
    ]
    bp_lines = [
        "com.samsung.health.blood_pressure,1,0",
        "start_time,time_offset,systolic,diastolic,pulse",
    ]

    for d in range(days):
        day_start = base_dt + timedelta(days=d)

        # Afternoon brisk walk at 13:00 UTC (+0100 -> 14:00 local, overlapping lunch IOB)
        w_utc_start = day_start + timedelta(hours=13)
        w_utc_end = w_utc_start + timedelta(minutes=30)
        exercise_lines.append(
            f"{w_utc_start.strftime('%Y-%m-%d %H:%M:%S')},{w_utc_end.strftime('%Y-%m-%d %H:%M:%S')},"
            f"UTC+0100,1800000,1001,3150,105.0,1.38,118.0,134.0,152.0"
        )

        # Overnight sleep stages (22:00 UTC -> 05:30 UTC => 23:00 -> 06:30 local, 7.5h)
        if d < days - 1:
            sid = f"sleep-{d:02d}"
            s_utc = day_start + timedelta(hours=22)
            stages = [
                ("40002", 90),   # Light
                ("40003", 110),  # Deep
                ("40002", 120),  # Light
                ("40004", 110),  # REM
                ("40001", 20),   # Awake
            ]
            cur = s_utc
            for code, mins in stages:
                nxt = cur + timedelta(minutes=mins)
                sleep_lines.append(
                    f"{sid},{cur.strftime('%Y-%m-%d %H:%M:%S')},{nxt.strftime('%Y-%m-%d %H:%M:%S')},UTC+0100,{code}"
                )
                cur = nxt

        # Daily activity summary + morning blood pressure
        day_str = day_start.strftime("%Y-%m-%d")
        day_summary_lines.append(f"{day_str},9450,2700000,0,430.0,10,8,3600000")
        bp_utc = day_start + timedelta(hours=7)
        sys_v = 138.0 if d % 2 == 0 else 122.0
        dia_v = 86.0 if d % 2 == 0 else 78.0
        bp_lines.append(
            f"{bp_utc.strftime('%Y-%m-%d %H:%M:%S')},UTC+0100,{sys_v},{dia_v},68"
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("com.samsung.shealth.exercise.20261008.csv", "\n".join(exercise_lines) + "\n")
        zf.writestr("com.samsung.shealth.sleep_stage.20261008.csv", "\n".join(sleep_lines) + "\n")
        zf.writestr(
            "com.samsung.shealth.activity.day_summary.20261008.csv",
            "\n".join(day_summary_lines) + "\n",
        )
        zf.writestr("com.samsung.health.blood_pressure.20261008.csv", "\n".join(bp_lines) + "\n")
    return buf.getvalue()


def test_health_and_api_key_configuration(
    client: TestClient,
    deterministic_statistical_mode: Path,
) -> None:
    """Verify GET /api/health and runtime POST /api/config/apikey."""
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["gemini_configured"] is False
    assert body["has_api_key"] is False
    assert isinstance(body["fast_model"], str) and len(body["fast_model"]) > 0
    assert isinstance(body["pro_model"], str) and len(body["pro_model"]) > 0

    # Configure a runtime key in isolated temp .env and verify health toggles
    key_res = client.post("/api/config/apikey", json={"api_key": "AIzaSyTestDummyKeyForUnitCheck"})
    assert key_res.status_code == 200
    key_body = key_res.json()
    assert key_body["status"] == "ok"
    assert key_body["gemini_configured"] is True
    assert key_body["has_api_key"] is True
    assert deterministic_statistical_mode.is_file()

    # Clear key back to statistical mode
    clear_res = client.post("/api/config/apikey", json={"api_key": ""})
    assert clear_res.status_code == 200
    assert clear_res.json()["gemini_configured"] is False


def test_personas_listing(client: TestClient) -> None:
    """Verify GET /api/personas returns all synthetic personas (+ local_real_export when present)."""
    res = client.get("/api/personas")
    assert res.status_code == 200
    personas = res.json()
    assert isinstance(personas, list)

    persona_by_id = {p["id"]: p for p in personas}
    for required_id in ("active_walker", "hiit_lifter", "bp_shiftworker"):
        assert required_id in persona_by_id, f"Missing synthetic persona {required_id}"
        info = persona_by_id[required_id]
        assert info["is_local_real"] is False
        assert len(info["title"]) > 5
        assert len(info["badges"]) >= 2

    if os.path.isdir(LOCAL_GLOOKO_DIR):
        assert "local_real_export" in persona_by_id
        assert persona_by_id["local_real_export"]["is_local_real"] is True
        # Local real export should be surfaced first when available
        assert personas[0]["id"] == "local_real_export"


def test_all_personas_produce_distinct_domain_appropriate_sse_insights(
    client: TestClient,
    deterministic_statistical_mode: Path,
) -> None:
    """
    Verify POST /api/sessions/persona/{id} + GET /api/sessions/{id}/discover/stream SSE
    for active_walker, hiit_lifter, bp_shiftworker (+ local_real_export if present),
    checking domain-specific verified insights, distinctness, and safety guardrails.
    """
    expected_signature_hypotheses: dict[str, set[str]] = {
        "active_walker": {
            "activity_iob_walk_drop_rate",
            "sleep_wake_foot_on_floor_surge",
            "dosing_late_night_bolusing",
        },
        "hiit_lifter": {
            "activity_anaerobic_adrenaline_spike",
            "sleep_short_vs_restorative_next_day",
            "vitals_workweek_vs_weekend_tir",
        },
        "bp_shiftworker": {
            "vitals_smartwatch_bp_surge",
            "vitals_wrist_temp_resp_resistance",
            "sleep_short_vs_restorative_next_day",
            "activity_high_bg_walk_rescue",
        },
    }
    if os.path.isdir(LOCAL_GLOOKO_DIR):
        expected_signature_hypotheses["local_real_export"] = {
            "activity_iob_walk_drop_rate",
            "sleep_wake_foot_on_floor_surge",
            "dosing_reactive_vs_prebolus",
            "dosing_pump_config_bypass_review",
        }

    verified_sets_by_persona: dict[str, set[str]] = {}
    insight_headlines_by_persona: dict[str, list[str]] = {}

    for persona_id, required_hypotheses in expected_signature_hypotheses.items():
        # 1. Create session from persona
        create_res = client.post(f"/api/sessions/persona/{persona_id}")
        assert create_res.status_code == 200, f"Failed creating session for {persona_id}"
        bundle = create_res.json()
        session_id = bundle["session_id"]
        profile = bundle["profile"]
        replays = bundle["replays"]

        assert session_id.startswith("sess_")
        assert profile["dataset_id"] == persona_id
        assert profile["cgm_count"] > 5000
        assert len(profile["hourly_agp"]) == 24
        assert len(profile["day_of_week"]) == 7
        assert len(replays) >= 3

        # Secondary GET endpoints for profile and replays
        prof_res = client.get(f"/api/sessions/{session_id}/profile")
        assert prof_res.status_code == 200
        assert prof_res.json()["cgm_count"] == profile["cgm_count"]

        rep_res = client.get(f"/api/sessions/{session_id}/replays")
        assert rep_res.status_code == 200
        assert len(rep_res.json()) == len(replays)

        # Report should be 404 prior to discovery stream
        pre_report = client.get(f"/api/sessions/{session_id}/report")
        assert pre_report.status_code == 404

        # 2. Stream live autonomous discovery over SSE
        events = _collect_sse_events(client, session_id)
        assert len(events) >= 20
        event_types = [e["event_type"] for e in events]
        assert "agent_status" in event_types
        assert "hypothesis_tested" in event_types
        assert "insight_ready" in event_types
        assert events[-1]["event_type"] == "complete"

        # Verify all 4 specialist domains emitted hypothesis_tested events
        tested_events = [e for e in events if e["event_type"] == "hypothesis_tested"]
        assert len(tested_events) == 17  # 4 activity + 4 sleep + 5 dosing + 4 vitals
        domains_tested = {e["domain"] for e in tested_events}
        assert domains_tested == {"activity", "sleep", "dosing", "vitals"}

        verified_hyp_ids = {
            e["evidence"]["hypothesis_id"]
            for e in tested_events
            if e["evidence"]["statistically_significant"]
        }
        verified_sets_by_persona[persona_id] = verified_hyp_ids

        missing = required_hypotheses - verified_hyp_ids
        assert not missing, (
            f"Persona {persona_id} missing expected signature hypotheses {missing}; "
            f"got {sorted(verified_hyp_ids)}"
        )

        # 3. Validate final DiscoveryReport (both from SSE complete event and GET /report)
        stream_report = events[-1]["report"]
        assert stream_report is not None
        assert stream_report["session_id"] == session_id
        assert stream_report["hypotheses_tested"] == 17
        assert stream_report["hypotheses_verified"] == len(verified_hyp_ids)
        assert len(stream_report["insights"]) >= 5
        assert len(stream_report["executive_summary"]) > 60

        stored_report_res = client.get(f"/api/sessions/{session_id}/report")
        assert stored_report_res.status_code == 200
        stored_report = stored_report_res.json()
        assert stored_report["hypotheses_verified"] == stream_report["hypotheses_verified"]

        insight_headlines_by_persona[persona_id] = [
            c["headline"] for c in stored_report["insights"]
        ]

        # 4. Enforce strict statistical & clinical safety guardrails on every InsightCard
        for idx, card in enumerate(stored_report["insights"], start=1):
            assert card["severity_rank"] == idx
            assert card["category"] in {
                "habit_experiment",
                "pre_activity_shield",
                "doctor_discussion",
            }
            ev = card["evidence"]
            assert ev["statistically_significant"] is True
            assert ev["n_a"] >= 5 and ev["n_b"] >= 5
            assert (ev["ci_95_low"] > 0.0) or (ev["ci_95_high"] < 0.0)
            assert len(ev["chart_spec"]["bars"]) >= 2

            if ev["hypothesis_id"] == "dosing_pump_config_bypass_review":
                assert card["category"] == "doctor_discussion"

    # 5. Assert personas produce genuinely distinct verified hypothesis sets & top insight rankings
    assert verified_sets_by_persona["active_walker"] != verified_sets_by_persona["hiit_lifter"]
    assert verified_sets_by_persona["hiit_lifter"] != verified_sets_by_persona["bp_shiftworker"]
    assert verified_sets_by_persona["active_walker"] != verified_sets_by_persona["bp_shiftworker"]
    assert (
        insight_headlines_by_persona["active_walker"]
        != insight_headlines_by_persona["hiit_lifter"]
    )
    assert (
        insight_headlines_by_persona["hiit_lifter"]
        != insight_headlines_by_persona["bp_shiftworker"]
    )


def test_walk_simulation_and_ask_my_body_chat(
    client: TestClient,
    deterministic_statistical_mode: Path,
) -> None:
    """Verify POST /api/sessions/{id}/simulate/walk and POST /api/sessions/{id}/chat."""
    create_res = client.post("/api/sessions/persona/active_walker")
    assert create_res.status_code == 200
    session_id = create_res.json()["session_id"]

    # Populate session report via SSE first
    events = _collect_sse_events(client, session_id)
    assert events[-1]["event_type"] == "complete"

    # 1. Walk Rx Simulation: High starting glucose + modest IOB -> safe glucose rescue
    rescue_res = client.post(
        f"/api/sessions/{session_id}/simulate/walk",
        json={"start_cgm": 225.0, "active_iob": 0.4, "planned_steps": 2500},
    )
    assert rescue_res.status_code == 200
    rescue = rescue_res.json()
    assert rescue["predicted_delta_mg_dl"] < 0.0
    assert rescue["predicted_end_cgm"] < 225.0
    assert rescue["hypo_risk_level"] == "low"
    assert rescue["model_r"] > 0.5
    assert len(rescue["recommendation"]) > 20

    # 2. Walk Rx Simulation: Same lower starting glucose (135 mg/dL), zero IOB vs high active IOB
    zero_iob_res = client.post(
        f"/api/sessions/{session_id}/simulate/walk",
        json={"start_cgm": 135.0, "active_iob": 0.0, "planned_steps": 2500},
    )
    assert zero_iob_res.status_code == 200
    zero_iob = zero_iob_res.json()

    risky_res = client.post(
        f"/api/sessions/{session_id}/simulate/walk",
        json={"start_cgm": 135.0, "active_iob": 2.8, "planned_steps": 3500},
    )
    assert risky_res.status_code == 200
    risky = risky_res.json()
    assert risky["predicted_delta_mg_dl"] < zero_iob["predicted_delta_mg_dl"]
    assert risky["predicted_end_cgm"] < rescue["predicted_end_cgm"]
    assert risky["hypo_risk_level"] == "high"
    assert "carbs" in risky["recommendation"].lower()

    # 3. Ask My Body Chat: Ad-hoc daily step threshold question + pump safety question
    chat_steps_res = client.post(
        f"/api/sessions/{session_id}/chat",
        json={
            "question": "How do days with 8000 steps compare on Time in Range?",
            "history": [],
        },
    )
    assert chat_steps_res.status_code == 200
    chat_steps = chat_steps_res.json()
    assert chat_steps["engine_mode"] == "statistical_fallback"
    assert len(chat_steps["answer"]) > 80
    assert len(chat_steps["supporting_numbers"]) >= 2
    assert any("steps" in chip.lower() or "tir" in chip.lower() for chip in chat_steps["supporting_numbers"])

    chat_pump_res = client.post(
        f"/api/sessions/{session_id}/chat",
        json={
            "question": "Should I change my evening carb ratio or basal rate?",
            "history": [
                {"role": "user", "content": "How do days with 8000 steps compare on Time in Range?"},
                {"role": "assistant", "content": chat_steps["answer"]},
            ],
        },
    )
    assert chat_pump_res.status_code == 200
    chat_pump = chat_pump_res.json()
    assert "endocrinologist" in chat_pump["answer"].lower()


def test_in_memory_zip_upload_and_periodic_sync_webhook(
    client: TestClient,
    deterministic_statistical_mode: Path,
) -> None:
    """
    Verify POST /api/sessions/upload with in-memory Glooko + Samsung Watch .zip archives,
    verify PII banner stripping, SSE stream execution, error handling, and webhook sync.
    """
    glooko_zip = _build_in_memory_glooko_zip(days=8)
    watch_zip = _build_in_memory_watch_zip(days=8)

    upload_res = client.post(
        "/api/sessions/upload",
        data={"title": "E2E In-Memory Archive Test"},
        files={
            "glooko_file": ("glooko_export.zip", glooko_zip, "application/zip"),
            "watch_file": ("samsung_watch.zip", watch_zip, "application/zip"),
        },
    )
    assert upload_res.status_code == 200, upload_res.text
    bundle = upload_res.json()
    session_id = bundle["session_id"]
    profile = bundle["profile"]
    replays = bundle["replays"]

    assert profile["title"] == "E2E In-Memory Archive Test"
    assert "Synthetic Test User" not in json.dumps(bundle)  # PII header stripped!
    assert profile["cgm_count"] == 8 * 288
    assert profile["bolus_count"] == 8 * 3
    assert profile["workout_count"] == 8
    assert profile["sleep_count"] == 7
    assert profile["bp_count"] == 8
    assert "glooko_cgm" in profile["source_tags"]
    assert "samsung_watch" in profile["source_tags"]
    assert "blood_pressure" in profile["source_tags"]
    assert len(replays) >= 3

    # Verify discovery SSE stream works seamlessly on uploaded zip dataset
    events = _collect_sse_events(client, session_id)
    assert events[-1]["event_type"] == "complete"
    assert events[-1]["report"]["hypotheses_tested"] == 17

    # Verify periodic cloud sync webhook merges new CGM readings into the session
    sync_res = client.post(
        "/api/webhooks/periodic-sync",
        json={
            "session_id": session_id,
            "source": "dexcom_health_connect",
            "glucose": [
                {"ts": "2026-10-09 00:00", "mg_dl": 124.0},
                {"ts": "2026-10-09 00:05", "mg_dl": 122.0},
            ],
        },
    )
    assert sync_res.status_code == 200
    sync_body = sync_res.json()
    assert sync_body["status"] == "accepted"
    assert sync_body["session_updated"] is True
    assert sync_body["total_records"] == 2

    updated_prof = client.get(f"/api/sessions/{session_id}/profile").json()
    assert updated_prof["cgm_count"] == 8 * 288 + 2

    # Verify clean 400 / 404 error responses on invalid inputs
    empty_res = client.post(
        "/api/sessions/upload",
        files={"glooko_file": ("empty.zip", b"", "application/zip")},
    )
    assert empty_res.status_code == 400

    corrupt_res = client.post(
        "/api/sessions/upload",
        files={"glooko_file": ("garbage.zip", b"not-valid-glooko-data", "application/zip")},
    )
    assert corrupt_res.status_code == 400

    missing_persona = client.post("/api/sessions/persona/nonexistent_persona")
    assert missing_persona.status_code == 404

    missing_session = client.get("/api/sessions/sess_does_not_exist/profile")
    assert missing_session.status_code == 404
    session_store.clear()


def test_gemini_swarm_orchestration_and_safety_guardrails(
    client: TestClient,
    deterministic_statistical_mode: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify the full gemini_swarm multi-agent path (4 parallel specialists + Chief Synthesizer
    + Ask My Body chat) and deterministic medical safety scrubbing using a deterministic fake client.
    """
    import glycolens.agents.chat as chat_mod
    import glycolens.agents.swarm as swarm_mod
    from glycolens.agents.swarm import _DraftInsightSpec, _SynthesizerOutput

    class _FakeResp:
        def __init__(self, text: str, parsed: Any = None) -> None:
            self.text = text
            self.parsed = parsed

    class _FakeAsyncChat:
        async def send_message(self, prompt: str) -> _FakeResp:
            return _FakeResp("- Verified domain pattern grounded in 95% CI.")

    class _FakeAsyncChats:
        def create(self, **kwargs: Any) -> _FakeAsyncChat:
            return _FakeAsyncChat()

    class _FakeAsyncModels:
        async def generate_content(self, **kwargs: Any) -> _FakeResp:
            synth = _SynthesizerOutput(
                executive_summary="Integrated 4-domain synthesis grounded in bootstrap 95% CIs.",
                insights=[
                    _DraftInsightSpec(
                        hypothesis_id="dosing_pump_config_bypass_review",
                        category="habit_experiment",  # Deliberately wrong category + prescriptive text to test guardrail!
                        severity_rank=1,
                        headline="Manual zero-carb boluses dominate your daily pump usage",
                        plain_explanation="98% of boluses bypass carb entry.",
                        impact_badge="+96% manual",
                        personal_anchor="32 days logged",
                        try_this_week="Increase your carb ratio and take 4 units before dinner.",
                    ),
                    _DraftInsightSpec(
                        hypothesis_id="activity_iob_walk_drop_rate",
                        category="pre_activity_shield",
                        severity_rank=2,
                        headline="Active bolus insulin steeply accelerates walking glucose drops",
                        plain_explanation="Brisk walks with >=1U IOB drop glucose much faster.",
                        impact_badge="+129 mg/dL/hr",
                        personal_anchor="23 vs 24 walks",
                        try_this_week="Check active IOB in Pre-Walk Shield before brisk walks.",
                    ),
                ],
            )
            return _FakeResp(synth.model_dump_json(), parsed=synth)

    class _FakeAio:
        def __init__(self) -> None:
            self.chats = _FakeAsyncChats()
            self.models = _FakeAsyncModels()

    class _FakeSyncChat:
        def send_message(self, prompt: str) -> _FakeResp:
            return _FakeResp(
                "Your active walks lower post-meal glucose reliably.\n\n"
                "SUPPORTING_NUMBERS:\n"
                "- Walk Model r: 0.99\n"
                "- Overall TIR: 80.9%"
            )

    class _FakeSyncChats:
        def create(self, **kwargs: Any) -> _FakeSyncChat:
            return _FakeSyncChat()

    class _FakeClient:
        def __init__(self) -> None:
            self.aio = _FakeAio()
            self.chats = _FakeSyncChats()

    agent_config.set_runtime_api_key("AIzaSyFakeSwarmKey")
    monkeypatch.setattr(swarm_mod, "create_genai_client", lambda: _FakeClient())
    monkeypatch.setattr(chat_mod, "create_genai_client", lambda: _FakeClient())

    session_id = client.post("/api/sessions/persona/active_walker").json()["session_id"]
    events = _collect_sse_events(client, session_id)
    assert events[-1]["event_type"] == "complete"
    assert events[-1]["engine_mode"] == "gemini_swarm"

    report = events[-1]["report"]
    assert report["engine_mode"] == "gemini_swarm"
    # Chief synthesizer topped up to >=5 verified insights and scrubbed unsafe pump advice!
    assert len(report["insights"]) >= 5
    pump_card = next(
        c for c in report["insights"]
        if c["evidence"]["hypothesis_id"] == "dosing_pump_config_bypass_review"
    )
    assert pump_card["category"] == "doctor_discussion"
    assert "take 4 units" not in pump_card["try_this_week"].lower()
    assert "endocrinologist" in pump_card["try_this_week"].lower()

    chat_res = client.post(
        f"/api/sessions/{session_id}/chat",
        json={"question": "How predictable are my walks?", "history": []},
    )
    assert chat_res.status_code == 200
    chat_json = chat_res.json()
    assert chat_json["engine_mode"] == "gemini_swarm"
    assert "Walk Model r: 0.99" in chat_json["supporting_numbers"]
    session_store.clear()

