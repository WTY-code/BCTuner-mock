"""Core state schema for the Fabric tuning LangGraph agent."""

import operator
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class TuningState(TypedDict):
    # --- immutable after init ---
    baseline_snapshot: Dict[str, Any]
    max_steps: int
    target_tps: int
    config_server_url: str

    # --- appended ---
    history: Annotated[List[Dict[str, Any]], operator.add]
    messages: Annotated[List[BaseMessage], add_messages]

    # --- overwritten ---
    current_config: Dict[str, str]
    best_config: Dict[str, str]
    best_effective_tps: float
    step: int
    next_action: str           # "tool_done" | "summarize" | "finish" | "parse_error"
    pending_config: Dict[str, str]
    pending_tool_call_id: str  # tool_call.id for ToolMessage linking
    pending_tool_name: str     # tool name to dispatch in tool_exec_node
    pending_tool_args: dict    # tool call arguments
    topology: str              # e.g. "8p" — used to load cliff_sharpness profile
    done: bool


# =============================================================================
# rate_explorer state — independent graph for send_rate exploration
# =============================================================================

class RateExplorerState(TypedDict):
    # --- passed from main agent ---
    config: Dict[str, str]           # parameter config being tested
    config_server_url: str           # ConfigSDK URL
    initial_send_rate: int           # starting send_rate (from main agent's target_tps)
    max_rounds: int                  # max exploration rounds (default 5)
    step: int                        # main agent step (for iteration.jsonl)

    # --- tracked ---
    best_send_rate: int              # best send_rate found so far
    best_effective_tps: float        # best effective TPS
    best_test_result: Optional[Dict[str, float]]  # full metrics of the best test

    # --- session ---
    session_id: Optional[str]        # active session ID
    session_active: bool             # whether session is active

    # --- appended ---
    messages: Annotated[List[BaseMessage], add_messages]

    # --- overwritten ---
    round: int                       # current round number
    next_action: str                 # "think" | "finish"
    topology: str                    # passed from TuningState; empty string if unknown
    done: bool
