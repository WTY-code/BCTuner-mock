"""Offline joint sample reservoir builder for `complex` branch conditions.

For each declared `joint_groups` entry in an AST template, this builder:
1. Samples up to N records from Fabric world state via the existing
   WorldStateSampler.sample_bulk() path (no second state-access mechanism).
2. Projects each record down to exactly the declared field group.
3. Persists the reservoir as a JSON cache via atomic temp-file + os.replace(),
   so the online path never observes a partially-written file.

The offline builder is invoked by HistogramOrchestrator.ensure_joint_reservoirs()
on the same background trigger as histogram refresh.
"""

import hashlib
import json
import logging
import os
import tempfile
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def condition_id(fields: List[str], expr: str) -> str:
    """Derive a stable, filesystem-safe ID for a complex condition.

    Uses a SHA-256 prefix over (sorted fields, expr) so the same logical
    condition always maps to the same cache file across processes and runs.
    """
    key = json.dumps({"fields": sorted(fields), "expr": expr}, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class JointReservoirBuilder:
    """Builds and persists joint sample reservoirs for complex conditions."""

    def __init__(self, reservoir_dir: str,
                 sampler=None,
                 n: int = 2400,
                 b: int = 20,
                 d_threshold: int = 2):
        """
        Args:
            reservoir_dir:  directory where reservoir JSON files are written.
            sampler:        WorldStateSampler instance (may be None for tests).
            n:              reservoir size — default N ≈ 0.96/0.02² = 2400 for
                            95% CI at half-width ε = 0.02.
            b:              bin count per dimension (for optional d-dim histogram).
            d_threshold:    use raw reservoir when d >= this value.
        """
        self._dir = reservoir_dir
        self._sampler = sampler
        self._n = n
        self._b = b
        self._d_threshold = d_threshold
        os.makedirs(self._dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, chaincode: str, field_group: List[str],
              expr: str = "",
              sampler_args: Optional[Dict] = None,
              records: Optional[List[Dict]] = None) -> str:
        """Build a reservoir for one joint field group.

        Args:
            chaincode:    chaincode name (e.g. "smallbank").
            field_group:  list of field names to sample jointly (e.g. ["CheckingBalance", "SavingsBalance"]).
            expr:         the condition expr string — used to derive the condition ID.
            sampler_args: forwarded to WorldStateSampler.sample_bulk().
            records:      pre-collected records (bypasses sampler; used in tests).

        Returns:
            Path to the written cache file.
        """
        cid = condition_id(field_group, expr)
        out_path = os.path.join(self._dir, f"reservoir_{cid}.json")

        if records is None:
            records = self._collect_records(chaincode, field_group, sampler_args or {})

        tuples = self._project(records, field_group)

        payload = {
            "condition_id": cid,
            "fields": field_group,
            "reservoir": tuples,
        }
        self._atomic_write(out_path, payload)
        logger.info("reservoir written: %s (%d tuples, fields=%s)", out_path, len(tuples), field_group)
        return out_path

    def cache_path_for(self, field_group: List[str], expr: str = "") -> str:
        """Return the cache path that build() would produce (without building)."""
        cid = condition_id(field_group, expr)
        return os.path.join(self._dir, f"reservoir_{cid}.json")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_records(self, chaincode: str, field_group: List[str],
                         sampler_args: Dict) -> List[Dict]:
        """Collect raw records from world state via the existing bulk-query path."""
        if self._sampler is None:
            logger.warning("no sampler configured; reservoir will be empty for %s", field_group)
            return []

        max_samples = sampler_args.get("max_samples", self._n)
        raw = self._sampler.sample_bulk(chaincode, "query_all_accounts", field_group,
                                        max_samples=max_samples)
        if not raw:
            return []

        # Zip per-field lists into per-record dicts (all lists must be same length)
        lengths = [len(v) for v in raw.values()]
        if len(set(lengths)) != 1:
            logger.warning("unequal field list lengths from sample_bulk: %s", lengths)
        n = min(lengths) if lengths else 0
        return [{f: raw[f][i] for f in field_group if f in raw} for i in range(n)]

    @staticmethod
    def _project(records: List[Dict], fields: List[str]) -> List[List[float]]:
        """Project records down to field_group columns, dropping incomplete rows."""
        tuples = []
        for rec in records:
            try:
                row = [float(rec[f]) for f in fields]
                tuples.append(row)
            except (KeyError, TypeError, ValueError):
                continue
        return tuples

    @staticmethod
    def _atomic_write(path: str, payload: dict) -> None:
        """Write JSON atomically via a temp file + os.replace()."""
        dir_ = os.path.dirname(path)
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
