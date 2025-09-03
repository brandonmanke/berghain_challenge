from __future__ import annotations

import time
from typing import Dict, Optional

import requests

from .types import (
    AttributeStatistics,
    Constraint,
    DecideAndNextCompleted,
    DecideAndNextFailed,
    DecideAndNextResponse,
    DecideAndNextRunning,
    NewGameResponse,
    Person,
)

# no logging dependency required here


class ApiClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        session: Optional[requests.Session] = None,
        retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.retries = max(0, retries)

    class ApiError(Exception):
        def __init__(self, status_code: int, message: str) -> None:
            super().__init__(f"HTTP {status_code}: {message}")
            self.status_code = status_code
            self.message = message

    def _get(self, path: str, params: Dict[str, object]) -> dict:
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                try:
                    resp.raise_for_status()
                except requests.exceptions.HTTPError:
                    # For 4xx, don't retry; raise structured error
                    status = getattr(resp, "status_code", 0)
                    if 400 <= status < 500:
                        try:
                            data = resp.json()
                            msg = data.get("error") or data.get("reason") or resp.text
                        except Exception:
                            msg = resp.text
                        raise ApiClient.ApiError(status, str(msg))
                    raise
                return resp.json()
            except requests.exceptions.RequestException as e:
                last_exc = e
                if attempt >= self.retries:
                    break
                time.sleep(0.5 * (2**attempt))
        assert last_exc is not None
        raise last_exc

    def new_game(self, scenario: int, player_id: str) -> NewGameResponse:
        data = self._get(
            "/new-game",
            {
                "scenario": scenario,
                "playerId": player_id,
            },
        )
        constraints = [
            Constraint(attribute=c["attribute"], minCount=int(c["minCount"]))
            for c in data["constraints"]
        ]
        stats = data["attributeStatistics"]
        attribute_statistics = AttributeStatistics(
            relativeFrequencies=stats.get("relativeFrequencies", {}),
            correlations=stats.get("correlations", {}),
        )
        return NewGameResponse(
            gameId=data["gameId"],
            constraints=constraints,
            attributeStatistics=attribute_statistics,
        )

    def decide_and_next(
        self, game_id: str, person_index: int, accept: Optional[bool] = None
    ) -> DecideAndNextResponse:
        params: Dict[str, object] = {"gameId": game_id, "personIndex": person_index}
        if accept is not None:
            params["accept"] = str(accept).lower()

        data = self._get("/decide-and-next", params)

        status = data.get("status")
        if status == "running":
            np = data.get("nextPerson")
            person = None
            if np is not None:
                person = Person(personIndex=np["personIndex"], attributes=np.get("attributes", {}))
            return DecideAndNextRunning(
                status="running",
                admittedCount=int(data.get("admittedCount", 0)),
                rejectedCount=int(data.get("rejectedCount", 0)),
                nextPerson=person,
            )
        if status == "completed":
            return DecideAndNextCompleted(
                status="completed", rejectedCount=int(data.get("rejectedCount", 0)), nextPerson=None
            )
        if status == "failed":
            return DecideAndNextFailed(
                status="failed", reason=str(data.get("reason", "unknown")), nextPerson=None
            )

        raise ValueError(f"Unexpected response: {data}")
