"""Append one structured JSON line to result/log/run_log.jsonl from a Caliper stdout dump.

Invoked automatically by run_experiment.py after a successful Caliper run, not meant as a
user-facing entry point.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _profile import load_profile

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "result" / "log"
JSONL_PATH = LOG_DIR / "run_log.jsonl"
NETWORK_CONFIG_PATH = BASE_DIR / "config" / "network_config.json"
TUNING_PARAMS_PATH = BASE_DIR / "config" / "tuning_params.json"

# Replicated from tx-schedule/execution_engine.py:_parse_table_rows — ANSI-stripped regex
# matching Caliper's ASCII metrics table.
_TABLE_PATTERN = re.compile(
    r'^\|\s*(?P<name>[\w_]+)\s*\|\s*'
    r'(?P<succ>\d+)\s*\|\s*'
    r'(?P<fail>\d+)\s*\|\s*'
    r'(?P<send_rate>[\d\.]+)\s*\|\s*'
    r'(?P<max_lat>[\d\.]+)\s*\|\s*'
    r'(?P<min_lat>[\d\.]+)\s*\|\s*'
    r'(?P<avg_lat>[\d\.]+)\s*\|\s*'
    r'(?P<throughput>[\d\.]+)\s*\|'
)
_ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _parse_table_rows(stdout: str) -> list[dict]:
    records = []
    for line in stdout.splitlines():
        clean = _ANSI_RE.sub('', line.strip())
        m = _TABLE_PATTERN.match(clean)
        if not m or m.group('name') == 'Name':
            continue
        d = m.groupdict()
        records.append({
            'succ': int(d['succ']),
            'fail': int(d['fail']),
            'send_rate': float(d['send_rate']),
            'max_latency': float(d['max_lat']),
            'min_latency': float(d['min_lat']),
            'avg_latency': float(d['avg_lat']),
            'throughput': float(d['throughput']),
        })
    return records


def main():
    parser = argparse.ArgumentParser(description='Log a Caliper run to run_log.jsonl')
    parser.add_argument('--chaincode', required=True, help='Chaincode profile key')
    parser.add_argument('--caliper-log', required=True, help='Path to tee-ed Caliper stdout')
    args = parser.parse_args()

    profile = load_profile(args.chaincode)

    try:
        raw = Path(args.caliper_log).read_text()
    except FileNotFoundError:
        print(f"[log_run] Caliper log not found: {args.caliper_log}", file=sys.stderr)
        sys.exit(2)

    records = _parse_table_rows(raw)
    if not records:
        print("[log_run] No Caliper metrics table found in stdout.", file=sys.stderr)
        sys.exit(2)

    rec = records[-1]  # last row = "All test results" summary

    with open(NETWORK_CONFIG_PATH) as f:
        net_cfg = json.load(f)
    with open(TUNING_PARAMS_PATH) as f:
        tuning_params = json.load(f)

    peer_count = sum(len(org['placements']) for org in net_cfg['topology']['orgs'])

    step = 1
    if JSONL_PATH.exists():
        with open(JSONL_PATH) as f:
            step = sum(1 for ln in f if ln.strip()) + 1

    total_tx = rec['succ'] + rec['fail']
    succ_rate = rec['succ'] / total_tx if total_tx else 0.0

    entry = {
        "step": step,
        "benchmark": profile["name"],
        "peer_count": peer_count,
        "raw_tps": rec["throughput"],
        "succ": rec["succ"],
        "fail": rec["fail"],
        "total_tx": total_tx,
        "succ_rate": succ_rate,
        "eff_tps": rec["throughput"] * succ_rate,
        "avg_latency": rec["avg_latency"],
        "config": tuning_params,
        "invalid": succ_rate == 0.0,
    }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(JSONL_PATH, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"[log_run] step={step}  eff_tps={entry['eff_tps']:.2f}  succ_rate={entry['succ_rate']:.4f}")


if __name__ == "__main__":
    main()
