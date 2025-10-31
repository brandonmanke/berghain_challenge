#!/usr/bin/env python3
import argparse
import os
import sys
from typing import List


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
    # Policy tuning parameters
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
    return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.abspath(os.path.join(here, "..", "src"))
    if src not in sys.path:
        sys.path.insert(0, src)

    from berghain.runner import run_game
    from berghain.utils import load_dotenv

    load_dotenv()
    argv = argv or sys.argv[1:]
    ns = parse_args(argv)

    base_url = os.getenv("BASE_URL", "https://berghain.challenges.listenlabs.ai/")
    player_id = os.getenv("PLAYER_ID")
    if not player_id:
        print("Missing PLAYER_ID in env/.env")
        return 2

    scenarios = [int(s.strip()) for s in ns.scenarios.split(",") if s.strip()]
    print(f"Bench start: scenarios={scenarios} capacity={ns.capacity} policy={ns.policy}")
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
                alpha=ns.alpha,
                risk_margin=ns.risk_margin,
                warmup=ns.warmup,
                window_size=ns.window_size,
                min_observations=ns.min_observations,
                gate_top_k=ns.gate_top_k,
                corr_aware=ns.corr_aware,
                corr_beta=ns.corr_beta,
                corr_include_neg=ns.corr_include_neg,
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
