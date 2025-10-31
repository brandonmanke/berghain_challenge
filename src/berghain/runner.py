from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .client import ApiClient
from .logging_utils import JsonLinesLogger
from .policy import QuotaReservePolicy
from .utils import load_dotenv


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
        choices=["reserve", "window", "ewma", "attr-ewma"],
        default=os.getenv("POLICY", "reserve"),
        help="Policy type",
    )
    # Policy tuning knobs (optional)
    p.add_argument("--alpha", type=float, default=None, help="Smoothing factor for EWMA policies")
    p.add_argument(
        "--risk-margin", type=float, default=None, help="Safety margin for relaxed policies"
    )
    p.add_argument(
        "--warmup", type=int, default=None, help="Warmup observations before relaxing gates"
    )
    p.add_argument("--window-size", type=int, default=None, help="Window size for window policy")
    p.add_argument(
        "--min-observations",
        type=int,
        default=None,
        help="Minimum observations before relaxing window policy",
    )
    p.add_argument("--gate-top-k", type=int, default=None, help="Gate only top-K underfilled attrs")
    p.add_argument("--corr-aware", action="store_true", help="Enable correlation-aware expectation")
    p.add_argument(
        "--corr-beta", type=float, default=None, help="Scale for correlation inflation (0-1)"
    )
    p.add_argument(
        "--corr-include-neg", action="store_true", help="Include negative correlations in averaging"
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


def _default_log_path(prefix: str, policy: str, scenario: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_policy = (policy or "policy").replace("/", "-")
    return os.path.join("logs", f"{prefix}-{safe_policy}-s{scenario}-{ts}.ndjson")


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
    alpha: Optional[float] = None,
    risk_margin: Optional[float] = None,
    warmup: Optional[int] = None,
    window_size: Optional[int] = None,
    min_observations: Optional[int] = None,
    gate_top_k: Optional[int] = None,
    corr_aware: bool = False,
    corr_beta: Optional[float] = None,
    corr_include_neg: bool = False,
) -> Tuple[int, Dict[str, int]]:
    client = ApiClient(base_url, timeout=timeout, retries=retries)
    new_game = client.new_game(scenario, player_id)

    min_counts: Dict[str, int] = {c.attribute: int(c.minCount) for c in new_game.constraints}
    if policy_name == "reserve":
        policy = QuotaReservePolicy(min_counts=min_counts, capacity=capacity)
    elif policy_name == "window":
        from .window_policy import WindowRelaxedPolicy  # type: ignore

        policy = WindowRelaxedPolicy(
            min_counts=min_counts,
            capacity=capacity,
            window_size=window_size or 500,
            risk_margin=risk_margin if risk_margin is not None else 0.15,
            min_observations=min_observations or 100,
        )
    elif policy_name == "ewma":
        from .ewma_policy import EwmaRelaxedPolicy  # type: ignore

        policy = EwmaRelaxedPolicy(
            min_counts=min_counts,
            capacity=capacity,
            alpha=alpha if alpha is not None else 0.03,
            risk_margin=risk_margin if risk_margin is not None else 0.15,
            warmup_observations=warmup or 100,
        )
    elif policy_name == "attr-ewma":
        from .attr_ewma_policy import AttributeEwmaPolicy  # type: ignore

        prior_freqs = (
            new_game.attributeStatistics.relativeFrequencies
            if hasattr(new_game, "attributeStatistics")
            else {}
        )
        policy = AttributeEwmaPolicy(
            min_counts=min_counts,
            capacity=capacity,
            alpha=alpha if alpha is not None else 0.04,
            risk_margin=risk_margin if risk_margin is not None else 0.20,
            warmup_observations=warmup or 200,
            prior_freqs=prior_freqs,
            gate_top_k=gate_top_k,
            correlations=(
                new_game.attributeStatistics.correlations
                if (hasattr(new_game, "attributeStatistics") and corr_aware)
                else None
            ),
            corr_beta=corr_beta if corr_beta is not None else 0.25,
            corr_include_negative=bool(corr_include_neg),
        )
    else:
        raise ValueError(f"Unknown policy: {policy_name}")

    # Fetch the first person (no decision yet)
    resp = client.decide_and_next(game_id=new_game.gameId, person_index=0, accept=None)

    jlogger = JsonLinesLogger(log_json) if log_json else None
    # Start log record
    if jlogger:
        prior_freqs = (
            new_game.attributeStatistics.relativeFrequencies
            if hasattr(new_game, "attributeStatistics")
            else None
        )
        correlations = (
            new_game.attributeStatistics.correlations
            if hasattr(new_game, "attributeStatistics")
            else None
        )
        jlogger.start(
            scenario=scenario,
            game_id=new_game.gameId,
            capacity=capacity,
            constraints=min_counts,
            prior_freqs=prior_freqs,
            correlations=correlations,
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
    load_dotenv()
    ns = parse_args(argv or sys.argv[1:])

    if not ns.player_id:
        print("Missing --player-id and env PLAYER_ID. Set PLAYER_ID in .env or pass the flag.")
        return 2
    if not ns.base_url:
        print("Missing --base-url and env BASE_URL.")
        return 2

    try:
        if ns.resume_from_log or ns.game_id or ns.start_index is not None:
            # Default resume log path if none provided
            if not ns.log_json:
                ns.log_json = _default_log_path("resume", ns.policy, ns.scenario)
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
                alpha=ns.alpha,
                risk_margin=ns.risk_margin,
                warmup=ns.warmup,
                window_size=ns.window_size,
                min_observations=ns.min_observations,
                gate_top_k=ns.gate_top_k,
                corr_aware=bool(ns.corr_aware),
                corr_beta=ns.corr_beta,
                corr_include_neg=bool(ns.corr_include_neg),
            )
        else:
            # Default run log path if none provided
            if not ns.log_json:
                ns.log_json = _default_log_path("run", ns.policy, ns.scenario)
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
                alpha=ns.alpha,
                risk_margin=ns.risk_margin,
                warmup=ns.warmup,
                window_size=ns.window_size,
                min_observations=ns.min_observations,
                gate_top_k=ns.gate_top_k,
                corr_aware=bool(ns.corr_aware),
                corr_beta=ns.corr_beta,
                corr_include_neg=bool(ns.corr_include_neg),
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
    prior_freqs = last_start.get("prior_freqs", {})
    correlations = last_start.get("correlations", {})

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
        "prior_freqs": prior_freqs,
        "correlations": correlations,
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
    alpha: Optional[float],
    risk_margin: Optional[float],
    warmup: Optional[int],
    window_size: Optional[int],
    min_observations: Optional[int],
    gate_top_k: Optional[int] = None,
    corr_aware: bool = False,
    corr_beta: Optional[float] = None,
    corr_include_neg: bool = False,
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
        prior_freqs = state.get("prior_freqs", {})
        correlations_from_log = state.get("correlations", {})
    else:
        # Manual resume; constraints unknown. Use empty constraints or supply via env.
        constraints = {}
        game_id = override_game_id
        start_index = int(override_start_index)
        prev_accept = False
        accepted_counts = {}
        events = []
        prior_freqs = {}
        correlations_from_log = {}

    if not game_id:
        raise RuntimeError("Missing gameId to resume. Use --game-id or --resume-from-log")

    # Build policy and preload counts
    if policy_name == "reserve":
        policy = QuotaReservePolicy(min_counts=constraints, capacity=capacity)
    elif policy_name == "window":
        from .window_policy import WindowRelaxedPolicy  # type: ignore

        policy = WindowRelaxedPolicy(
            min_counts=constraints,
            capacity=capacity,
            window_size=window_size or 500,
            risk_margin=risk_margin if risk_margin is not None else 0.15,
            min_observations=min_observations or 100,
        )
    elif policy_name == "ewma":
        from .ewma_policy import EwmaRelaxedPolicy  # type: ignore

        policy = EwmaRelaxedPolicy(
            min_counts=constraints,
            capacity=capacity,
            alpha=alpha if alpha is not None else 0.03,
            risk_margin=risk_margin if risk_margin is not None else 0.15,
            warmup_observations=warmup or 100,
        )
    elif policy_name == "attr-ewma":
        from .attr_ewma_policy import AttributeEwmaPolicy  # type: ignore

        # Use priors from log if available (requires logging enhancement)
        policy = AttributeEwmaPolicy(
            min_counts=constraints,
            capacity=capacity,
            alpha=alpha if alpha is not None else 0.04,
            risk_margin=risk_margin if risk_margin is not None else 0.20,
            warmup_observations=warmup or 200,
            prior_freqs=prior_freqs if prior_freqs else None,
            gate_top_k=gate_top_k,
            correlations=correlations_from_log if correlations_from_log else None,
            corr_beta=corr_beta if corr_beta is not None else 0.25,
            corr_include_negative=bool(corr_include_neg),
        )
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
