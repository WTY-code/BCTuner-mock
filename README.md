# Auriga

**Harness-Governed LLM Agents for Effective Throughput Optimization in
Execute-Order-Validate Permissioned Blockchains**

Effective throughput in Execute-Order-Validate (EOV) blockchains is jointly
constrained by *system capacity* and *transaction success rate*:

```
TPS_eff = TPS_raw × R_succ
```

Existing approaches tune one factor in isolation: parameter-tuning frameworks
maximize raw throughput while ignoring MVCC aborts; concurrency-aware scheduling
mitigates aborts but assumes a fixed configuration. **Auriga** unifies both
through a split-phase architecture — an LLM-driven multi-agent parameter tuner
runs offline to lift the throughput ceiling, and an LLM-extracted predictive
scheduler runs online to eliminate intra-block MVCC conflicts before they reach
validation.

***

## Quick Start

### Prerequisites

- Python 3.9+
- Node.js 18+ (only for live Caliper experiments)
- A DeepSeek API key — [platform.deepseek.com](https://platform.deepseek.com)
- Hyperledger Fabric 3.1.3
- Hyperledger Caliper 0.6.0

### 1. Set credentials

```bash
cp .env.example .env
# Edit .env and fill in DEEPSEEK_API_KEY
```

The code reads `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL` from
the environment. Use `python-dotenv` or `export` the variables before running.

### 2. Install Python dependencies

```bash
pip install langgraph langchain-openai openai
pip install numpy pyyaml
```

***

## Parameter Tuning (paper §III)

A LangGraph multi-agent system that navigates Fabric's 102-parameter
configuration space through a ReAct-style closed loop. The Harness mediates
all agent–environment interaction, enforcing termination conditions and memory
discipline.

| Paper component                                            | Code                                                                                                                     |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Main Agent ReAct loop (§III.A.1)                           | `core/graph.py`, `core/nodes.py` — `think_node`, `parse_node`                                                            |
| Send-Rate Prober (§III.A.2)                                | `core/nodes.py` — `deploy_and_test_rate_explorer_node`, `think_rate_explorer_node`, `parse_rate_explorer_node`           |
| Experience Distiller (§III.A.3)                            | `core/nodes.py` — `summarizer_node`                                                                                      |
| Harness three-condition termination guard (§III.B.1)       | `core/nodes.py` — `guard_node`, `_check_exploration_depth`, `_check_group_diversification`, `_check_trend_stabilization` |
| Tool mediation (§III.B.2)                                  | `core/tools.py` — `diagnose`, `retrieve_knowledge`, `reflect`, `make_test_tool`                                          |
| Semantic memory KBs (§III.B.3)                             | `knowledge/para_explain.jsonl`, `knowledge/topology_kb.jsonl`                                                            |
| Length-triggered context compression (§III.B.3)            | `utils/utils.py` — `manage_context`                                                                                      |
| Topology-specific probing via `cliff_sharpness` (§III.A.2) | `core/nodes.py` — `_build_topology_guidance`                                                                             |

### Run

First start the local config server (one terminal):

```bash
python infra/local_config_server/server.py
```

Then run the agent (another terminal):

```bash
cd parameter_tuning
python main.py --url http://127.0.0.1:8080 --topology 8p --max-steps 10 --tps 2000
```

Outputs are written to `parameter_tuning/temp/`:

- `experience.jsonl` — per-trial structured deltas (episodic memory)
- `iteration.jsonl` — per-step state log
- `explore_send_rate.jsonl` — Send-Rate Prober trajectory
- `best_config.json` — best-found Fabric configuration
- `best_send_rate.json` — best-found send\_rate for that configuration

***

## Transaction Scheduling (paper §IV)

A two-phase predictive scheduler. **Offline**: an LLM Agent extracts symbolic AST
templates from chaincode source; a background orchestrator samples world-state
statistics. **Online**: at scheduling time, each incoming transaction is
mapped to its template, branch probabilities are evaluated, paths are unioned
under a confidence threshold θ, a conflict graph is constructed over the
predicted read/write sets, and a Bin-Packing-with-Conflicts (BPC) heuristic
groups non-conflicting transactions into batches.

| Paper component                                                         | Code                                                                                   |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| LLM-driven symbolic AST extraction (§IV.A.1, Listing 2)                 | `llm_extract.py`                                                                       |
| AST template repository                                                 | `templates/*.json` (see schema below)                                                  |
| World-state 1-D histograms — `linear_1d` conditions                     | `ast_engine/histogram_generator.py`, `histogram_store.py`, `histogram_orchestrator.py` |
| World-state joint reservoirs — `complex` conditions (correlated fields) | `ast_engine/joint_reservoir_builder.py`, `complex_estimator.py`                        |
| Online path probability prediction (§IV.B.1)                            | `ast_engine/evaluator.py` — `_estimate_prob` (dispatch by `kind`)                      |
| θ-Greedy Path Union (§IV.B.2)                                           | `ast_engine/evaluator.py` — `_select_union`                                            |
| Conflict graph construction (§IV.B.3)                                   | `core/conflict_engine.py`, `core/conflict_detector.py`                                 |
| Capacitated DSatur BPC heuristic (§IV.B.3)                              | `core/capacitated_graph_coloring_scheduler.py`                                         |
| Buffer assembly: RecycleBucket + Smart-Fill (§IV.B.3)                   | `core/pool_controller.py`                                                              |
| Multi-threaded BPC scheduling (Fig. 7a)                                 | `core/pipeline_orchestrator.py` — `_run_multi_threaded`, `_bpc_window_task`            |
| Chaincode-agnostic adapter (template-driven)                            | `adapters/generic.py`                                                                  |

### Run extraction (offline, one-shot per chaincode)

```bash
cd transaction_scheduling
python llm_extract.py --source <path/to/chaincode.go> --output templates/<name>_llm.json
```

### AST Template Schema

Each template is a JSON document with this top-level structure:

```jsonc
{
  "chaincode": "<name>",
  "histograms":      ["Field1", ...],          // fields needing 1-D histograms
  "joint_groups":    [["F1", "F2"], ...],      // field groups needing joint reservoirs
  "bypass_functions": ["readOnlyFn1", ...],    // read-only functions skipped by scheduler
  "functions": {
    "<fn_name>": { "body": [ <node>, ... ] }
  }
}
```

**Node types:**

| Type      | Purpose                                        | Required fields                                     |
| --------- | ---------------------------------------------- | --------------------------------------------------- |
| `read`    | Records a read of a world-state key            | `key`                                               |
| `write`   | Records a write to a world-state key           | `key`                                               |
| `iterate` | Sequential loop with `${arg.N}` for loop index | `var`, `start`, `count`, `body`                     |
| `branch`  | Conditional execution path                     | `condition`, `then`, `else`, `then_tag`, `else_tag` |

**Condition kinds** (dispatch by `kind`, never by variable count):

| Kind        | Description                                    | Estimator                                         |
| ----------- | ---------------------------------------------- | ------------------------------------------------- |
| `predicate` | Argument-only check (e.g. `arg.0 < 0`)         | Deterministic from args                           |
| `linear_1d` | One state variable, linear comparison          | 1-D histogram cumulative lookup                   |
| `complex`   | ≥ 2 correlated fields or non-linear expression | Empirical satisfaction ratio over joint reservoir |

***

## Infrastructure (paper §V)

| Path                                                       | Role                                                                          |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------- |
| [`infra/local_config_server/`](infra/local_config_server/) | HTTP config server that exposes Fabric parameters to the agent                |
| [`infra/caliper-benchmarks/`](infra/caliper-benchmarks/)   | Hyperledger Caliper workload definitions for Smallbank, Token-ERC-20, IOHeavy |
| [`infra/topology-manager/`](infra/topology-manager/)       | Ansible playbooks and scripts for deploying multi-peer Fabric networks        |

Reproducing the full evaluation (§V) requires:

- Hyperledger Fabric 3.1.3 with RAFT consensus
- 8–40 peer cloud VMs (8 vCPUs, 16 GiB RAM each)
- Hyperledger Caliper 0.6.0 driven by the configurations in `infra/caliper-benchmarks/`
- `npm install` inside `infra/caliper-benchmarks/` to restore Node dependencies

***

## Repository Structure

```
auriga/
├── README.md                                  (this file)
├── .env.example                               credentials template
├── .gitignore
│
├── parameter_tuning/                          paper §III — harness-governed agent
│   ├── main.py                                CLI entry point
│   ├── core/                                  graph, nodes, prompts, llm, state, tools
│   ├── utils/                                 sdk, metrics, context compression
│   └── knowledge/                             semantic memory (KBs)
│       ├── para_explain.jsonl                 parameter semantics
│       └── topology_kb.jsonl                  topology cliff profiles
│
├── transaction_scheduling/                    paper §IV — predictive scheduler
│   ├── llm_extract.py                         §IV.A.1 — LLM AST extraction
│   ├── ast_engine/                            selectivity estimation
│   │   ├── evaluator.py                       path enumerator + θ-Greedy union
│   │   ├── complex_estimator.py               joint reservoir → online ratio
│   │   ├── joint_reservoir_builder.py         offline joint tuple sampler
│   │   ├── histogram_*.py                     1-D histogram pipeline
│   │   ├── worldstate_sampler.py              Fabric state I/O
│   │   └── nodes.py                           AST node types
│   ├── adapters/                              GenericAdapter — template-driven, chaincode-agnostic
│   ├── core/                                  conflict engine, BPC scheduler, pipeline
│   │   ├── pipeline_orchestrator.py           multi-threaded scheduler (Fig. 7a)
│   │   ├── conflict_engine.py                 R/W-set conflict graph
│   │   ├── capacitated_graph_coloring_scheduler.py  DSatur BPC heuristic
│   │   └── pool_controller.py                 RecycleBucket + smart fill
│   └── templates/                             LLM-extracted AST templates (paper §IV.A.1)
│       ├── smallbank_llm.json
│       ├── token_erc20_llm.json
│       └── ioheavy_llm.json
│
└── infra/                                     paper §V — experimental infrastructure
    ├── local_config_server/                   runtime config endpoint
    ├── caliper-benchmarks/                    Caliper workload definitions
    └── topology-manager/                      Ansible Fabric deployment
```

