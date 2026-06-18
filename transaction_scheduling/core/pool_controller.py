from collections import deque
from typing import Deque, List

from core.data_model import Transaction
from core.conflict_engine import ConflictEngine, TransactionGroup
from core.buffer_pool import BufferPool


class PoolController:
    def __init__(self, buffer_path: str, max_age: int, recycle_capacity: int):
        self.io_pool = BufferPool(buffer_path)
        self.main_queue: Deque[Transaction] = deque()
        self.recycle_bucket: List[Transaction] = []
        self.max_age = max_age
        self.recycle_capacity = recycle_capacity

    def load_window(self, window_size: int, max_wait_ms: int) -> int:
        raw_data = self.io_pool.get_window(window_size, max_wait_ms)
        for d in raw_data:
            self.main_queue.append(Transaction.from_dict(d))
        return len(raw_data)

    def fetch_batch(self, size: int) -> List[Transaction]:
        batch = []
        while len(batch) < size and self.main_queue:
            batch.append(self.main_queue.popleft())
        return batch

    def push_back_main(self, tx: Transaction):
        self.main_queue.appendleft(tx)

    def send_to_recycle(self, tx: Transaction):
        self.recycle_bucket.append(tx)

    def needs_detox(self, block_size: int) -> bool:
        return len(self.recycle_bucket) > self.recycle_capacity

    def force_detox_batch(self, block_size: int) -> List[Transaction]:
        if len(self.recycle_bucket) > 0:
            count = min(len(self.recycle_bucket), block_size)
            batch = self.recycle_bucket[:count]
            self.recycle_bucket = self.recycle_bucket[count:]
            return batch
        return []

    def has_pending_data(self):
        return bool(self.main_queue) or bool(self.recycle_bucket)

    def smart_fill(self, initial_group: List[Transaction], target_size: int, engine: ConflictEngine) -> List[Transaction]:
        wrapper = TransactionGroup()
        for tx in initial_group:
            wrapper.add(tx)

        need = target_size - len(initial_group)
        if need <= 0:
            return wrapper.to_list()

        # Try recycle bucket first
        new_recycle = []
        for tx in self.recycle_bucket:
            if need > 0 and not wrapper.conflicts_with(tx):
                wrapper.add(tx)
                need -= 1
            else:
                new_recycle.append(tx)
        self.recycle_bucket = new_recycle

        if need == 0:
            return wrapper.to_list()

        # Try main queue (drafting)
        skipped_txs = []
        scan_limit = need * 10
        scanned = 0

        while need > 0 and self.main_queue and scanned < scan_limit:
            candidate = self.main_queue.popleft()
            scanned += 1
            engine.compute_rw_sets([candidate])

            if not wrapper.conflicts_with(candidate):
                wrapper.add(candidate)
                need -= 1
            else:
                skipped_txs.append(candidate)

        for tx in reversed(skipped_txs):
            self.main_queue.appendleft(tx)

        return wrapper.to_list()
