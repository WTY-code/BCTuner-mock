"""1-D histogram store for probability estimation.

Each histogram JSON file::

    {"bins": [0, 1000, 5000, ...], "counts": [50, 30, ...]}

``bins`` are edges (len N+1), ``counts`` are per-bin frequencies (len N).
``prob_ge(x)`` returns P(value >= x) via linear interpolation across the bins
that start at or above ``x``.
"""

import json
from pathlib import Path
from typing import List, Optional


class HistogramStore:
    def __init__(self, path: Optional[str] = None):
        self.bins: List[float] = []
        self.counts: List[float] = []
        self.total: float = 0.0
        if path:
            self.load(path)

    def load(self, path: str):
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.bins = payload.get("bins", [])
            self.counts = payload.get("counts", [])
            self.total = float(sum(self.counts)) if self.counts else 0.0
        except Exception:
            self.bins = []
            self.counts = []
            self.total = 0.0

    def prob_ge(self, x: float) -> float:
        if not self.bins or not self.counts or self.total <= 0:
            return 0.0
        if x <= self.bins[0]:
            return 1.0
        if x > self.bins[-1]:
            return 0.0
        idx = 0
        for i in range(len(self.counts)):
            if x <= self.bins[i + 1]:
                idx = i
                break
        s = sum(self.counts[idx:])
        return s / self.total
