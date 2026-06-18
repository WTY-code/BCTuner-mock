"""AST node definitions for path templates.

Each node type corresponds to a syntactic element that can appear in a
chaincode execution path.  The evaluator walks these nodes to produce a
predicted ReadWriteSet.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Read:
    key_expr: str  # e.g. "checking:${arg.cust_id}"


@dataclass
class Write:
    key_expr: str
    value_desc: str = ""  # human-readable, not parsed


@dataclass
class CompositeKey:
    prefix: str
    parts: List[str]  # template expressions, e.g. ["balance", "${arg.account}"]


@dataclass
class Iterate:
    var: str
    start: int  # expression or literal
    count_expr: str  # e.g. "15" or "${arg.o_ol_cnt}"
    body: List[Any]  # list of AST nodes


@dataclass
class Branch:
    condition: Dict  # {"kind": "linear_1d", "field": "CheckingBalance", "op": ">=", "rhs_expr": "${arg.amount}"}
    then_body: List[Any]
    else_body: List[Any]
    prob_hint: Optional[float] = None  # histogram lookup or literal


@dataclass
class MonteCarloFallback:
    """Fallback for complex cases beyond 1D histogram coverage."""
    description: str
    samples: int = 1000


@dataclass
class Template:
    fn_name: str
    body: List[Any]
