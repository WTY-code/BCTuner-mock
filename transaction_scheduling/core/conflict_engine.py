from typing import List, Set

from core.data_model import Transaction, ReadWriteSet
from core.conflict_detector import ConflictDetector
from core.capacitated_graph_coloring_scheduler import CapacitatedGraphColoringScheduler


class TransactionGroup:
    """Maintains a transaction group and its union R/W sets for O(1) conflict checks."""

    def __init__(self):
        self.txs: List[Transaction] = []
        self.union_reads: Set[str] = set()
        self.union_writes: Set[str] = set()

    def add(self, tx: Transaction):
        self.txs.append(tx)
        if tx.reads:
            self.union_reads.update(tx.reads)
        if tx.writes:
            self.union_writes.update(tx.writes)

    def conflicts_with(self, tx: Transaction) -> bool:
        if tx.writes & self.union_writes:
            return True
        if self.union_writes & tx.reads:
            return True
        if self.union_reads & tx.writes:
            return True
        return False

    def to_list(self) -> List[Transaction]:
        return self.txs


class ConflictEngine:
    """Orchestrates RW-set prediction, conflict graph building, and DSatur coloring.

    Accepts a *predictor* that implements ``predict(raw_tx_data: dict) -> ReadWriteSet``.
    For chaincode-specific adapters, wrap ``parse_tx`` + ``predict_rwset`` into that method.
    """

    def __init__(self, predictor):
        self.predictor = predictor
        self.detector = ConflictDetector(predictor)

    def compute_rw_sets(self, txs: List[Transaction]):
        for tx in txs:
            if not tx.reads and not tx.writes:
                rw: ReadWriteSet = self.predictor.predict(tx.data)
                tx.reads = set(rw.reads)
                tx.writes = set(rw.writes)
                tx.valid = rw.valid

    def build_schedule(self, txs: List[Transaction], block_size: int) -> List[List[Transaction]]:
        self.compute_rw_sets(txs)

        graph = {tx.id: [] for tx in txs}
        tx_map = {tx.id: tx for tx in txs}
        ids = [tx.id for tx in txs]

        for i, id1 in enumerate(ids):
            tx1 = tx_map[id1]
            for id2 in ids[i + 1 :]:
                tx2 = tx_map[id2]
                if self._check_conflict(tx1, tx2):
                    graph[id1].append(id2)
                    graph[id2].append(id1)

        scheduler = CapacitatedGraphColoringScheduler(graph, block_size)
        scheduler.capacitated_dsatur_coloring()
        blocks_idx = scheduler.get_blocks()

        result_groups = []
        for _, tx_ids in blocks_idx.items():
            group = [tx_map[tid] for tid in tx_ids if tid in tx_map]
            if group:
                result_groups.append(group)
        return result_groups

    def _check_conflict(self, tx1: Transaction, tx2: Transaction) -> bool:
        if tx1.writes & tx2.writes:
            return True
        if tx1.writes & tx2.reads:
            return True
        if tx1.reads & tx2.writes:
            return True
        return False
