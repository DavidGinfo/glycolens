from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from glycolens.schemas import (
    CanonicalDataset,
    DataProfile,
    DayReplay,
    DiscoveryReport,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionRecord:
    """In-memory state for an active GlycoLens analysis session."""

    session_id: str
    created_at: str
    updated_at: str
    dataset: CanonicalDataset
    profile: DataProfile
    replays: list[DayReplay]
    report: Optional[DiscoveryReport] = None
    sync_count: int = 0


class SessionStore:
    """Thread-safe in-memory store for sessions and periodic webhook sync logs."""

    def __init__(self, max_sync_log: int = 200) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionRecord] = {}
        self._sync_log: list[dict[str, Any]] = []
        self._max_sync_log = max_sync_log

    def create_session(
        self,
        dataset: CanonicalDataset,
        profile: DataProfile,
        replays: list[DayReplay],
        session_id: Optional[str] = None,
    ) -> SessionRecord:
        sid = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        now = _utc_now_iso()
        record = SessionRecord(
            session_id=sid,
            created_at=now,
            updated_at=now,
            dataset=dataset,
            profile=profile,
            replays=replays,
            report=None,
            sync_count=0,
        )
        with self._lock:
            self._sessions[sid] = record
        return record

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        with self._lock:
            return self._sessions.get(session_id)

    def set_report(
        self, session_id: str, report: DiscoveryReport
    ) -> Optional[SessionRecord]:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            record.report = report
            record.updated_at = _utc_now_iso()
            return record

    def update_session_data(
        self,
        session_id: str,
        dataset: CanonicalDataset,
        profile: DataProfile,
        replays: list[DayReplay],
        increment_sync: bool = False,
    ) -> Optional[SessionRecord]:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            record.dataset = dataset
            record.profile = profile
            record.replays = replays
            if increment_sync:
                record.sync_count += 1
            record.updated_at = _utc_now_iso()
            return record

    def list_session_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())

    def record_sync_event(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._sync_log.append(event)
            if len(self._sync_log) > self._max_sync_log:
                self._sync_log = self._sync_log[-self._max_sync_log :]
        return event

    def get_sync_log(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._sync_log)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._sync_log.clear()


session_store = SessionStore()


def create_session(
    dataset: CanonicalDataset,
    profile: DataProfile,
    replays: list[DayReplay],
    session_id: Optional[str] = None,
) -> SessionRecord:
    return session_store.create_session(
        dataset=dataset,
        profile=profile,
        replays=replays,
        session_id=session_id,
    )


def get_session(session_id: str) -> Optional[SessionRecord]:
    return session_store.get_session(session_id)


def set_report(session_id: str, report: DiscoveryReport) -> Optional[SessionRecord]:
    return session_store.set_report(session_id, report)
