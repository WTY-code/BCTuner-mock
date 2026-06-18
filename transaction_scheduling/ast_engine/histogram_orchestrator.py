#!/usr/bin/env python3
"""Histogram + Joint Reservoir Orchestrator — ensures all cached statistics are available.

Reads an AST template JSON, checks which histogram fields and joint reservoirs
are missing, and generates them via world-state sampling or synthetic defaults.

Usage::

    orchestrator = HistogramOrchestrator(histograms_dir="artifacts/histograms/")
    orchestrator.ensure_all(template_path="templates/smallbank_llm.json",
                            chaincode="smallbank",
                            sampler_args={...})
    orchestrator.ensure_joint_reservoirs(template_path="templates/smallbank_llm.json",
                                          chaincode="smallbank")
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from ast_engine.histogram_generator import HistogramGenerator
from ast_engine.worldstate_sampler import WorldStateSampler


# Synthetic defaults for common TPC-C / token field types.
# Used as fallback when live sampling is not configured.
_SYNTHETIC_SPECS = {
    "Balance":       {"min": 0,      "max": 2_000_000, "mean": 1_000_000},
    "TokenBalance":  {"min": 0,      "max": 2_000_000, "mean": 1_000_000},
    "CheckingBalance": {"min": 200,  "max": 1_800,     "mean": 1_000},
    "SavingsBalance":  {"min": 200,  "max": 1_800,     "mean": 1_000},
    "C_CREDIT":      {"min": 0,      "max": 1,         "mean": 0.9},
    "StockQty":      {"min": 10,     "max": 100,       "mean": 55},
}

# Key-space descriptors for live sampling (per-key queries).
_KEY_SPACES = {
    "token_erc20": {
        "Balance": {
            "pattern": "balance:{account}",
            "args": {"account": []},
        },
        "TokenBalance": {
            "pattern": "balance:{account}",
            "args": {"account": []},
        },
    },
}

# Bulk-query descriptors for chaincodes whose keys are hashed (not enumerable).
_BULK_QUERIES = {
    "smallbank": {
        "query_fn": "query_all_accounts",
        "fields": ["CheckingBalance", "SavingsBalance"],
    },
}


class HistogramOrchestrator:
    def __init__(self, histograms_dir: str,
                 sampler: Optional[WorldStateSampler] = None,
                 # N ≈ 0.96/ε² for 95% CI at half-width ε=0.02 → 2400
                 reservoir_n: int = 2400,
                 reservoir_b: int = 20,
                 reservoir_d_threshold: int = 2,
                 refresh_interval_k: Optional[int] = None):
        self.dir = histograms_dir
        os.makedirs(self.dir, exist_ok=True)
        self.sampler = sampler
        self.generator = HistogramGenerator(num_bins=50)
        self._bulk_cache: Dict[str, Dict[str, List[float]]] = {}
        self.reservoir_n = reservoir_n
        self.reservoir_b = reservoir_b
        self.reservoir_d_threshold = reservoir_d_threshold
        self.refresh_interval_k = refresh_interval_k

    def ensure_all(self, template_path: str,
                   chaincode: str = "",
                   sampler_args: Optional[Dict] = None) -> List[str]:
        """Ensure all histogram fields declared in *template_path* exist.

        Returns list of field names that were generated (or already existed).
        """
        with open(template_path) as f:
            registry = json.load(f)
        fields = registry.get("histograms", [])
        if not fields:
            print("  No histogram fields declared in template.")
            return []

        generated = []
        for field in fields:
            out = os.path.join(self.dir, f"{field}.json")
            if os.path.isfile(out):
                print(f"  [{field}] already exists → skip")
                generated.append(field)
                continue

            print(f"  [{field}] missing → generating...")
            values = self._collect_values(field, chaincode, sampler_args)
            self.generator.generate(field, values, self.dir)
            generated.append(field)

        return generated

    def ensure_joint_reservoirs(self, template_path: str,
                                 chaincode: str = "",
                                 sampler_args: Optional[Dict] = None) -> List[str]:
        """Build joint sample reservoirs for all groups in template's ``joint_groups``.

        Reuses the same bulk-query cache as ensure_all() — no second state-access path.
        Returns list of cache file paths that were written (or already existed).
        """
        from ast_engine.joint_reservoir_builder import JointReservoirBuilder

        with open(template_path) as f:
            registry = json.load(f)
        joint_groups = registry.get("joint_groups", [])
        if not joint_groups:
            return []

        reservoir_dir = os.path.join(self.dir, "reservoirs")
        builder = JointReservoirBuilder(
            reservoir_dir=reservoir_dir,
            sampler=self.sampler,
            n=self.reservoir_n,
            b=self.reservoir_b,
            d_threshold=self.reservoir_d_threshold,
        )

        # Collect all complex condition exprs from the template to pair with groups
        exprs_by_group = self._collect_complex_exprs(registry)

        written = []
        for group in joint_groups:
            group_key = frozenset(group)
            expr = exprs_by_group.get(group_key, "")
            cache_path = builder.cache_path_for(group, expr)
            if os.path.isfile(cache_path):
                print(f"  [joint {group}] reservoir already exists → skip")
                written.append(cache_path)
                continue

            print(f"  [joint {group}] building reservoir (n={self.reservoir_n})...")
            records = self._collect_joint_records(chaincode, group, sampler_args or {})
            path = builder.build(chaincode, group, expr=expr, records=records)
            written.append(path)

        return written

    # ------------------------------------------------------------------

    def _collect_values(self, field: str, chaincode: str,
                        sampler_args: Optional[Dict]) -> List[float]:
        """Try live sampling first, fall back to synthetic."""
        args = sampler_args or {}

        if self.sampler:
            bulk_spec = _BULK_QUERIES.get(chaincode)
            if bulk_spec and field in bulk_spec["fields"]:
                if chaincode not in self._bulk_cache:
                    raw = self.sampler.sample_bulk(
                        chaincode, bulk_spec["query_fn"], bulk_spec["fields"])
                    self._bulk_cache[chaincode] = raw
                    total = sum(len(v) for v in raw.values())
                    if total:
                        print(f"    Bulk-sampled {total} values from Fabric world state")
                cached = self._bulk_cache.get(chaincode, {}).get(field, [])
                if cached:
                    return cached

            cc_spaces = _KEY_SPACES.get(chaincode, {})
            spec = args.get(field) or cc_spaces.get(field)
            if spec:
                pattern = spec.get("pattern", "")
                key_args = spec.get("args", {})
                if key_args:
                    vals = self.sampler.sample(chaincode, pattern, key_args,
                                               max_samples=args.get("max_samples", 1000))
                    if vals:
                        print(f"    Sampled {len(vals)} values from Fabric")
                        return vals

        syn = _SYNTHETIC_SPECS.get(field)
        if syn:
            import numpy as np
            rng = np.random.default_rng(42)
            sigma = (syn["max"] - syn["min"]) / 6
            vals = rng.normal(syn["mean"], max(sigma, 1), 1000)
            vals = [max(syn["min"], min(syn["max"], v)) for v in vals]
            print(f"    Generated 1000 synthetic values ({syn})")
            return vals

        import numpy as np
        vals = np.random.default_rng(42).uniform(0, 100000, 500).tolist()
        print(f"    Generated 500 uniform random values (fallback)")
        return vals

    def _collect_joint_records(self, chaincode: str, field_group: List[str],
                                sampler_args: Dict) -> List[Dict]:
        """Collect raw records for joint reservoir construction.

        Reuses _bulk_cache populated by ensure_all() where possible.
        """
        if self.sampler is None:
            return []

        bulk_spec = _BULK_QUERIES.get(chaincode)
        if bulk_spec and all(f in bulk_spec["fields"] for f in field_group):
            if chaincode not in self._bulk_cache:
                raw = self.sampler.sample_bulk(
                    chaincode, bulk_spec["query_fn"], bulk_spec["fields"],
                    max_samples=self.reservoir_n)
                self._bulk_cache[chaincode] = raw

            cached = self._bulk_cache.get(chaincode, {})
            lengths = [len(cached.get(f, [])) for f in field_group]
            if lengths and min(lengths) > 0:
                n = min(lengths)
                return [{f: cached[f][i] for f in field_group} for i in range(n)]

        return []

    @staticmethod
    def _collect_complex_exprs(registry: dict) -> Dict[frozenset, str]:
        """Walk template functions and collect expr strings keyed by field group."""
        exprs: Dict[frozenset, str] = {}
        for fn_def in registry.get("functions", {}).values():
            _scan_nodes(fn_def.get("body", []), exprs)
        return exprs


def _scan_nodes(nodes: list, exprs: Dict[frozenset, str]) -> None:
    for node in nodes:
        if node.get("type") == "branch":
            cond = node.get("condition", {})
            if cond.get("kind") == "complex":
                fields = cond.get("fields", [])
                expr = cond.get("expr", "")
                exprs[frozenset(fields)] = expr
            for sub in ("then", "else"):
                _scan_nodes(node.get(sub, []), exprs)
        elif node.get("type") == "iterate":
            _scan_nodes(node.get("body", []), exprs)
