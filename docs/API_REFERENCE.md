# GlycoLens API & Data Integration Reference

GlycoLens exposes a **headless, UI-agnostic REST + Server-Sent Events (SSE) API** implemented with FastAPI (`backend/glycolens/api/app.py`) and strictly typed with Pydantic v2 (`backend/glycolens/schemas.py`). When running locally, interactive Swagger / OpenAPI documentation is automatically served at:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`
- **OpenAPI JSON Schema:** `http://localhost:8000/openapi.json`

---

## 1. HTTP & SSE Endpoints Overview

| Method | Path | Response Type | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/health` | `application/json` | Health check, active Gemini model names, and API key status (`gemini_configured`). |
| `POST` | `/api/config/apikey` | `application/json` | Configure or update `GEMINI_API_KEY` at runtime (persists locally to gitignored `backend/.env`). |
| `GET` | `/api/personas` | `list[PersonaInfo]` | List built-in anonymized synthetic digital twins (plus local export if present on disk). |
| `POST` | `/api/sessions/persona/{persona_id}` | `SessionBundleResponse` | Load a persona dataset into an active session, compute its `DataProfile` & 24h `DayReplay`s. |
| `POST` | `/api/sessions/upload` | `SessionBundleResponse` | Multipart upload of a Glooko `.zip` export and optional Samsung Health `.zip` export in memory. |
| `GET` | `/api/sessions/{session_id}/profile` | `DataProfile` | Retrieve baseline glycemic metrics (TIR, TBR, TAR, GMI, CV%), 24h AGP buckets, and `WalkModelWeights`. |
| `GET` | `/api/sessions/{session_id}/replays` | `list[DayReplay]` | Retrieve curated 24-hour multimodal day replays (CGM + boluses + workouts + sleep + BP). |
| `GET` | `/api/sessions/{session_id}/discover/stream` | `text/event-stream` | Live SSE stream orchestrating the 4 parallel domain specialists + Chief Clinical Synthesizer. |
| `GET` | `/api/sessions/{session_id}/report` | `DiscoveryReport` | Fetch the completed `DiscoveryReport` cached on the session. |
| `POST` | `/api/sessions/{session_id}/simulate/walk` | `WalkSimulationResponse` | Predict 30-min glucose change, end CGM, and 90-min hypo risk using the user's Ridge Walk Rx model. |
| `POST` | `/api/sessions/{session_id}/chat` | `ChatResponse` | Grounded natural-language Q&A (*Ask About Your Data*) with ad-hoc bootstrap cohort split tool-calling. |
| `POST` | `/api/webhooks/periodic-sync` | `PeriodicSyncBatchResponse` | Incremental webhook for continuous CGM/wearable cloud sync (Health Connect, Dexcom, Apple HealthKit). |

---

## 2. Detailed Endpoint Specifications

### `GET /api/health`
Returns backend status and whether the Gemini multi-agent engine or deterministic statistical fallback is active.

```json
{
  "gemini_configured": true,
  "fast_model": "gemini-3.8-flash",
  "pro_model": "gemini-3.8-flash",
  "has_api_key": true,
  "status": "ok"
}
```

### `POST /api/config/apikey`
Updates the active Google AI Studio `GEMINI_API_KEY` in memory and writes it to local gitignored `backend/.env`.
- **Request Body (`ApiKeyRequest`):**
  ```json
  { "api_key": "YOUR_GEMINI_API_KEY" }
  ```

### `GET /api/personas`
Lists available datasets (`active_walker`, `hiit_lifter`, `bp_shiftworker`, plus `local_real_export` when local gitignored exports are present).

### `POST /api/sessions/persona/{persona_id}`
Creates an isolated session from a named persona and immediately returns its baseline `DataProfile` and `DayReplay` archetypes:
```json
{
  "session_id": "sess_a1b2c3d4e5f6",
  "profile": {
    "title": "Active Walker (AID Pump + Smartwatch)",
    "tir_pct": 61.4,
    "tbr_pct": 1.2,
    "tar_pct": 37.4,
    "mean_cgm": 168.2,
    "gmi_pct": 7.3,
    "cv_pct": 33.8,
    "hourly_agp": [ ... ],
    "walk_model": {
      "sample_count": 42,
      "holdout_r": 0.89,
      "mae_mg_dl": 8.4
    }
  },
  "replays": [ ... ]
}
```

### `POST /api/sessions/upload`
Multipart form-data endpoint accepting raw `.zip` exports directly in memory (no permanent raw file storage):
- `glooko_file` *(required, ` UploadFile`)*: Glooko diabetes export `.zip` containing `cgm_data_*.csv`, `bolus_data_*.csv`, `basal_data_*.csv`, `alarms_data_*.csv`.
- `watch_file` *(optional, `UploadFile`)*: Samsung Health smartwatch export `.zip` containing `com.samsung.shealth.exercise*.csv`, `com.samsung.shealth.sleep*.csv`, `sleep_stage*.csv`, `blood_pressure*.csv`, `skin_temperature*.csv`, `respiratory_rate*.csv`.
- `title` *(optional, `str`)*: Custom display label.

### `GET /api/sessions/{session_id}/discover/stream` (Server-Sent Events)
Streams newline-delimited `data: <SwarmStreamEvent JSON>\n\n` frames as the 4 parallel domain agents execute bootstrap tests, run Gemini confounder checks, and synthesize priority `InsightCard`s.

**Event Types (`event_type`):**
1. `agent_status` — Specialist lifecycle transitions (`queued` → `running` → `testing` → `done`).
2. `hypothesis_tested` — Emitted for each of the 17 domain probes with full `StatisticalEvidence` (`status: "verified"` when the 1,000-iteration bootstrap 95% CI excludes zero; `"rejected"` otherwise).
3. `insight_ready` — Streams each safety-checked `InsightCard` (`habit_experiment`, `pre_activity_shield`, or `doctor_discussion`) with its immutable `StatisticalEvidence` attached.
4. `complete` — Final event carrying the complete `DiscoveryReport` (`engine_mode`: `"gemini_swarm"` or `"statistical_fallback"`).
5. `error` — Graceful error notification if an unexpected exception occurs.

### `POST /api/sessions/{session_id}/simulate/walk`
Evaluates the session's personalized 4-feature standardized Ridge Regression model (`StartCGM`, `IOB`, `Steps`, `Steps × IOB` interaction):
- **Request (`WalkSimulationRequest`):**
  ```json
  {
    "start_cgm": 165.0,
    "active_iob": 2.1,
    "planned_steps": 2800
  }
  ```
- **Response (`WalkSimulationResponse`):**
  ```json
  {
    "predicted_delta_mg_dl": -54.2,
    "predicted_end_cgm": 110.8,
    "hypo_risk_level": "moderate",
    "recommendation": "With 2.1U active bolus insulin, a 2,800-step walk is projected to drop glucose ~54 mg/dL...",
    "model_r": 0.553
  }
  ```

### `POST /api/sessions/{session_id}/chat`
Answers natural-language questions grounded strictly in the user's `DataProfile`, verified `StatisticalEvidence`, and live `query_custom_cohort_split` tool executions.
- **Request (`ChatRequest`):**
  ```json
  {
    "question": "Compare my Time in Range on days with >= 8,000 steps vs fewer steps",
    "history": []
  }
  ```
- **Response (`ChatResponse`):**
  ```json
  {
    "answer": "On days where you logged **8,000 or more steps** ...",
    "supporting_numbers": [
      "steps >= 8000 vs < 8000: 64.2 vs 54.8 % tir_pct (Δ +9.4 %)"
    ],
    "engine_mode": "gemini_swarm"
  }
  ```

### `POST /api/webhooks/periodic-sync`
Future-ready webhook enabling mobile companion apps (Apple HealthKit, Android Health Connect) or cloud CGM relays (Dexcom API, Nightscout) to push incremental telemetry batches (`cgm`, `boluses`, `workouts`, `sleep_summaries`, `daily_vitals`) into an existing or new session and automatically recompute `DataProfile` and `DayReplay`s.

---

## 3. External APIs & Vendor Export Formats Supported

1. **Google Gemini API (`google-genai` Python SDK v3.3+):**
   - **Automatic Function Calling (`tools=[bound_tool]`):** Used by the 4 parallel domain specialists and *Ask About Your Data* chat to invoke `query_custom_cohort_split(metric, split_feature, threshold)` against the user's daily feature table.
   - **Structured Outputs (`response_mime_type="application/json"`, `response_schema=_SynthesizerOutput`):** Used by the Chief Clinical Synthesizer to guarantee schema-valid `InsightCard` drafts.
   - **Latency Tuning (`ThinkingConfig(thinking_level="LOW")`):** Keeps full 4-specialist + synthesizer wall-clock latency to ~8–12 seconds over SSE.
2. **Glooko Diabetes Export Format (`.zip` / `.csv`):**
   - Skips Line 1 patient metadata banner (`Name:..., Date Range:...`), parses Line 2 headers, filters out sensor error sentinel codes (`2001.0`, enforcing `20 <= mg/dL <= 600`), extracts manual vs. carb-calculated insulin boluses, basal schedules, and pump configuration tables.
3. **Samsung Health / Galaxy Watch Export Format (`.zip` / `.csv`):**
   - Skips Line 1 package metadata banner (`com.samsung.shealth...`), strips column prefixes, converts **UTC timestamps + per-row `time_offset` (`UTC+0100` / `UTC+0200` DST transitions)** into exact local wall-clock time to align cleanly with Glooko local timestamps, and deduplicates Google Drive `(1).csv` export copies.
