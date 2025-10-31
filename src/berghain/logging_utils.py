from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonLinesLogger:
    def __init__(self, path: str) -> None:
        # Create directory if needed
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path

    def _write(self, record: Dict[str, Any]) -> None:
        record.setdefault("ts", _utc_iso())
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")

    def progress(
        self,
        *,
        scenario: int,
        step: int,
        admitted: int,
        rejected: int,
        cap_left: int,
        need_sum: int,
        top_remaining: Iterable[Tuple[str, int]],
        accept: bool,
        person_index: int,
    ) -> None:
        self._write(
            {
                "event": "progress",
                "scenario": scenario,
                "step": step,
                "admitted": admitted,
                "rejected": rejected,
                "cap_left": cap_left,
                "need_sum": need_sum,
                "top_remaining": list(top_remaining),
                "accept": accept,
                "personIndex": person_index,
            }
        )

    def completed(self, *, scenario: int, rejected_count: int, remaining: Dict[str, int]) -> None:
        self._write(
            {
                "event": "completed",
                "scenario": scenario,
                "rejected": rejected_count,
                "remaining": remaining,
                "constraints_satisfied": sum(remaining.values()) == 0,
            }
        )

    def failed(self, *, scenario: int, reason: str) -> None:
        self._write({"event": "failed", "scenario": scenario, "reason": reason})

    def start(
        self,
        *,
        scenario: int,
        game_id: str,
        capacity: int,
        constraints: Dict[str, int],
        prior_freqs: Optional[Dict[str, float]] = None,
        correlations: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> None:
        record = {
            "event": "start",
            "scenario": scenario,
            "gameId": game_id,
            "capacity": capacity,
            "constraints": constraints,
        }
        if prior_freqs:
            record["prior_freqs"] = prior_freqs
        if correlations:
            record["correlations"] = correlations
        self._write(record)

    def request(
        self,
        *,
        scenario: int,
        send_person_index: int,
        decide_for_index: int,
        decide_for_attrs: Dict[str, bool],
        accept: bool,
    ) -> None:
        self._write(
            {
                "event": "request",
                "scenario": scenario,
                "send_person_index": send_person_index,
                "decide_for_index": decide_for_index,
                "decide_for_attrs": decide_for_attrs,
                "accept": accept,
            }
        )

    def response(
        self,
        *,
        scenario: int,
        admitted: int,
        rejected: int,
        status: str,
        next_person_index: int | None,
    ) -> None:
        self._write(
            {
                "event": "response",
                "scenario": scenario,
                "admitted": admitted,
                "rejected": rejected,
                "status": status,
                "next_person_index": next_person_index,
            }
        )

    def resync(self, *, scenario: int, expected: int, got: int) -> None:
        self._write({"event": "resync", "scenario": scenario, "expected": expected, "got": got})
