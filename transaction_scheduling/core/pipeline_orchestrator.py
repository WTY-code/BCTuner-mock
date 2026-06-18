import concurrent.futures
import json
import os
import queue
import threading
import time
from pathlib import Path

from core.data_model import Transaction
from core.conflict_engine import ConflictEngine
from core.pool_controller import PoolController
from core.metric_extractor import MetricExtractor
from core.execution_engine import CaliperExecutor
from core.caliper_config_generator import generate_config, write_yaml
from core.schedule_to_groups import emit_groups


class PipelineOrchestrator:
    """Producer-consumer pipeline: scheduler thread feeds blocks, executor thread runs Caliper.

    Accepts a *predictor* that implements ``predict(raw_tx_data: dict) -> ReadWriteSet``.
    This keeps the orchestrator chaincode-agnostic — the predictor is typically a
    chaincode adapter wrapping ``parse_tx`` + ``predict_rwset``.
    """

    def __init__(self, config: dict, predictor):
        self.cfg = config
        self.block_size = config["block_size"]
        self.target_blocks = config.get("target_blocks", 100)

        self.output_base = Path(config["output_dir"])
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.pool = PoolController(
            buffer_path=config["buffer_path"],
            max_age=config.get("max_attempts", 3),
            recycle_capacity=self.block_size * 2,
        )
        self.engine = ConflictEngine(predictor)
        self.extractor = MetricExtractor(self.output_base)
        self.executor = CaliperExecutor(
            workspace=config["caliper_workspace"],
            verbose=config.get("verbose", False),
        )

        self.execution_queue = queue.Queue()
        self.scheduling_done = False
        self.generated_block_count = 0

    def scheduler_worker(self):
        """Entry point: branch on scheduler_workers config (default 1 = single-threaded)."""
        n = self.cfg.get("scheduler_workers", 1)
        if n <= 1:
            self._run_single_threaded()
        else:
            self._run_multi_threaded(n)

    def _run_single_threaded(self):
        """Original single-threaded scheduler loop — unchanged for backward compatibility."""
        print("[Scheduler] Started (single-threaded).")
        all_blocks = []

        # Timing accumulators (algorithm vs I/O)
        self.t_algo_s = 0.0
        self.t_io_s = 0.0
        t_thread_start = time.perf_counter()

        while self.generated_block_count < self.target_blocks:
            if len(self.pool.main_queue) < self.cfg["window_size"]:
                t0 = time.perf_counter()
                self.pool.load_window(2 * self.cfg["window_size"], self.cfg["max_wait_ms"])
                self.t_io_s += time.perf_counter() - t0

            if not self.pool.has_pending_data():
                print("[Scheduler] No more data available.")
                break

            t0 = time.perf_counter()
            window_txs = self.pool.fetch_batch(self.cfg["window_size"])
            if not window_txs:
                self.t_algo_s += time.perf_counter() - t0
                break

            raw_groups = self.engine.build_schedule(window_txs, self.block_size)

            for group_txs in raw_groups:
                size = len(group_txs)
                final_group = None

                if size == self.block_size:
                    final_group = group_txs
                elif size >= int(self.block_size * self.cfg.get("fill_threshold", 0.8)):
                    final_group = self.pool.smart_fill(group_txs, self.block_size, self.engine)
                    if len(final_group) < self.block_size:
                        self._scatter_group(final_group)
                        final_group = None
                else:
                    self._scatter_group(group_txs)

                if final_group:
                    all_blocks.append(final_group)
                    self.generated_block_count += 1

            if self.pool.needs_detox(self.block_size):
                print(f"[Scheduler] WARNING: Triggering Detox for {len(self.pool.recycle_bucket)} items")
                detox = self.pool.force_detox_batch(self.block_size)
                if detox:
                    all_blocks.append(detox)
                    self.generated_block_count += 1

            self.t_algo_s += time.perf_counter() - t0

            if self.generated_block_count >= self.target_blocks:
                break

        if all_blocks:
            t0 = time.perf_counter()
            cfg_path = self._prepare_batch_files(1, all_blocks)
            self.t_io_s += time.perf_counter() - t0
            self.execution_queue.put((1, cfg_path))
            print(f"[Scheduler] Combined batch queued ({len(all_blocks)} blocks, single Caliper invocation).")
        else:
            print("[Scheduler] No blocks produced.")

        self.t_scheduler_thread_wall_s = time.perf_counter() - t_thread_start
        self.blocks_produced = len(all_blocks)

        timing = {
            "scheduler_workers": 1,
            "t_algo_s": round(self.t_algo_s, 3),
            "t_io_s": round(self.t_io_s, 3),
            "t_scheduler_thread_wall_s": round(self.t_scheduler_thread_wall_s, 3),
            "blocks_produced": self.blocks_produced,
        }
        (self.output_base / "scheduler_timing.json").write_text(json.dumps(timing, indent=2))
        print(f"[Scheduler] Timing: algo={self.t_algo_s:.2f}s io={self.t_io_s:.2f}s "
              f"thread_wall={self.t_scheduler_thread_wall_s:.2f}s blocks={self.blocks_produced}")

        self.scheduling_done = True
        print("[Scheduler] Done.")

    def _bpc_window_task(self, pool_lock: threading.Lock,
                         all_blocks: list, results_lock: threading.Lock) -> None:
        """Per-thread BPC task for multi-threaded mode.

        Lock design:
          [pool_lock]   fetch_batch          ← fast, serialized
          [no lock]     build_schedule        ← slow, runs concurrently across threads
          [pool_lock]   smart_fill/scatter    ← medium, serialized
          [results_lock] extend all_blocks   ← tiny, serialized
        """
        fill_threshold = self.cfg.get("fill_threshold", 0.8)
        while True:
            # Stop if target already reached
            with results_lock:
                if self.generated_block_count >= self.target_blocks:
                    return

            # Fetch one window under pool lock (fast)
            with pool_lock:
                if not self.pool.has_pending_data():
                    return
                window_txs = self.pool.fetch_batch(self.cfg["window_size"])

            if not window_txs:
                return

            # BPC — lock-free, runs concurrently across all worker threads
            raw_groups = self.engine.build_schedule(window_txs, self.block_size)

            # Fill/scatter/detox under pool lock
            completed = []
            with pool_lock:
                for group in raw_groups:
                    size = len(group)
                    if size == self.block_size:
                        completed.append(group)
                    elif size >= int(self.block_size * fill_threshold):
                        filled = self.pool.smart_fill(group, self.block_size, self.engine)
                        if len(filled) >= self.block_size:
                            completed.append(filled)
                        else:
                            self._scatter_group(filled)
                    else:
                        self._scatter_group(group)

                if self.pool.needs_detox(self.block_size):
                    detox = self.pool.force_detox_batch(self.block_size)
                    if detox:
                        completed.append(detox)

            # Accumulate results
            if completed:
                with results_lock:
                    all_blocks.extend(completed)
                    self.generated_block_count += len(completed)

    def _run_multi_threaded(self, n_workers: int) -> None:
        """Multi-threaded BPC scheduler: window-level parallelism across n_workers threads.

        Each thread independently fetches a window and runs BPC concurrently.
        Pool access is serialized by pool_lock only during the cheap fetch and
        fill phases; the expensive BPC computation is fully lock-free.
        """
        print(f"[Scheduler] Started (multi-threaded, {n_workers} BPC workers).")
        t_start = time.perf_counter()

        # Pre-load all transactions into main_queue before spawning workers
        self.pool.load_window(10 * self.cfg["window_size"], self.cfg["max_wait_ms"])
        print(f"[Scheduler] Pre-loaded {len(self.pool.main_queue)} txs into queue.")

        pool_lock = threading.Lock()
        all_blocks: list = []
        results_lock = threading.Lock()

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = [
                executor.submit(self._bpc_window_task, pool_lock, all_blocks, results_lock)
                for _ in range(n_workers)
            ]
            for f in concurrent.futures.as_completed(futures):
                f.result()  # propagate any worker exception immediately

        self.t_scheduler_thread_wall_s = time.perf_counter() - t_start
        self.blocks_produced = len(all_blocks)

        if all_blocks:
            cfg_path = self._prepare_batch_files(1, all_blocks)
            self.execution_queue.put((1, cfg_path))
            print(f"[Scheduler] Combined batch queued ({len(all_blocks)} blocks, "
                  f"single Caliper invocation).")
        else:
            print("[Scheduler] No blocks produced.")

        timing = {
            "scheduler_workers": n_workers,
            "t_scheduler_thread_wall_s": round(self.t_scheduler_thread_wall_s, 3),
            "blocks_produced": self.blocks_produced,
        }
        (self.output_base / "scheduler_timing.json").write_text(json.dumps(timing, indent=2))
        print(f"[Scheduler] Timing: wall={self.t_scheduler_thread_wall_s:.2f}s "
              f"blocks={self.blocks_produced} workers={n_workers}")

        self.scheduling_done = True
        print("[Scheduler] Done.")

    def executor_worker(self):
        print("[Executor] Started waiting for tasks...")
        while True:
            try:
                batch_id, config_path = self.execution_queue.get(timeout=2)
            except queue.Empty:
                if self.scheduling_done:
                    break
                continue

            print(f"[Executor] >>> Running Batch {batch_id}")
            records = self.executor.run(config_path)

            if records:
                self.extractor.add_batch_results(batch_id, records)
                print(f"[Executor] <<< Batch {batch_id} Done. Groups: {len(records)}")
                self.extractor.save_to_csv("detailed_metrics.csv")
            else:
                print(f"[Executor] <<< Batch {batch_id} No data returned.")

            self.execution_queue.task_done()

    def run(self):
        t_s = threading.Thread(target=self.scheduler_worker)
        t_e = threading.Thread(target=self.executor_worker)
        t_s.start()
        t_e.start()
        t_s.join()
        t_e.join()

        print("\n" + "=" * 50)
        print("PIPELINE FINISHED - SAVING DATA")
        print("=" * 50)
        self.extractor.save_to_csv("detailed_metrics.csv")

        all_data = self.extractor.get_all_records()
        print(f"Total Groups Recorded: {len(all_data)}")
        return all_data

    def _prepare_batch_files(self, batch_id, blocks):
        batch_dir = self.output_base / f"batch_{batch_id}"
        batch_dir.mkdir(parents=True, exist_ok=True)

        schedule_data = {"blocks": {f"block_{i}": [tx.id for tx in blk] for i, blk in enumerate(blocks)}}
        all_txs = [tx.to_dict() for blk in blocks for tx in blk]

        (batch_dir / "schedule.json").write_text(json.dumps(schedule_data, indent=2))
        (batch_dir / "txs.json").write_text(json.dumps(all_txs, indent=2))

        groups_dir = batch_dir / "groups"
        group_files = emit_groups(str(batch_dir / "schedule.json"), str(batch_dir / "txs.json"), str(groups_dir))

        tx_numbers = {idx: len(json.loads(Path(p).read_text())) for idx, p in group_files.items()}

        workers = self.cfg.get("workers", 1)
        rate_control = self.cfg.get("rate_control", None)

        caliper_cfg = generate_config(
            group_files,
            self.cfg["tps"],
            tx_numbers,
            self.cfg["workload_module"],
            workers=workers,
            rate_control=rate_control,
            contract_id=self.cfg.get("contract_id", self.cfg.get("chaincode", "smallbank")),
            contract_version=self.cfg.get("contract_version", "1.0"),
        )

        # Resolve relative paths to absolute for caliper
        for round_cfg in caliper_cfg["test"]["rounds"]:
            tx_path = round_cfg["workload"]["arguments"].get("txFilePath", "")
            if tx_path and not os.path.isabs(tx_path):
                round_cfg["workload"]["arguments"]["txFilePath"] = os.path.abspath(tx_path)

        cfg_path = batch_dir / "caliper_config.yaml"
        write_yaml(caliper_cfg, str(cfg_path))
        return str(cfg_path.absolute())

    def _scatter_group(self, group):
        for tx in group:
            tx.age += 1
            if tx.age < self.pool.max_age:
                self.pool.push_back_main(tx)
            else:
                self.pool.send_to_recycle(tx)


if __name__ == "__main__":
    BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    config = {
        "buffer_path": os.path.join(BASE_DIR, "artifacts/world_state/transactions_buffer.jsonl"),
        "output_dir": os.path.join(BASE_DIR, "artifacts/world_state/pipeline_test"),
        "caliper_workspace": os.path.join(BASE_DIR, "infra/caliper-benchmarks"),
        "block_size": 100,
        "window_size": 500,
        "target_blocks": 20,
        "max_wait_ms": 50000,
        "tps": 100,
        "workload_module": "benchmarks/scenario/smallbank/customWorkLoad.js",
        "max_attempts": 3,
        "verbose": True,
        "workers": 1,
        "rate_control": {"type": "fixed-rate", "opts": {"tps": 100}},
    }
    print("PipelineOrchestrator requires a predictor (adapter). See adapters/smallbank.py for an example.")
    print(f"Config template:\n{json.dumps(config, indent=2)}")
