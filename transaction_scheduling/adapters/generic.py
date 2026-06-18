"""Generic adapter — works with ANY LLM-extracted template JSON.

A single class driven entirely by the template JSON.  Performs strict schema
validation on load: fails loudly (naming the offending file) rather than
silently accepting malformed input.

Usage::

    adapter = GenericAdapter("templates/smallbank_llm.json")
    rwset = adapter.predict({"functionName": "write_check", "arguments": ["100", "user1"]})
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Set

from core.data_model import ParsedTx, ReadWriteSet
from adapters.base import WorkloadSchema
from ast_engine.evaluator import Evaluator
from ast_engine.histogram_store import HistogramStore

_VALID_KINDS = {"predicate", "linear_1d", "complex"}
_VALID_TYPES = {"read", "write", "branch", "iterate", "composite_key", "monte_carlo_fallback"}


class GenericAdapter:
    """Chaincode-agnostic adapter driven entirely by an AST template JSON file."""

    def __init__(self, templates_path: str, histograms_dir: Optional[str] = None,
                 reservoirs_dir: Optional[str] = None):
        with open(templates_path, "r") as f:
            registry = json.load(f)

        # Strict schema validation — fails loudly naming the offending file
        _validate_schema(registry, templates_path)

        self.name = registry.get("chaincode", "unknown")
        self._templates: Dict[str, dict] = registry["functions"]
        self._bypass: set = set(registry.get("bypass_functions", []))
        self._histogram_fields: list = registry.get("histograms", [])
        self._joint_groups: List[List[str]] = registry.get("joint_groups", [])

        # Normalise function keys: lowercase + strip underscores for flexible matching
        nfn = lambda s: s.lower().replace("_", "")
        self._templates = {nfn(k): v for k, v in self._templates.items()}
        self._bypass = {nfn(b) for b in self._bypass}

        # Resolve directories
        if histograms_dir is None:
            repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            histograms_dir = os.path.join(repo, "artifacts", "histograms")
        if reservoirs_dir is None:
            reservoirs_dir = os.path.join(histograms_dir, "reservoirs")

        # Load 1-D histogram stores
        stores: Dict[str, HistogramStore] = {}
        for field in self._histogram_fields:
            p = os.path.join(histograms_dir, f"{field}.json")
            if os.path.isfile(p):
                stores[field] = HistogramStore(p)

        # Load complex estimators (one per joint group)
        complex_estimators = {}
        if self._joint_groups:
            from ast_engine.complex_estimator import ComplexEstimator, ComplexEstimatorConfig
            from ast_engine.joint_reservoir_builder import condition_id as _cid
            cfg = ComplexEstimatorConfig()
            # Collect expr per group by scanning templates
            exprs_by_group = _collect_complex_exprs(registry)
            for group in self._joint_groups:
                key = frozenset(group)
                expr = exprs_by_group.get(key, "")
                cid = _cid(group, expr)
                cache_path = os.path.join(reservoirs_dir, f"reservoir_{cid}.json")
                estimator = ComplexEstimator(cache_path, cfg)
                complex_estimators[key] = estimator

        self._evaluator = Evaluator(histogram_stores=stores,
                                    complex_estimators=complex_estimators,
                                    theta=0.95)

    # ---- ChaincodeAdapter protocol ----

    def _norm(self, s: str) -> str:
        return s.lower().replace("_", "")

    def parse_tx(self, raw: dict) -> ParsedTx:
        fn = self._norm(raw.get("functionName", raw.get("fn", "")))
        return ParsedTx(
            fn_name=fn,
            args=raw.get("arguments", raw.get("args", [])),
            bypass=fn in self._bypass,
        )

    def predict_rwset(self, tx: ParsedTx) -> ReadWriteSet:
        if tx.bypass:
            return ReadWriteSet(reads=set(), writes=set(), valid=True)
        template = self._templates.get(tx.fn_name)
        if template is None:
            return ReadWriteSet(reads=set(), writes=set(), valid=False)
        return self._evaluator.eval(template, tx.args)

    def workload_schema(self) -> WorkloadSchema:
        return WorkloadSchema()

    def predict(self, tx_data: dict) -> ReadWriteSet:
        """Compatibility wrapper for ConflictEngine / PipelineOrchestrator."""
        parsed = self.parse_tx(tx_data)
        return self.predict_rwset(parsed)


# ---------------------------------------------------------------------------
# Schema validation helpers
# ---------------------------------------------------------------------------

def _validate_schema(registry: dict, path: str) -> None:
    """Validate template registry.  Raises ValueError naming *path* on any violation."""
    errors: List[str] = []

    if "chaincode" not in registry:
        errors.append("missing top-level 'chaincode'")
    if "functions" not in registry:
        errors.append("missing top-level 'functions'")
        _raise_if(errors, path)

    joint_groups_raw: List[List[str]] = registry.get("joint_groups", [])
    declared_groups: List[frozenset] = [frozenset(g) for g in joint_groups_raw]

    for fn, fd in registry["functions"].items():
        body = fd.get("body", [])
        if not isinstance(body, list):
            errors.append(f"{fn}: 'body' must be a list")
            continue
        _validate_nodes(body, fn, declared_groups, errors)

    _raise_if(errors, path)


def _validate_nodes(nodes: list, path: str,
                    declared_groups: List[frozenset],
                    errors: List[str]) -> None:
    for i, node in enumerate(nodes):
        p = f"{path}[{i}]"
        t = node.get("type", "")
        if t not in _VALID_TYPES:
            errors.append(f"{p}: unknown node type {t!r} (valid: {sorted(_VALID_TYPES)})")
        if t in ("read", "write") and "key" not in node:
            errors.append(f"{p}: {t} node missing 'key'")
        if t == "iterate":
            _validate_nodes(node.get("body", []), p + ".body", declared_groups, errors)
        if t == "branch":
            # Both tags required
            if "then_tag" not in node:
                errors.append(f"{p}: branch missing 'then_tag'")
            if "else_tag" not in node:
                errors.append(f"{p}: branch missing 'else_tag'")
            # Condition kind must be known
            cond = node.get("condition", {})
            kind = cond.get("kind", "")
            if kind not in _VALID_KINDS:
                errors.append(
                    f"{p}: unknown condition kind {kind!r} (valid: {sorted(_VALID_KINDS)})"
                )
            if kind == "complex":
                fields = cond.get("fields")
                expr = cond.get("expr")
                if not fields or not isinstance(fields, list):
                    errors.append(f"{p}: complex condition missing 'fields' list")
                if not expr:
                    errors.append(f"{p}: complex condition missing 'expr'")
                if fields and declared_groups is not None:
                    fset = frozenset(fields)
                    if fset not in declared_groups:
                        errors.append(
                            f"{p}: complex condition fields {sorted(fields)} "
                            f"not declared in top-level 'joint_groups'"
                        )
            # Recurse into branches
            _validate_nodes(node.get("then", []), p + ".then", declared_groups, errors)
            _validate_nodes(node.get("else", []), p + ".else", declared_groups, errors)


def _raise_if(errors: List[str], path: str) -> None:
    if errors:
        bullet = "\n  • ".join(errors)
        raise ValueError(f"Template schema errors in {path!r}:\n  • {bullet}")


def _collect_complex_exprs(registry: dict) -> Dict[frozenset, str]:
    """Walk all function bodies and collect expr strings keyed by field group."""
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
