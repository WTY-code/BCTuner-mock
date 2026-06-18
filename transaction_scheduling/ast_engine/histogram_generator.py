#!/usr/bin/env python3
"""Histogram Generator — produces 1D histogram JSON from sampled world-state values.

Output format is compatible with HistogramStore::

    {"attribute": "Balance", "bins": [0, 100, 500, ...], "counts": [20, 30, ...]}

Usage::

    gen = HistogramGenerator(num_bins=50)
    gen.generate("Balance", values, output_dir="artifacts/histograms/")
"""

import json
import math
import os
from pathlib import Path
from typing import List, Optional


class HistogramGenerator:
    """Generates equal-width histogram JSON files from numeric samples."""

    def __init__(self, num_bins: int = 50):
        self.num_bins = num_bins

    def generate(self, field_name: str, values: List[float],
                 output_dir: str) -> str:
        """Generate a histogram JSON file.

        Returns the path to the generated file.
        """
        if not values:
            raise ValueError(f"No values provided for field '{field_name}'")

        n = min(self.num_bins, len(values) // 10) or 5
        bins, counts = self._build_bins(values, n)

        # Compute statistics
        sorted_vals = sorted(values)
        payload = {
            "attribute": field_name,
            "stats": {
                "total": len(values),
                "min": sorted_vals[0],
                "max": sorted_vals[-1],
                "median": sorted_vals[len(values) // 2],
                "mean": round(sum(values) / len(values), 2),
            },
            "bins": bins,
            "counts": counts,
        }

        out = Path(output_dir) / f"{field_name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  Histogram saved: {out} ({len(values)} samples, {n} bins)")
        return str(out)

    # ------------------------------------------------------------------

    def _build_bins(self, values: List[float], n: int):
        vmin, vmax = min(values), max(values)
        if vmin == vmax:
            vmin -= 1
            vmax += 1
        width = (vmax - vmin) / n
        bins = [round(vmin + i * width, 4) for i in range(n + 1)]
        counts = [0] * n
        for v in values:
            idx = min(int((v - vmin) / width), n - 1)
            idx = max(idx, 0)
            counts[idx] += 1
        return bins, counts
