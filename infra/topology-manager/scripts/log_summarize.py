"""Produce result/log/summary.json from the accumulated run_log.jsonl.

Manual entry point: python3 scripts/log_summarize.py [--chaincode smallbank]
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "result" / "log"
JSONL_PATH = LOG_DIR / "run_log.jsonl"
SUMMARY_PATH = LOG_DIR / "summary.json"


def main():
    parser = argparse.ArgumentParser(description='Summarize run_log.jsonl')
    parser.add_argument('--chaincode', default=None, help='Filter by benchmark name')
    args = parser.parse_args()

    if not JSONL_PATH.exists():
        print(f"[log_summarize] {JSONL_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    entries = []
    with open(JSONL_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))

    if not entries:
        print("[log_summarize] No entries in run_log.jsonl.", file=sys.stderr)
        sys.exit(1)

    if args.chaincode:
        entries = [e for e in entries if e.get("benchmark") == args.chaincode]
        if not entries:
            print(f"[log_summarize] No entries for benchmark={args.chaincode}.", file=sys.stderr)
            sys.exit(1)

    best = max(entries, key=lambda e: e["eff_tps"])

    summary = {
        "benchmark": best["benchmark"],
        "peer_count": best["peer_count"],
        "budget": None,
        "best_eff_tps": best["eff_tps"],
        "best_raw_tps": best["raw_tps"],
        "best_succ_rate": best["succ_rate"],
        "best_config": best["config"],
        "best_step": best["step"],
        "invalid_config_count": sum(1 for e in entries if e.get("invalid")),
        "total_steps": len(entries),
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"[log_summarize] best_eff_tps={best['eff_tps']:.2f}  best_step={best['step']}  "
          f"total_steps={summary['total_steps']}  invalid={summary['invalid_config_count']}")


if __name__ == "__main__":
    main()
