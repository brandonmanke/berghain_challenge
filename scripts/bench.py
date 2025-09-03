#!/usr/bin/env python3
import argparse
import os
import sys
from typing import List


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


def parse_args(argv: List[str]):
    p = argparse.ArgumentParser(description="Bench multiple scenarios")
    p.add_argument("--scenarios", default="1,2,3", help="Comma-separated scenario list")
    p.add_argument("--capacity", type=int, default=1000)
    p.add_argument("--timeout", type=float, default=float(os.getenv("TIMEOUT", "30")))
    p.add_argument("--retries", type=int, default=int(os.getenv("RETRIES", "3")))
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--json-out", default=os.getenv("BENCH_JSON"), help="Write summary JSON to file")
    p.add_argument(
        "--policy",
        choices=["reserve", "window", "ewma", "attr-ewma"],
        default=os.getenv("POLICY", "reserve"),
    )
    return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    _load_dotenv()
    argv = argv or sys.argv[1:]
    ns = parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.abspath(os.path.join(here, "..", "src"))
    if src not in sys.path:
        sys.path.insert(0, src)

    from berghain.runner import run_game

    base_url = os.getenv("BASE_URL", "https://berghain.challenges.listenlabs.ai/")
    player_id = os.getenv("PLAYER_ID")
    if not player_id:
        print("Missing PLAYER_ID in env/.env")
        return 2

    scenarios = [int(s.strip()) for s in ns.scenarios.split(",") if s.strip()]
    print(f"Bench start: scenarios={scenarios} capacity={ns.capacity}")
    results = {}
    for s in scenarios:
        try:
            rejected, remaining = run_game(
                base_url=base_url,
                player_id=player_id,
                scenario=s,
                capacity=ns.capacity,
                timeout=ns.timeout,
                retries=ns.retries,
                verbose=ns.verbose,
                progress_interval=200,
                progress_attrs=3,
                policy_name=ns.policy,
            )
            results[s] = {"rejected": rejected, "remaining": remaining}
            print(f"Scenario {s}: rejected={rejected} remaining={remaining}")
        except Exception as e:
            print(f"Scenario {s} failed: {e}")
            results[s] = {"error": str(e)}

    # Default JSON output name if not provided
    if not ns.json_out:
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ns.json_out = f"bench-{ns.policy}-{ts}.json"

    print("\nSummary:")
    for s in scenarios:
        r = results.get(s, {})
        if "rejected" in r:
            print(f"  s{s}: rejected={r['rejected']} remaining={r['remaining']}")
        else:
            print(f"  s{s}: error={r.get('error')}")
    # Optional JSON output
    if ns.json_out:
        import json

        out = {str(k): v for k, v in results.items()}
        with open(ns.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
