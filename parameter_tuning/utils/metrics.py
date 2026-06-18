from typing import Any, Dict


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def extract_throughput(result: Dict[str, Any]) -> float:
    """Extract throughput from test result."""
    result_section = result.get("result") or {}
    total_throughput = 0.0
    for entry in result_section.values():
        throughput = entry.get("throughput")
        if throughput is not None:
            try:
                total_throughput += float(throughput)
            except (TypeError, ValueError):
                pass
    return total_throughput


def extract_avg_latency(result: Dict[str, Any]) -> float:
    """Extract average latency from test result."""
    result_section = result.get("result") or {}
    latency_sum = 0.0
    latency_samples = 0
    for entry in result_section.values():
        lat = entry.get("avg-lat")
        if lat is None:
            lat = entry.get("max-lat")
        if lat is not None:
            latency_sum += _safe_float(lat)
            latency_samples += 1
    return (latency_sum / latency_samples) if latency_samples else 0.0


def extract_success_rate(result: Dict[str, Any], tx_number: int) -> float:
    """Extract success rate from test result."""
    result_section = result.get("result") or {}
    total_succ = 0
    for entry in result_section.values():
        succ = entry.get("succ")
        if succ is not None:
            try:
                total_succ += int(succ)
            except (TypeError, ValueError):
                pass
    if tx_number <= 0:
        return 0.0
    return total_succ / tx_number
