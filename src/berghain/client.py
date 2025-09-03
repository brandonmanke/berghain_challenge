from __future__ import annotations

import logging
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


log = logging.getLogger(__name__)


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 10.0, session: Optional[requests.Session] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get(self, path: str, params: Dict[str, object]) -> dict:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def new_game(self, scenario: int, player_id: str) -> NewGameResponse:
        data = self._get(
            "/new-game",
            {
                "scenario": scenario,
                "playerId": player_id,
            },
        )
        constraints = [Constraint(attribute=c["attribute"], minCount=int(c["minCount"])) for c in data["constraints"]]
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
            return DecideAndNextCompleted(status="completed", rejectedCount=int(data.get("rejectedCount", 0)), nextPerson=None)
        if status == "failed":
            return DecideAndNextFailed(status="failed", reason=str(data.get("reason", "unknown")), nextPerson=None)

        raise ValueError(f"Unexpected response: {data}")
