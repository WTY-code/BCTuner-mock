"""Online selectivity estimator for `complex` branch conditions.

A `complex` condition spans two or more possibly correlated world-state fields.
The estimator loads a pre-built joint sample reservoir from a JSON cache file
(written by JointReservoirBuilder) and computes the empirical satisfaction ratio:

    ŝ_c(a) = (1/N) · |{x ∈ R_c : c(x, a) is true}|

All sampling happens offline (JointReservoirBuilder).  This class performs
**no state I/O** on the critical path — only arithmetic over cached tuples.

Cold-start safety: if the cache file is absent or unreadable, returns 1.0
("branch taken") per the asymmetric-safety invariant.  A cache miss is logged
but never blocks execution.
"""

import ast
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Permitted AST node types for safe expression evaluation.
def _build_safe_nodes():
    nodes = {
        ast.Expression,
        ast.BoolOp, ast.And, ast.Or,
        ast.BinOp,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
        ast.UnaryOp, ast.USub, ast.UAdd,
        ast.Compare,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
        ast.Name,
        ast.Load,
    }
    # Constant exists in Python 3.8+; Num exists in older versions
    for name in ("Constant", "Num"):
        n = getattr(ast, name, None)
        if n is not None:
            nodes.add(n)
    return frozenset(nodes)

_SAFE_NODES = _build_safe_nodes()


@dataclass
class ComplexEstimatorConfig:
    # N ≈ 0.96/ε² for 95% CI at half-width ε=0.02 → 2400
    n: int = 2400
    b: int = 20            # bin count per dimension (for optional d-dim histogram)
    d_threshold: int = 2   # use raw reservoir when d >= this value


class ComplexEstimator:
    """Online estimator for one `complex` branch condition.

    Loaded from a cache file produced by JointReservoirBuilder.
    """

    def __init__(self, cache_path: str, config: Optional[ComplexEstimatorConfig] = None):
        self._cache_path = cache_path
        self._cfg = config or ComplexEstimatorConfig()
        self._fields: List[str] = []
        self._reservoir: List[Dict[str, float]] = []  # list of {field: value} dicts
        self._loaded = False
        self._constant_cache: Optional[float] = None  # fast path for arg-free exprs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate(self, cond: dict, args: list) -> float:
        """Return P(condition is true) ∈ [0, 1].

        Uses cached reservoir tuples.  Returns 1.0 on cold start (conservative).
        """
        if not self._loaded:
            self._load()
        if not self._reservoir:
            logger.warning("complex estimator cold start — cache miss at %s", self._cache_path)
            return 1.0

        expr = cond.get("expr", "")
        resolved = self._substitute_args(expr, args)

        # Constant-condition fast path: no ${arg.N} remaining after substitution
        if "${arg." not in resolved and self._constant_cache is not None:
            return self._constant_cache

        count = sum(1 for row in self._reservoir if self._eval_expr(resolved, row))
        ratio = count / len(self._reservoir)

        # Cache scalar if constant (no arg placeholders in original expr)
        if "${arg." not in expr:
            self._constant_cache = ratio

        return ratio

    def load_reservoir_from_records(self, fields: List[str],
                                     records: List[Dict[str, float]]) -> None:
        """Directly populate the reservoir from in-memory records (used in tests)."""
        self._fields = fields
        self._reservoir = [{f: rec[f] for f in fields if f in rec} for rec in records]
        self._loaded = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        self._loaded = True
        if not os.path.isfile(self._cache_path):
            return
        try:
            payload = json.loads(open(self._cache_path, encoding="utf-8").read())
            self._fields = payload.get("fields", [])
            raw = payload.get("reservoir", [])
            self._reservoir = [
                {f: float(v) for f, v in zip(self._fields, row)}
                for row in raw
                if len(row) == len(self._fields)
            ]
        except Exception as exc:
            logger.warning("failed to load complex estimator cache %s: %s", self._cache_path, exc)

    @staticmethod
    def _substitute_args(expr: str, args: list) -> str:
        """Replace ${arg.N} with the corresponding argument value."""
        def _repl(m: re.Match) -> str:
            idx = int(m.group(1))
            return str(args[idx]) if idx < len(args) else m.group(0)
        return re.sub(r"\$\{arg\.(\d+)\}", _repl, expr)

    @classmethod
    def _eval_expr(cls, expr: str, field_vals: Dict[str, float]) -> bool:
        """Safely evaluate a comparison expression over field values.

        Grammar: numeric field names, +/-/*//, comparison ops, numeric literals.
        Rejects anything outside this whitelist by raising ValueError.
        """
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"invalid expr syntax: {expr!r}") from exc

        cls._check_safe(tree)

        # Build eval namespace: field values as identifiers
        ns = {k: float(v) for k, v in field_vals.items()}
        try:
            result = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, ns)  # noqa: S307
        except Exception as exc:
            raise ValueError(f"expr eval failed for {expr!r}: {exc}") from exc
        return bool(result)

    @classmethod
    def _check_safe(cls, node: ast.AST) -> None:
        """Walk AST and raise ValueError on any node type not in _SAFE_NODES."""
        for n in ast.walk(node):
            if type(n) not in _SAFE_NODES:
                raise ValueError(
                    f"unsafe expression node {type(n).__name__!r} in complex condition expr"
                )
