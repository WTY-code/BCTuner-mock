import subprocess
import time
import sys
import json
import argparse
from pathlib import Path

from _profile import load_profile

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"

def run_script(script_name, chaincode=None):
    print(f"\n>>> Running {script_name}...")
    script_path = SCRIPTS_DIR / script_name
    cmd = ["python3", str(script_path)]
    if chaincode:
        cmd.extend(["--chaincode", chaincode])
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print(f"!!! Error running {script_name}. Aborting.")
        sys.exit(1)

def run_caliper(profile):
    print(f"\n>>> Running Caliper Benchmark (chaincode: {profile['name']}, config: {profile['caliper_config']})...")
    caliper_dir = BASE_DIR.parent / "caliper-benchmarks"
    cmd = (
        "npx caliper launch manager "
        "--caliper-workspace . "
        "--caliper-networkconfig networks/fabric/network-config-gateway.yaml "
        f"--caliper-benchconfig {profile['caliper_config']} "
        "--caliper-flow-only-test "
        "--caliper-fabric-gateway-enabled"
    )
    log_dir = BASE_DIR / "result" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ".last_caliper.log"
    p = subprocess.Popen(cmd, cwd=caliper_dir, shell=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    with open(log_path, "w") as log_f:
        for line in p.stdout:
            print(line, end="", flush=True)
            log_f.write(line)
    p.wait()
    if p.returncode != 0:
        print("!!! Caliper benchmark failed.")
        sys.exit(1)
    subprocess.run(["python3", str(SCRIPTS_DIR / "log_run.py"),
                    "--chaincode", profile["_key"],
                    "--caliper-log", str(log_path)], check=True)

def main():
    parser = argparse.ArgumentParser(description="Run Fabric Experiment Pipeline")
    parser.add_argument("--deploy-only", action="store_true", help="Only deploy the network, do not run Caliper benchmark")
    parser.add_argument("--test-only", action="store_true", help="Only run Caliper benchmark, assume network is deployed")
    parser.add_argument("--chaincode", default="smallbank", help="Chaincode profile name (default: env PERFTUNER_CHAINCODE or json `active`)")
    args = parser.parse_args()

    profile = load_profile(args.chaincode)
    chaincode_key = profile["_key"]
    chaincode_name = profile["name"]
    print(f"=== Active chaincode profile: {chaincode_key} (name={chaincode_name}) ===")

    if args.test_only:
        run_caliper(profile)
        return

    print("=== Starting Fabric Experiment Pipeline ===")
    start_time = time.time()

    # 0. Initialize Resources (Ensure chaincode is cached)
    # This is idempotent, checks if vendor exists
    run_script("0_init_resources.py", chaincode=chaincode_key)

    # 1. Generate Artifacts (Crypto, Genesis, ConfigTx)
    run_script("1_generate_artifacts.py")

    # 2. Generate Docker Compose
    run_script("2_generate_compose.py")

    # 3. Deploy Network (Start Containers)
    run_script("3_deploy.py", chaincode=chaincode_key)

    # 4. Create Channel & Join Nodes
    run_script("4_create_channel.py")

    # 5. Deploy Chaincode (Install, Approve, Commit)
    run_script("5_deploy_chaincode.py", chaincode=chaincode_key)

    # 6. Generate Connection Profiles
    run_script("7_generate_ccp.py")

    # 7. Generate Caliper Network Config (Gateway)
    print("Generating Caliper Network Config...")
    # Load network config to get all orgs for multi-org endorsement
    network_cfg_path = BASE_DIR / "config/enhanced_network_config.json"
    if not network_cfg_path.exists():
        network_cfg_path = BASE_DIR / "config/network_config.json"
    with open(network_cfg_path) as f:
        network_cfg = json.load(f)
    orgs_cfg = network_cfg["topology"]["orgs"]

    orgs_yaml = ""
    for org in orgs_cfg:
        org_name = org["name"]
        org_domain = org["domain"]
        orgs_yaml += f"""
  - mspid: {org_name}MSP
    identities:
      certificates:
      - name: 'User1'
        clientPrivateKey:
          path: '{BASE_DIR}/artifacts/crypto-config/peerOrganizations/{org_domain}/users/User1@{org_domain}/msp/keystore/priv_sk'
        clientSignedCert:
          path: '{BASE_DIR}/artifacts/crypto-config/peerOrganizations/{org_domain}/users/User1@{org_domain}/msp/signcerts/User1@{org_domain}-cert.pem'
    connectionProfile:
      path: '{BASE_DIR}/artifacts/connection-profiles/connection-{org_name.lower()}.yaml'
      discover: true
"""

    caliper_config_content = f"""
name: Caliper Network Gateway
version: "2.0.0"
caliper:
  blockchain: fabric
channels:
  - channelName: mychannel
    contracts:
    - id: {chaincode_name}
organizations:{orgs_yaml}
"""
    with open(BASE_DIR.parent / "caliper-benchmarks/networks/fabric/network-config-gateway.yaml", "w") as f:
        f.write(caliper_config_content)

    if args.deploy_only:
        print("\n=== Deployment Completed (Skipping Benchmark) ===")
    else:
        # 8. Run Caliper
        run_caliper(profile)

    end_time = time.time()
    duration = end_time - start_time
    print(f"\n=== Experiment Completed in {duration:.2f} seconds ===")

if __name__ == "__main__":
    main()
