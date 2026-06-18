"""Build LangGraph StateGraphs — main agent and RateExplorer."""

from langgraph.graph import END, START, StateGraph

from core.nodes import (
    baseline_node,
    deploy_and_test_rate_explorer_node,
    finish_node,
    finish_rate_explorer_node,
    guard_node,
    init_node,
    init_rate_explorer_node,
    parse_node,
    parse_rate_explorer_node,
    summarizer_node,
    think_node,
    think_rate_explorer_node,
)
from core.state import RateExplorerState, TuningState


# =============================================================================
# Main agent graph
# =============================================================================

def route_after_parse(state: TuningState) -> str:
    """Route based on parse_node output."""
    return state.get("next_action", "parse_error")


def build_graph() -> StateGraph:
    graph = StateGraph(TuningState)

    graph.add_node("init", init_node)
    graph.add_node("baseline", baseline_node)
    graph.add_node("think", think_node)
    graph.add_node("parse", parse_node)
    graph.add_node("summarizer", summarizer_node)
    graph.add_node("guard", guard_node)
    graph.add_node("finish", finish_node)

    graph.add_edge(START, "init")
    graph.add_edge("init", "baseline")
    graph.add_edge("baseline", "think")
    graph.add_edge("think", "parse")

    graph.add_conditional_edges("parse", route_after_parse, {
        "tool_done": "think",
        "summarize": "summarizer",
        "guard": "guard",
        "parse_error": "think",
    })

    graph.add_conditional_edges("guard",
                                lambda s: s.get("next_action", "finish"),
                                {"finish": "finish", "think": "think"})

    graph.add_edge("summarizer", "think")
    graph.add_edge("finish", END)

    return graph.compile()


# =============================================================================
# RateExplorer graph — explore optimal send_rate for a given config
# =============================================================================

def route_rate_explorer_after_parse(state: RateExplorerState) -> str:
    return state.get("next_action", "finish")


def build_rate_explorer_graph() -> StateGraph:
    graph = StateGraph(RateExplorerState)

    graph.add_node("init", init_rate_explorer_node)
    graph.add_node("deploy_and_test", deploy_and_test_rate_explorer_node)
    graph.add_node("think", think_rate_explorer_node)
    graph.add_node("parse", parse_rate_explorer_node)
    graph.add_node("finish", finish_rate_explorer_node)

    graph.add_edge(START, "init")
    graph.add_edge("init", "deploy_and_test")
    graph.add_edge("deploy_and_test", "think")
    graph.add_edge("think", "parse")

    graph.add_conditional_edges("parse", route_rate_explorer_after_parse, {
        "think": "think",
        "finish": "finish",
    })

    graph.add_edge("finish", END)

    return graph.compile()
