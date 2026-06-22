"""Run the Auriga transaction scheduling pipeline on a workload buffer.

Loads transactions from a JSONL buffer, predicts each transaction's read/write
set via the LLM-extracted AST template, constructs a conflict graph over the
predicted sets, and groups non-conflicting transactions into capacity-bounded
batches using a capacitated DSatur Bin-Packing-with-Conflicts heuristic.

This is the entry point for the §IV transaction scheduling pipeline.

Usage::

    python run_pipeline.py \\
        --buffer <txs.jsonl> \\
        --template templates/smallbank_llm.json \\
        --block-size 1000 --window-size 5000 \\
        --scheduler-workers 4
"""

import argparse
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from adapters.generic import GenericAdapter
from core.pipeline_orchestrator import PipelineOrchestrator


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--buffer", required=True, type=Path,
                   help="JSONL file with one transaction per line")
    p.add_argument("--template", required=True, type=Path,
                   help="LLM-extracted AST template JSON "
                        "(e.g. templates/smallbank_llm.json)")
    p.add_argument("--output-dir", type=Path, default=Path("./schedule_output"),
                   help="Directory to write the schedule (default: ./schedule_output)")
    p.add_argument("--block-size", type=int, default=1000,
                   help="Target transactions per block / MaxMessageCount "
                        "(default: 1000)")
    p.add_argument("--window-size", type=int, default=5000,
                   help="Transactions per scheduling window (default: 5000)")
    p.add_argument("--target-blocks", type=int, default=100,
                   help="Stop after producing this many blocks (default: 100)")
    p.add_argument("--scheduler-workers", type=int, default=1,
                   help="Parallel BPC threads — paper §IV.B.3, Fig. 7a "
                        "(default: 1)")
    p.add_argument("--max-attempts", type=int, default=3,
                   help="Max scatter retries before a tx is sent to RecycleBucket")
    p.add_argument("--max-wait-ms", type=int, default=60000,
                   help="Max time to wait for the buffer to fill (ms)")
    args = p.parse_args()

    if not args.buffer.is_file():
        print(f"error: buffer file not found: {args.buffer}", file=sys.stderr)
        return 1
    if not args.template.is_file():
        print(f"error: template file not found: {args.template}", file=sys.stderr)
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    adapter = GenericAdapter(str(args.template))

    config = {
        "buffer_path": str(args.buffer),
        "output_dir": str(args.output_dir),
        "caliper_workspace": str(args.output_dir),
        "block_size": args.block_size,
        "window_size": args.window_size,
        "target_blocks": args.target_blocks,
        "max_attempts": args.max_attempts,
        "max_wait_ms": args.max_wait_ms,
        "tps": 0,
        "workload_module": "",
        "scheduler_workers": args.scheduler_workers,
    }

    orch = PipelineOrchestrator(config, predictor=adapter)
    t = threading.Thread(target=orch.scheduler_worker)
    t.start()
    t.join()

    blocks = getattr(orch, "blocks_produced", 0)
    print()
    print("=== Scheduling complete ===")
    print(f"  Blocks produced:        {blocks}")
    print(f"  Scheduler workers:      {args.scheduler_workers}")
    print(f"  Output directory:       {args.output_dir}/batch_1/")
    print(f"    schedule.json         block → tx_id mapping")
    print(f"    txs.json              all transactions in block order")
    print(f"    groups/               per-block transaction files")
    print(f"    scheduler_timing.json wall-time + thread counters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
