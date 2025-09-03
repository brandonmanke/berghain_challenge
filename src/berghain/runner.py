from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

from .client import ApiClient
from .logging_utils import JsonLinesLogger
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


def parse_args(argv: List[str]) -> argparse.Namespace:
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
    p.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("TIMEOUT", "30")),
        help="HTTP timeout seconds",
    )
    p.add_argument(
        "--retries", type=int, default=int(os.getenv("RETRIES", "3")), help="HTTP retries on error"
    )
    p.add_argument(
        "--progress-interval",
        type=int,
        default=int(os.getenv("PROGRESS_INTERVAL", "100")),
        help="Print progress every N people when --verbose",
    )
    p.add_argument(
        "--progress-attrs",
        type=int,
        default=int(os.getenv("PROGRESS_ATTRS", "3")),
        help="Show top-K remaining-needed attributes",
    )
    p.add_argument(
        "--log-json",
        default=os.getenv("LOG_JSON"),
        help="Write newline-delimited JSON progress to file",
    )
    p.add_argument(
        "--log-interval",
        type=int,
        default=int(os.getenv("LOG_INTERVAL", "100")),
        help="Log JSON every N steps",
    )
    p.add_argument(
        "--policy",
        choices=["reserve", "window", "ewma"],
        default=os.getenv("POLICY", "reserve"),
        help="Policy type",
    )
    # Resume options
    p.add_argument(
        "--resume-from-log", default=os.getenv("RESUME_LOG"), help="Resume run from NDJSON log file"
    )
    p.add_argument(
        "--game-id",
        default=os.getenv("GAME_ID"),
        help="Resume target gameId (overrides log if set)",
    )
    p.add_argument(
        "--start-index",
        type=int,
        default=None,
        help="Resume starting personIndex (overrides log if set)",
    )
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def run_game(
    *,
    base_url: str,
    player_id: str,
    scenario: int,
    capacity: int,
    timeout: float,
    retries: int,
    verbose: bool = False,
    progress_interval: int = 100,
    progress_attrs: int = 3,
    log_json: Optional[str] = None,
    log_interval: int = 100,
    policy_name: str = "reserve",
) -> Tuple[int, Dict[str, int]]:
    client = ApiClient(base_url, timeout=timeout, retries=retries)
    new_game = client.new_game(scenario, player_id)

    min_counts: Dict[str, int] = {c.attribute: int(c.minCount) for c in new_game.constraints}
    if policy_name == "reserve":
        policy = QuotaReservePolicy(min_counts=min_counts, capacity=capacity)
    elif policy_name == "window":
        from .window_policy import WindowRelaxedPolicy  # type: ignore

        policy = WindowRelaxedPolicy(min_counts=min_counts, capacity=capacity)
    elif policy_name == "ewma":
        from .ewma_policy import EwmaRelaxedPolicy  # type: ignore

        policy = EwmaRelaxedPolicy(min_counts=min_counts, capacity=capacity)
    else:
        raise ValueError(f"Unknown policy: {policy_name}")

    # Fetch the first person (no decision yet)
    resp = client.decide_and_next(game_id=new_game.gameId, person_index=0, accept=None)

    jlogger = JsonLinesLogger(log_json) if log_json else None
    # Start log record
    if jlogger:
        jlogger.start(
            scenario=scenario,
            game_id=new_game.gameId,
            capacity=capacity,
            constraints=min_counts,
        )

    step = 0
    admitted = 0
    rejected = 0
    while True:
        if getattr(resp, "status", None) == "failed":
            reason = getattr(resp, "reason", "unknown")
            if jlogger:
                jlogger.failed(scenario=scenario, reason=str(reason))
            raise RuntimeError(f"Game failed: {reason}")
        if getattr(resp, "status", None) == "completed":
            rejected = int(getattr(resp, "rejectedCount", 0))
            remaining = policy.remaining_needed()
            if jlogger:
                jlogger.completed(scenario=scenario, rejected_count=rejected, remaining=remaining)
            return rejected, remaining

        # running
        running = resp  # type: ignore[assignment]
        person = running.nextPerson
        if person is None:
            raise RuntimeError("No next person provided; aborting.")

        admitted = int(running.admittedCount)
        rejected = int(running.rejectedCount)

        accept = policy.decide(admitted_count=admitted, attributes=person.attributes)

        next_index = int(person.personIndex) + 1

        if jlogger:
            jlogger.request(
                scenario=scenario,
                send_person_index=next_index,
                decide_for_index=int(person.personIndex),
                decide_for_attrs=dict(person.attributes),
                accept=bool(accept),
            )

        # Submit decision for current person; fetch next person by next_index
        try:
            resp = client.decide_and_next(
                game_id=new_game.gameId,
                person_index=next_index,
                accept=accept,
            )
        except Exception as e:
            # Try to resync if server tells us the expected index
            msg = str(e)
            import re

            m = re.search(r"Expected person (\d+), got (\d+)", msg)
            if m:
                expected = int(m.group(1))
                got = int(m.group(2))
                if jlogger:
                    jlogger.resync(scenario=scenario, expected=expected, got=got)
                resp = client.decide_and_next(
                    game_id=new_game.gameId,
                    person_index=expected,
                    accept=accept,
                )
            else:
                raise

        # Update local state if we accepted
        if accept:
            policy.update_on_accept(person.attributes)

        step += 1
        if verbose and progress_interval > 0 and step % progress_interval == 0:
            rem = policy.remaining_needed()
            # Show top-K attributes by remaining needed
            top = sorted(rem.items(), key=lambda kv: kv[1], reverse=True)
            top = [(k, v) for k, v in top if v > 0][: max(0, progress_attrs)]
            top_str = ", ".join(f"{k}:{v}" for k, v in top) if top else "ok"
            remaining_capacity = max(0, capacity - admitted)
            print(
                f"step={step} idx={person.personIndex} adm={admitted} rej={rejected} la={accept} "
                f"cap_left={remaining_capacity} need_sum={sum(rem.values())} top=[{top_str}]",
                flush=True,
            )
        if jlogger and log_interval > 0 and step % log_interval == 0:
            rem = policy.remaining_needed()
            top = sorted(rem.items(), key=lambda kv: kv[1], reverse=True)
            top = [(k, v) for k, v in top if v > 0][: max(0, progress_attrs)]
            # Log response metadata and progress snapshot
            jlogger.response(
                scenario=scenario,
                admitted=admitted,
                rejected=rejected,
                status=getattr(resp, "status", "unknown"),
                next_person_index=getattr(getattr(resp, "nextPerson", None), "personIndex", None),
            )
            jlogger.progress(
                scenario=scenario,
                step=step,
                admitted=admitted,
                rejected=rejected,
                cap_left=max(0, capacity - admitted),
                need_sum=sum(rem.values()),
                top_remaining=top,
                accept=bool(accept),
                person_index=int(person.personIndex),
            )


def main(argv: Optional[List[str]] = None) -> int:
    _load_dotenv()
    ns = parse_args(argv or sys.argv[1:])

    if not ns.player_id:
        print("Missing --player-id and env PLAYER_ID. Set PLAYER_ID in .env or pass the flag.")
        return 2
    if not ns.base_url:
        print("Missing --base-url and env BASE_URL.")
        return 2

    try:
        if ns.resume_from_log or ns.game_id or ns.start_index is not None:
            rejected, remaining = resume_game(
                base_url=ns.base_url,
                player_id=ns.player_id,
                scenario=ns.scenario,
                capacity=ns.capacity,
                timeout=ns.timeout,
                retries=ns.retries,
                verbose=ns.verbose,
                progress_interval=ns.progress_interval,
                progress_attrs=ns.progress_attrs,
                log_json=ns.log_json,
                log_interval=ns.log_interval,
                policy_name=ns.policy,
                resume_from_log=ns.resume_from_log,
                override_game_id=ns.game_id,
                override_start_index=ns.start_index,
            )
        else:
            rejected, remaining = run_game(
                base_url=ns.base_url,
                player_id=ns.player_id,
                scenario=ns.scenario,
                capacity=ns.capacity,
                timeout=ns.timeout,
                retries=ns.retries,
                verbose=ns.verbose,
                progress_interval=ns.progress_interval,
                progress_attrs=ns.progress_attrs,
                log_json=ns.log_json,
                log_interval=ns.log_interval,
                policy_name=ns.policy,
            )
    except Exception as e:
        print(str(e))
        return 3

    status = "satisfied" if sum(remaining.values()) == 0 else "unsatisfied"
    print(f"Completed. Rejected: {rejected}. Constraints: {status}. Remaining: {remaining}")
    return 0


def _reconstruct_from_log(path: str):
    import json

    last_start = None
    buffer = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("event") == "start":
                last_start = rec
                buffer = [rec]
            elif last_start is not None:
                buffer.append(rec)

    if last_start is None:
        raise RuntimeError("No start event found in log")

    constraints = last_start.get("constraints", {})
    capacity = int(last_start.get("capacity", 1000))
    game_id = last_start.get("gameId")
    scenario = int(last_start.get("scenario", 1))

    # Build accepted attribute counts and find last decisions
    accepted_counts: Dict[str, int] = {k: 0 for k in constraints.keys()}
    last_request = None
    last_response = None
    for rec in buffer:
        ev = rec.get("event")
        if ev == "request":
            last_request = rec
            if rec.get("accept"):
                attrs = rec.get("decide_for_attrs", {})
                for a, v in attrs.items():
                    if v:
                        accepted_counts[a] = accepted_counts.get(a, 0) + 1
        elif ev == "response":
            last_response = rec

    if last_response and last_response.get("next_person_index") is not None:
        next_index = int(last_response.get("next_person_index"))
        prev_accept = bool(last_request.get("accept")) if last_request else False
    elif last_request is not None:
        next_index = int(last_request.get("send_person_index"))
        prev_accept = bool(last_request.get("accept"))
    else:
        raise RuntimeError("No request/response events to infer next index")

    return {
        "constraints": constraints,
        "capacity": capacity,
        "game_id": game_id,
        "scenario": scenario,
        "accepted_counts": accepted_counts,
        "next_index": next_index,
        "prev_accept": prev_accept,
        "events": buffer,
    }


def resume_game(
    *,
    base_url: str,
    player_id: str,
    scenario: int,
    capacity: int,
    timeout: float,
    retries: int,
    verbose: bool,
    progress_interval: int,
    progress_attrs: int,
    log_json: Optional[str],
    log_interval: int,
    policy_name: str,
    resume_from_log: Optional[str],
    override_game_id: Optional[str],
    override_start_index: Optional[int],
) -> Tuple[int, Dict[str, int]]:
    if not resume_from_log and (not override_game_id or override_start_index is None):
        raise RuntimeError("Provide --resume-from-log or both --game-id and --start-index")

    if resume_from_log:
        state = _reconstruct_from_log(resume_from_log)
        constraints = {str(k): int(v) for k, v in state["constraints"].items()}
        capacity = int(state["capacity"]) or capacity
        game_id = override_game_id or state["game_id"]
        start_index = (
            override_start_index if override_start_index is not None else int(state["next_index"])
        )
        prev_accept = bool(state["prev_accept"])
        accepted_counts = dict(state["accepted_counts"])
        scenario = int(state["scenario"]) or scenario
        events = list(state.get("events", []))
    else:
        # Manual resume; constraints unknown. Use empty constraints or supply via env.
        constraints = {}
        game_id = override_game_id
        start_index = int(override_start_index)
        prev_accept = False
        accepted_counts = {}

    if not game_id:
        raise RuntimeError("Missing gameId to resume. Use --game-id or --resume-from-log")

    # Build policy and preload counts
    if policy_name == "reserve":
        policy = QuotaReservePolicy(min_counts=constraints, capacity=capacity)
    elif policy_name == "window":
        from .window_policy import WindowRelaxedPolicy  # type: ignore

        policy = WindowRelaxedPolicy(min_counts=constraints, capacity=capacity)
    elif policy_name == "ewma":
        from .ewma_policy import EwmaRelaxedPolicy  # type: ignore

        policy = EwmaRelaxedPolicy(min_counts=constraints, capacity=capacity)
    else:
        raise ValueError(f"Unknown policy: {policy_name}")

    # Prime policy state from log events to restore EWMA/window statistics
    # We recompute "helpful" at each logged request using the constraints
    # and the running accepted counts as of that moment.
    policy.accepted_attribute_counts.clear()
    # Initialize counts to zero, then replay
    for rec in events if resume_from_log else []:
        if rec.get("event") == "request":
            attrs = rec.get("decide_for_attrs", {})
            # Helpful if contributes to any underfilled attribute at that time
            helpful = False
            for a, m in constraints.items():
                have = policy.accepted_attribute_counts.get(a, 0)
                need = max(0, m - have)
                if need > 0 and bool(attrs.get(a, False)):
                    helpful = True
                    break
            # Update streaming estimate regardless of accept
            try:
                policy.record_observation(helpful)  # type: ignore[attr-defined]
            except Exception:
                pass
            # Apply acceptance effects to counts to maintain chronology
            if bool(rec.get("accept")):
                for a, v in attrs.items():
                    if v:
                        policy.accepted_attribute_counts[a] = (
                            policy.accepted_attribute_counts.get(a, 0) + 1
                        )
        # Ignore other events for priming
    # Ensure we at least match accepted_counts computed directly
    for a, c in accepted_counts.items():
        if policy.accepted_attribute_counts.get(a, 0) < c:
            policy.accepted_attribute_counts[a] = c

    client = ApiClient(base_url, timeout=timeout, retries=retries)
    jlogger = JsonLinesLogger(log_json) if log_json else None
    if jlogger:
        jlogger.start(
            scenario=scenario, game_id=game_id, capacity=capacity, constraints=constraints
        )

    # First call after resume uses last accept and the next expected index
    try:
        resp = client.decide_and_next(game_id=game_id, person_index=start_index, accept=prev_accept)
    except Exception as e:
        # Resync once if needed
        msg = str(e)
        import re

        m = re.search(r"Expected person (\d+), got (\d+)", msg)
        if m:
            expected = int(m.group(1))
            if jlogger:
                jlogger.resync(scenario=scenario, expected=expected, got=int(m.group(2)))
            resp = client.decide_and_next(
                game_id=game_id, person_index=expected, accept=prev_accept
            )
        else:
            raise

    # Continue with the normal loop
    step = 0
    admitted = 0
    rejected = 0
    while True:
        if getattr(resp, "status", None) == "failed":
            reason = getattr(resp, "reason", "unknown")
            if jlogger:
                jlogger.failed(scenario=scenario, reason=str(reason))
            raise RuntimeError(f"Game failed: {reason}")
        if getattr(resp, "status", None) == "completed":
            rejected = int(getattr(resp, "rejectedCount", 0))
            remaining = policy.remaining_needed()
            if jlogger:
                jlogger.completed(scenario=scenario, rejected_count=rejected, remaining=remaining)
            return rejected, remaining

        running = resp  # type: ignore[assignment]
        person = running.nextPerson
        if person is None:
            raise RuntimeError("No next person provided; aborting.")

        admitted = int(running.admittedCount)
        rejected = int(running.rejectedCount)

        accept = policy.decide(admitted_count=admitted, attributes=person.attributes)

        next_index = int(person.personIndex) + 1

        if jlogger:
            jlogger.request(
                scenario=scenario,
                send_person_index=next_index,
                decide_for_index=int(person.personIndex),
                decide_for_attrs=dict(person.attributes),
                accept=bool(accept),
            )

        try:
            resp = client.decide_and_next(game_id=game_id, person_index=next_index, accept=accept)
        except Exception as e:
            msg = str(e)
            import re

            m = re.search(r"Expected person (\d+), got (\d+)", msg)
            if m:
                expected = int(m.group(1))
                if jlogger:
                    jlogger.resync(scenario=scenario, expected=expected, got=int(m.group(2)))
                resp = client.decide_and_next(game_id=game_id, person_index=expected, accept=accept)
            else:
                raise

        if accept:
            policy.update_on_accept(person.attributes)

        step += 1
        if verbose and progress_interval > 0 and step % progress_interval == 0:
            rem = policy.remaining_needed()
            top = sorted(rem.items(), key=lambda kv: kv[1], reverse=True)
            top = [(k, v) for k, v in top if v > 0][: max(0, progress_attrs)]
            top_str = ", ".join(f"{k}:{v}" for k, v in top) if top else "ok"
            remaining_capacity = max(0, capacity - admitted)
            print(
                f"step={step} idx={person.personIndex} adm={admitted} rej={rejected} la={accept} "
                f"cap_left={remaining_capacity} need_sum={sum(rem.values())} top=[{top_str}]",
                flush=True,
            )
        if jlogger and log_interval > 0 and step % log_interval == 0:
            rem = policy.remaining_needed()
            top = sorted(rem.items(), key=lambda kv: kv[1], reverse=True)
            top = [(k, v) for k, v in top if v > 0][: max(0, progress_attrs)]
            jlogger.response(
                scenario=scenario,
                admitted=admitted,
                rejected=rejected,
                status=getattr(resp, "status", "unknown"),
                next_person_index=getattr(getattr(resp, "nextPerson", None), "personIndex", None),
            )
            jlogger.progress(
                scenario=scenario,
                step=step,
                admitted=admitted,
                rejected=rejected,
                cap_left=max(0, capacity - admitted),
                need_sum=sum(rem.values()),
                top_remaining=top,
                accept=bool(accept),
                person_index=int(person.personIndex),
            )


if __name__ == "__main__":
    raise SystemExit(main())
