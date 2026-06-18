"""General-purpose helper utilities for the tuning agent.

Each function here is self-contained and framework-agnostic — no dependency on
LangGraph, the config server, or the agent state.  This keeps ``core/`` focused
on graph logic and ``main.py`` thin.
"""

import json
import logging
import os
import re
import shutil
import sys
import time
from typing import Any, Dict, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(base_dir: Optional[str] = None) -> logging.Logger:
    """Initialise file + console logging for a fresh agent run.

    - Log file is written to ``<base_dir>/temp/run.log``.
    - If a previous ``run.log`` exists, it is moved to
      ``<base_dir>/temp/log_bk/run_<timestamp>.log`` before the new file is
      opened, so every run starts with a clean log.
    - Stream handler echoes INFO-level messages to stdout.

    Returns the ``"FabricAgent"`` logger.
    """
    if base_dir is None:
        base_dir = os.path.dirname(__file__)

    log_dir = os.path.join(base_dir, "temp")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "run.log")

    # Back up previous run log before overwriting
    if os.path.exists(log_file):
        bk_dir = os.path.join(log_dir, "log_bk")
        os.makedirs(bk_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        shutil.move(log_file, os.path.join(bk_dir, f"run_{ts}.log"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    return logging.getLogger("FabricAgent")


# ---------------------------------------------------------------------------
# JSON extraction (LLM output)
# ---------------------------------------------------------------------------

def extract_json_block(raw: str) -> Optional[str]:
    """Pull a JSON object string from LLM output with markdown-fence tolerance.

    Tries (in order):
    1. Content inside a ```json or ``` code fence.
    2. Raw text between the first ``{`` and last ``}``.

    Returns ``None`` when no plausible JSON is found.
    """
    raw = raw.strip()

    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if candidate.startswith("{"):
            return candidate

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]

    return None


# ---------------------------------------------------------------------------
# Message construction
# ---------------------------------------------------------------------------

def make_test_human_msg(tps: float, latency: float, success_rate: float,
                        effective_tps: float, config=None,
                        error=None) -> HumanMessage:
    """Build a HumanMessage carrying structured test result data."""
    payload: Dict[str, Any] = {
        "tps": tps,
        "latency": latency,
        "success_rate": float(success_rate),
        "effective_tps": effective_tps,
    }
    if config is not None:
        payload["config"] = config
    if error:
        payload["error"] = error
    return HumanMessage(content=json.dumps(payload))


def make_history_entry(thought: str, config: Dict[str, str], tps: float,
                       latency: float, success_rate: float,
                       effective_tps: float) -> Dict[str, Any]:
    """Build a structured history entry with test metrics."""
    return {
        "thought": thought,
        "config": config,
        "tps": tps,
        "latency": latency,
        "success_rate": success_rate,
        "effective_tps": effective_tps,
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# Tool / message serialization (DeepSeek compatibility)
# ---------------------------------------------------------------------------

def tool_to_openai(tool) -> Dict[str, Any]:
    """Convert a LangChain @tool to OpenAI function-calling format."""
    schema = tool.args_schema.schema()
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        },
    }


def message_to_dict(msg) -> Dict[str, Any]:
    """Convert a LangChain message to an OpenAI API-compatible dict.

    Preserves AIMessage fields required by non-OpenAI providers (e.g. DeepSeek's
    reasoning_content is handled in the caller via additional_kwargs).
    """
    if isinstance(msg, SystemMessage):
        return {"role": "system", "content": msg.content}
    if isinstance(msg, HumanMessage):
        return {"role": "user", "content": msg.content}
    if isinstance(msg, AIMessage):
        d: Dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        tool_calls = getattr(msg, "tool_calls", None) or []
        if tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["args"]),
                    },
                }
                for tc in tool_calls
            ]
        return d
    if isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "content": msg.content,
            "tool_call_id": msg.tool_call_id,
        }
    return {"role": "user", "content": str(msg.content)}


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def execute_tools(tool_calls: list, logger=None) -> list:
    """Execute multiple tool calls and return a ToolMessage for each.

    Each element of *tool_calls* is a ``(tool_call_id, tool_name, args)`` tuple.
    """
    from core.tools import diagnose, reflect, retrieve_knowledge

    tool_map = {
        "diagnose": diagnose,
        "retrieve_knowledge": retrieve_knowledge,
        "reflect": reflect,
    }

    msgs = []
    for tc_id, name, args in tool_calls:
        tool_fn = tool_map.get(name)
        if not tool_fn:
            msgs.append(ToolMessage(
                content=f"Error: unknown tool '{name}'",
                tool_call_id=tc_id,
            ))
            continue
        try:
            if args:
                result = tool_fn.invoke(args)
            else:
                result = tool_fn.invoke({})
        except Exception as e:
            if logger:
                logger.error("execute_tools: '%s' failed: %s", name, e)
            result = f"Error: {e}"
        msgs.append(ToolMessage(content=result, tool_call_id=tc_id))
        if logger:
            logger.info("execute_tools: '%s' done (%d chars)", name, len(result))
    return msgs


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------

def append_to_jsonl(file_path: str, entry: dict) -> None:
    """Append a JSON entry to a JSONL file, creating parent dirs if needed."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_jsonl(file_path: str) -> list:
    """Read all entries from a JSONL file. Returns empty list if not found."""
    try:
        with open(file_path, "r") as f:
            return [json.loads(line) for line in f if line.strip()]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


# ---------------------------------------------------------------------------
# File backup
# ---------------------------------------------------------------------------

def backup_jsonl(file_path: str) -> None:
    """Move a JSONL file to a *_bk sibling dir with timestamp suffix, if exists."""
    from pathlib import Path

    src = Path(file_path)
    if not src.exists():
        return
    dst_dir = src.parent / (src.stem + "_bk")
    dst_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = dst_dir / f"{src.stem}_{ts}.jsonl"
    src.rename(dst)


# ---------------------------------------------------------------------------
# Test execution
# ---------------------------------------------------------------------------

def run_single_test(
    url: str,
    overrides: dict,
    test_payload: str,
    tx_number: int,
) -> tuple:
    """Deploy config + run one Caliper benchmark, return (tps, latency, succ_rate, eff_tps)."""
    from utils.metrics import extract_throughput, extract_avg_latency, extract_success_rate
    from utils.sdk import ConfigSDK

    client = ConfigSDK(url)
    with client.session(configs=overrides) as sess:
        result = sess.test(test_payload)

    if result.get("status") == "failure":
        raise RuntimeError(
            f"Test execution failed: {result.get('error', 'unknown error')}"
        )

    tps = extract_throughput(result)
    latency = extract_avg_latency(result)
    success_rate = extract_success_rate(result, tx_number)
    effective_tps = tps * success_rate
    return tps, latency, success_rate, effective_tps


def run_test_with_retry(
    url: str,
    overrides: dict,
    test_payload: str,
    tx_number: int,
) -> tuple:
    """Run a single test with one retry on session conflict errors (409/500)."""
    from utils.sdk import ConfigSDK, ConfigSDKError, reset_session

    client = ConfigSDK(url)
    try:
        return run_single_test(url, overrides, test_payload, tx_number)
    except ConfigSDKError as e:
        if e.status in (409, 500):
            logging.getLogger("FabricAgent").warning(
                "run_test_with_retry: ConfigSDKError %d, resetting and retrying...",
                e.status,
            )
            reset_session(url)
            time.sleep(5)
            return run_single_test(url, overrides, test_payload, tx_number)
        raise


# ---------------------------------------------------------------------------
# Context management — compress old messages when approaching token limit
# ---------------------------------------------------------------------------

_CONTEXT_THRESHOLD = 400_000  # 40% of 1M token window


def estimate_tokens(messages: list) -> int:
    """Rough token count for a list of LangChain messages (chars / 2)."""
    total = 0
    for m in messages:
        content = getattr(m, "content", "") or ""
        total += len(content)
        # AIMessage tool_calls metadata
        for tc in getattr(m, "tool_calls", None) or []:
            total += len(json.dumps(tc, default=str))
    return total // 2


def manage_context(messages: list, keep_rounds: int = 5) -> list:
    """Compress old messages into a summary when token count exceeds threshold.

    Identifies round boundaries by summarizer's HumanMessage({"summary":...}),
    keeps the last *keep_rounds* rounds intact, and replaces older messages
    with a single summary HumanMessage generated by an LLM call.
    """
    tokens = estimate_tokens(messages)
    if tokens < _CONTEXT_THRESHOLD:
        return messages  # under threshold, no compression needed

    # Find round boundaries (summarizer HumanMessages with "summary" key)
    from langchain_core.messages import HumanMessage, SystemMessage

    boundary_indices = []
    for i, m in enumerate(messages):
        if isinstance(m, HumanMessage) and '"summary"' in (m.content or ""):
            boundary_indices.append(i)

    if len(boundary_indices) <= keep_rounds:
        return messages  # not enough rounds to compress

    # Split point: after (N - keep_rounds)th boundary
    split_at = boundary_indices[-(keep_rounds)] + 1
    old_msgs = messages[:split_at]
    recent_msgs = messages[split_at:]

    # Keep SystemMessage from old messages
    sys_msg = None
    for m in old_msgs:
        if isinstance(m, SystemMessage):
            sys_msg = m
            break

    # Build compression prompt from old messages (exclude SystemMessage)
    old_text = "\n".join(
        f"[{type(m).__name__}] {m.content or '(tool_calls)'}"
        for m in old_msgs if not isinstance(m, SystemMessage)
    )

    from core.prompts import CONTEXT_COMPRESSION_PROMPT
    prompt = CONTEXT_COMPRESSION_PROMPT.format(messages_text=old_text)

    from langchain_openai import ChatOpenAI
    from core.llm_config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
    compressor = ChatOpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        model=LLM_MODEL,
        temperature=0.3,
        max_tokens=1024,
        timeout=120,
    )
    try:
        response = compressor.invoke(prompt)
        summary_text = response.content or ""
    except Exception:
        summary_text = f"(compression failed, {len(old_msgs)} old messages truncated)"

    summary_msg = HumanMessage(content=json.dumps({
        "context_summary": summary_text,
        "compressed_rounds": f"前 {len(boundary_indices) - keep_rounds} 轮的调优历史摘要",
    }))

    # Reconstruct: SystemMessage (if any) + summary + recent messages
    result = [summary_msg]
    if sys_msg:
        result.insert(0, sys_msg)
    result.extend(recent_msgs)

    new_tokens = estimate_tokens(result)
    logging.getLogger("FabricAgent").info(
        "manage_context: compressed %d→%d messages (%d→%d tokens)",
        len(messages), len(result), tokens, new_tokens,
    )
    return result
