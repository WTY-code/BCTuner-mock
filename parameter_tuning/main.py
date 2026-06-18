#!/usr/bin/env python3
"""LangGraph-based Fabric parameter tuning agent entry point."""

import argparse
import os

from core.graph import build_graph
from utils.utils import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="LangGraph agent for Fabric parameter optimization")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Config server URL (default: %(default)s)")
    parser.add_argument("--max-steps", type=int, default=10, help="Max optimization steps (default: %(default)s)")
    parser.add_argument("--tps", type=int, default=2000, help="Target send rate TPS (default: %(default)s)")
    parser.add_argument("--topology", default="",
                        help="Network topology key for cliff_sharpness lookup "
                             "(e.g. '8p', '16p'). Leave empty if unknown.")
    args = parser.parse_args()

    logger = setup_logging(base_dir=os.path.dirname(__file__))
    logger.info("=== LangGraph Fabric Optimization Agent ===")
    logger.info("Config server: %s | max_steps=%d | target_tps=%d | topology=%s",
                args.url, args.max_steps, args.tps, args.topology or "(unset)")

    graph = build_graph()

    initial_state = {
        "baseline_snapshot": {},
        "max_steps": args.max_steps,
        "target_tps": args.tps,
        "config_server_url": args.url,
        "history": [],
        "messages": [],
        "current_config": {},
        "best_config": {},
        "best_effective_tps": 0.0,
        "step": 0,
        "next_action": "",
        "pending_config": {},
        "pending_tool_call_id": "",
        "pending_tool_name": "",
        "pending_tool_args": {},
        "topology": args.topology,
        "done": False,
    }

    final_state = graph.invoke(initial_state, config={"recursion_limit": 500})

    logger.info("=== Agent Finished ===")
    logger.info("Best Effective TPS: %.1f", final_state.get("best_effective_tps", 0))
    logger.info("Total steps: %d", final_state.get("step", 0))
    logger.info("History entries: %d", len(final_state.get("history", [])))


if __name__ == "__main__":
    main()
