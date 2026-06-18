"""Conflict detector — checks WW / WR / RW conflicts between two R/W sets."""

import json
from typing import Dict, List

from core.data_model import ReadWriteSet


class ConflictDetector:
    def __init__(self, predictor):
        self.predictor = predictor

    def has_conflict(self, rwset1: ReadWriteSet, rwset2: ReadWriteSet) -> bool:
        if rwset1.writes & rwset2.writes:
            return True
        if rwset1.writes & rwset2.reads:
            return True
        if rwset1.reads & rwset2.writes:
            return True
        return False

    def build_conflict_graph(self, txs: list) -> Dict[str, List[str]]:
        tx_map = {tx.id: tx for tx in txs}
        graph: Dict[str, List[str]] = {tx.id: [] for tx in txs}
        ids = list(tx_map.keys())
        for i, id1 in enumerate(ids):
            tx1 = tx_map[id1]
            for id2 in ids[i + 1 :]:
                tx2 = tx_map[id2]
                if self.has_conflict(
                    ReadWriteSet(reads=tx1.reads, writes=tx1.writes),
                    ReadWriteSet(reads=tx2.reads, writes=tx2.writes),
                ):
                    graph[id1].append(id2)
                    graph[id2].append(id1)
        return graph
