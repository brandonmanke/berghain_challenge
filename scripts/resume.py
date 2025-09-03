#!/usr/bin/env python3
import argparse
import os
import sys


def _load_dotenv(path: str = ".env") -> None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def parse_args(argv):
    p = argparse.ArgumentParser(description="Resume a game from NDJSON logs or index")
    p.add_argument(
        "--log",
        default=None,
        help="NDJSON log (default: latest logs/*.ndjson)",
    )
    p.add_argument(
        "--policy",
        choices=["reserve", "window", "ewma"],
        default=os.getenv("POLICY", "reserve"),
    )
    p.add_argument("--timeout", type=float, default=float(os.getenv("TIMEOUT", "30")))
    p.add_argument("--retries", type=int, default=int(os.getenv("RETRIES", "3")))
    p.add_argument("--verbose", action="store_true")
    p.add_argument(
        "--progress-interval", type=int, default=int(os.getenv("PROGRESS_INTERVAL", "100"))
    )
    p.add_argument("--progress-attrs", type=int, default=int(os.getenv("PROGRESS_ATTRS", "3")))
    # Manual override
    p.add_argument("--game-id", default=os.getenv("GAME_ID"))
    p.add_argument("--start-index", type=int, default=None)
    p.add_argument("--capacity", type=int, default=int(os.getenv("CAPACITY", "1000")))
    p.add_argument("--json-log", default=os.getenv("LOG_JSON"))
    p.add_argument("--log-interval", type=int, default=int(os.getenv("LOG_INTERVAL", "100")))
    return p.parse_args(argv)


def main(argv=None) -> int:
    _load_dotenv()
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.abspath(os.path.join(here, "..", "src"))
    if src not in sys.path:
        sys.path.insert(0, src)

    from berghain.runner import resume_game

    ns = parse_args(argv or sys.argv[1:])

    base_url = os.getenv("BASE_URL", "https://berghain.challenges.listenlabs.ai/")
    player_id = os.getenv("PLAYER_ID")
    if not player_id:
        print("Missing PLAYER_ID in env/.env")
        return 2

    # Default to latest logs/*.ndjson if not provided
    log = ns.log
    if log is None:
        try:
            import glob

            candidates = sorted(glob.glob("logs/*.ndjson"), key=os.path.getmtime, reverse=True)
            log = candidates[0]
            print(f"Using latest log: {log}")
        except Exception:
            log = None

    rejected, remaining = resume_game(
        base_url=base_url,
        player_id=player_id,
        scenario=1,  # scenario is inferred from log where possible
        capacity=ns.capacity,
        timeout=ns.timeout,
        retries=ns.retries,
        verbose=ns.verbose,
        progress_interval=ns.progress_interval,
        progress_attrs=ns.progress_attrs,
        log_json=ns.json_log,
        log_interval=ns.log_interval,
        policy_name=ns.policy,
        resume_from_log=log,
        override_game_id=ns.game_id,
        override_start_index=ns.start_index,
    )

    status = "satisfied" if sum(remaining.values()) == 0 else "unsatisfied"
    print(f"Resumed complete. Rejected: {rejected}. Constraints: {status}. Remaining: {remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
