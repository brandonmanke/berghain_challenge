from __future__ import annotations

import argparse
import os
import sys
from typing import Dict

from .client import ApiClient
from .policy import QuotaReservePolicy


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, ignore comments/blank.
    Only sets variables not already in the environment.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Berghain Challenge Agent")
    p.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "https://berghain.challenges.listenlabs.ai/"),
        help="API base URL (env: BASE_URL)",
    )
    p.add_argument(
        "--player-id",
        default=os.getenv("PLAYER_ID"),
        help="UUID identifying the player (env: PLAYER_ID)",
    )
    p.add_argument("--scenario", type=int, default=1, choices=[1, 2, 3])
    p.add_argument("--capacity", type=int, default=1000, help="Venue capacity (default 1000)")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    ns = parse_args(argv or sys.argv[1:])

    if not ns.player_id:
        print("Missing --player-id and env PLAYER_ID. Set PLAYER_ID in .env or pass the flag.")
        return 2
    if not ns.base_url:
        print("Missing --base-url and env BASE_URL.")
        return 2

    client = ApiClient(ns.base_url)
    new_game = client.new_game(ns.scenario, ns.player_id)

    min_counts: Dict[str, int] = {c.attribute: int(c.minCount) for c in new_game.constraints}
    policy = QuotaReservePolicy(min_counts=min_counts, capacity=ns.capacity)

    # Fetch the first person (no decision yet)
    resp = client.decide_and_next(game_id=new_game.gameId, person_index=0, accept=None)

    step = 0
    while True:
        if getattr(resp, "status", None) == "failed":
            print(f"Game failed: {getattr(resp, 'reason', 'unknown')}")
            return 2
        if getattr(resp, "status", None) == "completed":
            print(f"Completed. Rejected: {getattr(resp, 'rejectedCount', -1)}")
            return 0

        # running
        running = resp  # type: ignore[assignment]
        person = running.nextPerson
        if person is None:
            print("No next person provided; aborting.")
            return 3

        accept = policy.decide(admitted_count=running.admittedCount, attributes=person.attributes)

        # Submit decision; next call returns the next person
        resp = client.decide_and_next(game_id=new_game.gameId, person_index=person.personIndex, accept=accept)

        # Update local state if we accepted
        if accept:
            policy.update_on_accept(person.attributes)

        step += 1
        if ns.verbose and step % 100 == 0:
            print(
                f"step={step} admitted={running.admittedCount} rejected={running.rejectedCount} last_accept={accept}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
