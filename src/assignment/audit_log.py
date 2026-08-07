"""
Assignment 11 — Audit Log starter (TODO).

Records every interaction for forensics. Never blocks by itself —
other layers catch attacks; this layer makes them reviewable.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


class AuditLogPlugin:
    """Framework-agnostic audit logger (wire into ADK callbacks or your pipeline)."""

    def __init__(self):
        self.name = "audit_log"
        self.logs: list[dict] = []
        self._open: dict[str, float] = {}

    def record_input(self, *, user_id: str, text: str, request_id: str | None = None):
        """TODO: store input + start timestamp keyed by request_id/user_id."""
        rid = request_id or f"{user_id}-{len(self.logs) + len(self._open) + 1}"
        self._open[rid] = time.perf_counter()
        self.logs.append(
            {
                "request_id": rid,
                "user_id": user_id,
                "timestamp": utc_now_iso(),
                "event": "input",
                "input": text,
            }
        )
        return rid

    def record_output(
        self,
        *,
        user_id: str,
        text: str,
        blocked: bool = False,
        layer: str | None = None,
        request_id: str | None = None,
    ):
        """TODO: store output, layer decision, latency; append to self.logs."""
        rid = request_id or f"{user_id}-{len(self.logs) + 1}"
        started = self._open.pop(rid, None)
        latency_ms = None
        if started is not None:
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
        self.logs.append(
            {
                "request_id": rid,
                "user_id": user_id,
                "timestamp": utc_now_iso(),
                "event": "output",
                "output": text,
                "blocked": blocked,
                "layer": layer,
                "latency_ms": latency_ms,
            }
        )
        return rid

    def export_json(self, filepath: str = "outputs/audit_log.json"):
        """Write logs to disk (JSON array)."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.logs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
