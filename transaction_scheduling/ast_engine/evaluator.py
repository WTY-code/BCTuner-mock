"""AST evaluator — walks a template tree to produce a predicted ReadWriteSet.

Supports nodes: read, write, iterate, branch.
Branch probability estimation dispatches on condition `kind`:
  - predicate   → evaluate directly from transaction args (deterministic)
  - linear_1d   → 1-D HistogramStore cumulative bin-count lookup
  - complex     → empirical satisfaction ratio over a joint sample reservoir
                  (ComplexEstimator); returns 1.0 (conservative) on cold start
"""

import re
from typing import Any, Dict, List, Optional

from core.data_model import ReadWriteSet


class Evaluator:
    def __init__(self,
                 histogram_stores: Optional[Dict[str, "HistogramStore"]] = None,
                 complex_estimators: Optional[Dict[str, "ComplexEstimator"]] = None,
                 theta: float = 0.95):
        self.histograms: Dict[str, "HistogramStore"] = histogram_stores or {}
        self.complex_estimators: Dict[str, "ComplexEstimator"] = complex_estimators or {}
        self.theta = theta

    def eval(self, template: dict, args: list) -> ReadWriteSet:
        body = template.get("body", [])
        variants = self._walk(body, args)
        return self._select_union(variants, theta=self.theta)

    # ------------------------------------------------------------------
    #  internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, expr: str, args: list) -> str:
        """Replace ``${arg.N}`` placeholders with actual argument values."""
        if not isinstance(expr, str):
            return str(expr)

        def _repl(m: re.Match) -> str:
            idx = int(m.group(1))
            return str(args[idx]) if idx < len(args) else m.group(0)

        return re.sub(r"\$\{arg\.(\d+)\}", _repl, expr)

    def _walk(self, nodes: List[dict], args: list) -> List[dict]:
        """Recursively walk a list of AST nodes, returning a list of path variants.

        Each variant is::

            {"prob": float, "reads": List[str], "writes": List[str], "path_id": str}
        """
        variants: List[dict] = [{"prob": 1.0, "reads": [], "writes": [], "path_id": "root", "tags": set()}]

        for node in nodes:
            t = node.get("type", "")
            if t in ("read", "write"):
                key = self._resolve(node.get("key", node.get("key_expr", "")), args)
                field = "reads" if t == "read" else "writes"
                for v in variants:
                    v[field].append(key)
            elif t == "composite_key":
                pass  # consumed by parent read/write nodes
            elif t == "iterate":
                var = node["var"]
                start = int(self._resolve(str(node.get("start", 0)), args))
                count_expr = node.get("count", "0")
                count = int(self._resolve(count_expr, args))
                body = node.get("body", [])
                new_variants: List[dict] = []
                for v in variants:
                    iter_results: List[List[dict]] = []
                    for i in range(start, start + count):
                        sub_args = list(args) + [str(i)]
                        sub_results = self._walk(body, sub_args)
                        if sub_results:
                            iter_results.append(sub_results)
                    if not iter_results:
                        new_variants.append(v)
                        continue
                    combined = [v]
                    for batch in iter_results:
                        next_combined = []
                        for cv in combined:
                            for sv in batch:
                                next_combined.append({
                                    "prob": cv["prob"] * sv["prob"],
                                    "reads": cv["reads"] + sv["reads"],
                                    "writes": cv["writes"] + sv["writes"],
                                    "path_id": f"{cv['path_id']}.{var}={start}..{start+count-1}",
                                    "tags": cv.get("tags", set()) | sv.get("tags", set()),
                                })
                        combined = next_combined
                    new_variants.extend(combined)
                variants = new_variants
            elif t == "branch":
                cond = node.get("condition", {})
                then_body = node.get("then", [])
                else_body = node.get("else", [])
                then_tag = node.get("then_tag", "success")
                else_tag = node.get("else_tag", "abort")
                prob_then = self._estimate_prob(cond, args)
                prob_else = max(0.0, 1.0 - prob_then)
                new_variants = []
                for v in variants:
                    for body_nodes, p, branch_role in [(then_body, prob_then, "then"), (else_body, prob_else, "else")]:
                        if p <= 0:
                            continue
                        sub_results = self._walk(body_nodes, args)
                        for sv in sub_results:
                            tags = v.get("tags", set()) | sv.get("tags", set())
                            tags.add(f"branch_{branch_role}")
                            if branch_role == "then" and then_tag:
                                tags.add(then_tag)
                            if branch_role == "else" and else_tag:
                                tags.add(else_tag)
                            new_variants.append({
                                "prob": v["prob"] * p * sv["prob"],
                                "reads": v["reads"] + sv["reads"],
                                "writes": v["writes"] + sv["writes"],
                                "path_id": f"{v['path_id']}.branch({_branch_label(cond)})",
                                "tags": tags,
                            })
                variants = new_variants if new_variants else variants
            elif t == "monte_carlo_fallback":
                # Legacy node type — treated as identity pass-through.
                pass
        return variants

    def _estimate_prob(self, cond: dict, args: list) -> float:
        """Estimate P(condition is true).

        Dispatches on condition ``kind`` (never on variable count):
          - ``predicate``  — evaluate directly from args; deterministic 0.0 or 1.0.
          - ``linear_1d``  — 1-D histogram cumulative lookup; 0.5 if no histogram.
          - ``complex``    — empirical satisfaction ratio over joint reservoir;
                             1.0 (conservative, branch-taken) on cold start.
        An explicit ``prob`` hint overrides dispatch.
        """
        if "prob" in cond:
            return float(cond["prob"])

        kind = cond.get("kind", "")

        if kind == "predicate":
            return self._estimate_predicate(cond, args)

        if kind == "linear_1d":
            field = cond.get("field", "")
            op = cond.get("op", ">=")
            rhs_expr = cond.get("rhs", "0")
            rhs = float(self._resolve(rhs_expr, args))
            store = self.histograms.get(field)
            if store is None:
                return 0.5
            if op == ">=":
                return store.prob_ge(rhs)
            elif op == "<":
                return 1.0 - store.prob_ge(rhs)
            elif op == ">":
                return store.prob_ge(rhs + 1e-9)
            elif op == "<=":
                return 1.0 - store.prob_ge(rhs + 1e-9)
            return 0.5

        if kind == "complex":
            fields = cond.get("fields", [])
            # Key into complex_estimators: use frozenset of fields for lookup
            key = frozenset(fields)
            estimator = self.complex_estimators.get(key)
            if estimator is None:
                # Cold start: no estimator registered → conservative (branch taken)
                return 1.0
            return estimator.estimate(cond, args)

        # Unknown kind: conservative fallback
        return 1.0

    def _estimate_predicate(self, cond: dict, args: list) -> float:
        """Evaluate a predicate condition directly from transaction arguments."""
        param = cond.get("param", "")
        op = cond.get("op", ">=")
        rhs_expr = cond.get("rhs", "0")
        rhs = float(self._resolve(rhs_expr, args))
        # param is "arg.N" style
        m = re.match(r"arg\.(\d+)", param)
        if not m:
            return 1.0
        idx = int(m.group(1))
        if idx >= len(args):
            return 1.0
        try:
            val = float(args[idx])
        except (ValueError, TypeError):
            return 1.0
        result = {
            ">=": val >= rhs,
            ">":  val >  rhs,
            "<":  val <  rhs,
            "<=": val <= rhs,
            "==": val == rhs,
            "!=": val != rhs,
        }.get(op, True)
        return 1.0 if result else 0.0

    def _select_union(self, variants: List[dict], theta: float = 0.95) -> ReadWriteSet:
        """Greedy union until cumulative probability >= theta."""
        xs = sorted(variants, key=lambda v: v["prob"], reverse=True)
        reads: List[str] = []
        writes: List[str] = []
        cum = 0.0
        success_prob = 0.0
        has_branch = any(("branch_then" in v.get("tags", set()) or "branch_else" in v.get("tags", set())) for v in xs)
        for v in xs:
            if cum >= theta:
                break
            cum += v["prob"]
            for k in v["reads"]:
                if k not in reads:
                    reads.append(k)
            for k in v["writes"]:
                if k not in writes:
                    writes.append(k)
            if "success" in v.get("tags", set()):
                success_prob = max(success_prob, v["prob"])
        if has_branch:
            valid = success_prob >= theta
        else:
            valid = True  # linear path, no branch
        return ReadWriteSet(reads=set(reads), writes=set(writes), valid=valid)


def _branch_label(cond: dict) -> str:
    kind = cond.get("kind", "?")
    if kind == "complex":
        return cond.get("expr", "complex")
    return cond.get("field", kind)
