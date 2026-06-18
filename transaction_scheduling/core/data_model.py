from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class ReadWriteSet:
    reads: Set[str] = field(default_factory=set)
    writes: Set[str] = field(default_factory=set)
    valid: bool = True

    def to_dict(self) -> dict:
        return {"reads": sorted(self.reads), "writes": sorted(self.writes), "valid": self.valid}


@dataclass
class ParsedTx:
    fn_name: str
    args: dict
    bypass: bool = False


@dataclass
class Transaction:
    __slots__ = ["id", "data", "age", "reads", "writes", "valid"]

    id: str
    data: Dict
    age: int
    reads: Set[str]
    writes: Set[str]
    valid: bool

    @classmethod
    def from_dict(cls, data: Dict) -> "Transaction":
        return cls(
            id=data.get("id", "unknown"),
            data=data,
            age=0,
            reads=set(),
            writes=set(),
            valid=True,
        )

    def to_dict(self) -> dict:
        return self.data
