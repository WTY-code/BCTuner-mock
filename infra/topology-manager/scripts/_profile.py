"""Chaincode profile loader.

Resolves which chaincode profile is active for a deployment script invocation.
Resolution order: function arg > env PERFTUNER_CHAINCODE > json `active` field.
"""
import json
import os
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILES_PATH = BASE_DIR / "config" / "chaincode_profiles.json"


def _load_registry() -> dict:
    with open(PROFILES_PATH, "r") as f:
        return json.load(f)


def load_profile(name: Optional[str] = None) -> dict:
    """Return the active chaincode profile dict.

    Adds two derived absolute paths:
      - `local_src_dir`: absolute path to the chaincode source directory on the manager host
      - `caliper_bench_path_rel`: same as profile['caliper_config'], kept for clarity
    """
    registry = _load_registry()
    chosen = name or os.environ.get("PERFTUNER_CHAINCODE") or registry.get("active")
    if chosen is None:
        raise ValueError("No chaincode profile selected (no arg, no env, no 'active' in json).")
    profiles = registry.get("profiles", {})
    if chosen not in profiles:
        raise KeyError(f"Unknown chaincode profile: {chosen!r}. Available: {list(profiles)}")
    profile = dict(profiles[chosen])
    profile["_key"] = chosen
    profile["local_src_dir"] = str(BASE_DIR / profile["src_dir"])
    profile["caliper_bench_path_rel"] = profile["caliper_config"]
    return profile


def chaincode_install_path(profile: dict) -> str:
    """Container-side install path for `peer lifecycle chaincode package --path`."""
    return f"/opt/gopath/src/github.com/hyperledger/multiple-deployment/chaincode/go"


def caliper_bench_path(profile: dict) -> str:
    """Path to caliper bench config, relative to infra/caliper-benchmarks/."""
    return profile["caliper_config"]


if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    p = load_profile(arg)
    print(json.dumps(p, indent=2))
