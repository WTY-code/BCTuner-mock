from typing import Protocol, runtime_checkable

from core.data_model import ParsedTx, ReadWriteSet


class WorkloadSchema:
    """Describes the fn_names, arg schemas, and key formats for a chaincode."""


@runtime_checkable
class ChaincodeAdapter(Protocol):
    name: str

    def parse_tx(self, raw: dict) -> ParsedTx:
        """Extract fn_name, args, and bypass flag from a raw caliper tx dict."""
        ...

    def predict_rwset(self, tx: ParsedTx) -> ReadWriteSet:
        """Run the AST evaluator to produce a predicted R/W set."""
        ...

    def workload_schema(self) -> WorkloadSchema:
        """Return the schema for tx generators."""
        ...
