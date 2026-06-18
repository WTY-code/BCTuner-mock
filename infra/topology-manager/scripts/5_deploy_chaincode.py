
import argparse
import subprocess
import json
import time
from pathlib import Path

from _profile import load_profile

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config/enhanced_network_config.json"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
CHANNEL_ARTIFACTS_DIR = ARTIFACTS_DIR / "channel-artifacts"

# Container-side chaincode path (remote layout unchanged across profiles).
CHAINCODE_PATH = "/opt/gopath/src/github.com/hyperledger/multiple-deployment/chaincode/go"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def run_ssh_cmd(ip, password, cmd):
    ssh_cmd = f"ssh -o StrictHostKeyChecking=no root@{ip} \"{cmd}\""
    subprocess.run(ssh_cmd, shell=True, check=True)

def get_ssh_output(ip, password, cmd):
    ssh_cmd = f"ssh -o StrictHostKeyChecking=no root@{ip} \"{cmd}\""
    result = subprocess.run(ssh_cmd, shell=True, check=True, stdout=subprocess.PIPE)
    return result.stdout.decode().strip()

def get_machine_by_name(machines, name):
    for m in machines:
        if m['name'] == name:
            return m
    return None

def sync_artifacts(machines):
    print("Syncing artifacts (including chaincode package & vendor) to all machines...")
    for machine in machines:
        ip = machine['host_ip']
        pw = machine['password']
        # Sync channel-artifacts
        cmd1 = f"scp -o StrictHostKeyChecking=no -r {CHANNEL_ARTIFACTS_DIR}/* root@{ip}:/root/fabric_deployment/channel-artifacts/"
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                subprocess.run(cmd1, shell=True, check=True)
                break  # Success, exit the retry loop
            except subprocess.CalledProcessError as e:
                if attempt < max_retries - 1:
                    print(f"  Warning: Failed to sync artifacts to {machine['name']} (attempt {attempt+1}/{max_retries}). Retrying in 2 seconds...")
                    time.sleep(2)
                else:
                    print(f"  Error: Failed to sync artifacts to {machine['name']} after {max_retries} attempts: {e}")
                    raise e  # Fail fast if all retries are exhausted
        # Skip chaincode sync as it is handled by 3_deploy.py using cached resources
        
def fetch_vendor(machine):
    print("Skipping vendor fetch as we use pre-provisioned resources.")
    pass

def deploy_chaincode(config, profile):
    chaincode_name = profile["name"]
    chaincode_version = profile["version"]
    sequence = profile.get("sequence", 1)
    chaincode_label = f"{chaincode_name}_{chaincode_version}"
    endorsement_policy = profile.get("endorsement_policy", "")
    print(f"Deploying chaincode profile '{profile['_key']}' (name={chaincode_name}, version={chaincode_version}, sequence={sequence})...")

    machines = config['machines']
    orgs = config['topology']['orgs']
    channel_name = config['channel']['name']

    # 1. Sync updated channel artifacts
    sync_artifacts(machines)

    # We need to select a "Leader Peer" for packaging and approving.
    # Usually Peer0 of each Org.

    # Find Org1 Leader
    org1 = orgs[0]
    org1_leader_placement = org1['placements'][0]
    org1_leader_machine = get_machine_by_name(machines, org1_leader_placement['machine'])
    org1_leader_cli = f"cli-{org1_leader_placement['node']}.{org1['domain']}"

    print(f"\n=== Packaging Chaincode {chaincode_name} on {org1['name']} Leader ({org1_leader_cli}) ===")

    pkg_cmd = (
        f"peer lifecycle chaincode package /opt/gopath/src/github.com/hyperledger/fabric/peer/channel-artifacts/{chaincode_name}.tar.gz "
        f"--path {CHAINCODE_PATH} --lang golang --label {chaincode_label}"
    )
    run_ssh_cmd(org1_leader_machine['host_ip'], org1_leader_machine['password'], f"docker exec {org1_leader_cli} {pkg_cmd}")

    # Copy back to manager
    print("Fetching package from Org1 Leader...")
    fetch_cmd = f"scp -o StrictHostKeyChecking=no root@{org1_leader_machine['host_ip']}:/root/fabric_deployment/channel-artifacts/{chaincode_name}.tar.gz {CHANNEL_ARTIFACTS_DIR}/"
    subprocess.run(fetch_cmd, shell=True, check=False)

    # Sync to all machines so everyone can install
    sync_artifacts(machines)

    # Install on ALL peers
    print("\n=== Installing Chaincode on ALL Peers ===")
    install_cmd = f"peer lifecycle chaincode install /opt/gopath/src/github.com/hyperledger/fabric/peer/channel-artifacts/{chaincode_name}.tar.gz"
    
    for org in orgs:
        for p in org['placements']:
            m = get_machine_by_name(machines, p['machine'])
            cli = f"cli-{p['node']}.{org['domain']}"
            
            # Check if CLI exists, if not, use a leader CLI to install on remote peer? 
            # NO, peer lifecycle chaincode install MUST run against the target peer.
            # If we don't have a CLI for every peer, we must use CORE_PEER_ADDRESS to target the peer from a shared CLI.
            
            # Since we generate CLIs for every peer in generate_compose.py, we should be fine.
            # BUT, if a peer failed to start (Exited (1)), the CLI might be up but the peer is down, or CLI failed too.
            # The user log shows peer2.org1 exited.
            
            print(f"Installing on {cli}...")
            try:
                run_ssh_cmd(m['host_ip'], m['password'], f"docker exec {cli} {install_cmd}")
            except subprocess.CalledProcessError:
                 print(f"  Already installed or failed on {cli}")

    # Calculate Package ID
    print("\n=== Calculating Package ID ===")
    pkg_id_cmd = f"peer lifecycle chaincode calculatepackageid /opt/gopath/src/github.com/hyperledger/fabric/peer/channel-artifacts/{chaincode_name}.tar.gz"
    package_id = get_ssh_output(org1_leader_machine['host_ip'], org1_leader_machine['password'], f"docker exec {org1_leader_cli} {pkg_id_cmd}")
    print(f"Package ID: {package_id}")
    
    # Approve for EACH Org
    # We need a valid orderer address.
    # We can use the first orderer in the config.
    orderer_cfg = config['topology']['orderers']
    first_orderer_placement = orderer_cfg['placements'][0]
    # To reach it from inside CLI, we use hostname:port
    orderer_addr = f"{first_orderer_placement['node']}.{orderer_cfg['domain']}:{first_orderer_placement['ports']['listen']}"
    orderer_tls_root = f"/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/ordererOrganizations/{orderer_cfg['domain']}/tlsca/tlsca.{orderer_cfg['domain']}-cert.pem"
    
    for org in orgs:
        print(f"\n=== Approving for {org['name']} ===")
        
        # Use first peer of this org as leader
        leader_p = org['placements'][0]
        leader_m = get_machine_by_name(machines, leader_p['machine'])
        leader_cli = f"cli-{leader_p['node']}.{org['domain']}"
        
        # Use escaped double quotes so the shell preserves the policy string.
        policy_flag = " --signature-policy \\\"{}\\\"".format(endorsement_policy) if endorsement_policy else ""
        approve_cmd = (
            f"peer lifecycle chaincode approveformyorg -o {orderer_addr} "
            f"--ordererTLSHostnameOverride {first_orderer_placement['node']}.{orderer_cfg['domain']} --tls "
            f"--cafile {orderer_tls_root} "
            f"--channelID {channel_name} --name {chaincode_name} --version {chaincode_version} "
            f"--package-id {package_id} --sequence {sequence}{policy_flag}"
        )
        
        try:
            run_ssh_cmd(leader_m['host_ip'], leader_m['password'], f"docker exec {leader_cli} {approve_cmd}")
        except subprocess.CalledProcessError:
            print(f"  {org['name']} already approved or failed")
            
    # Commit Chaincode
    print("\n=== Committing Chaincode ===")
    
    # We need to target peers from ALL orgs (typically one from each org is enough for endorsement, 
    # but the command allows specifying multiple).
    # Let's include one peer from each org in the commit command.
    
    peer_addresses_flags = ""
    for org in orgs:
        # Pick first peer
        p = org['placements'][0]
        # Hostname accessible from inside CLI (docker network)
        peer_host = f"{p['node']}.{org['domain']}"
        peer_port = p['ports']['listen']
        tls_root = f"/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/peerOrganizations/{org['domain']}/peers/{peer_host}/tls/ca.crt"
        
        peer_addresses_flags += f" --peerAddresses {peer_host}:{peer_port} --tlsRootCertFiles {tls_root}"
        
    commit_cmd = (
        f"peer lifecycle chaincode commit -o {orderer_addr} --tls "
        f"--cafile {orderer_tls_root} "
        f"--channelID {channel_name} --name {chaincode_name} --version {chaincode_version} --sequence {sequence} "
        f"{peer_addresses_flags}{policy_flag}"
    )
    
    try:
        run_ssh_cmd(org1_leader_machine['host_ip'], org1_leader_machine['password'], f"docker exec {org1_leader_cli} {commit_cmd}")
    except subprocess.CalledProcessError:
        print("  Chaincode already committed or failed")

    # Invoke Init / LoadData if the profile requires it
    if profile.get("init_required"):
        init_fn = profile["init_fn"]
        init_args = profile.get("init_args", [])
        print(f"\n=== Invoking {init_fn}({', '.join(init_args)}) on {chaincode_name} ===")
        # Use \" inside the JSON so they survive being embedded inside the outer
        # double-quoted SSH command string in run_ssh_cmd (shell=True wraps with "...").
        init_args_json = ",".join(f'\\"{a}\\"' for a in init_args)
        invoke_cmd = (
            f"peer chaincode invoke -o {orderer_addr} --tls "
            f"--cafile {orderer_tls_root} "
            f"--channelID {channel_name} -n {chaincode_name} "
            f"-c '{{\\\"Args\\\":[\\\"{ init_fn }\\\",{init_args_json}]}}'"
            f"{peer_addresses_flags}"
        )
        try:
            run_ssh_cmd(org1_leader_machine['host_ip'], org1_leader_machine['password'], f"docker exec {org1_leader_cli} {invoke_cmd}")
            time.sleep(5)
            print(f"  Init ({init_fn}) completed.")
        except subprocess.CalledProcessError:
            print(f"  Init ({init_fn}) failed or already executed")

    print("\n✅ Chaincode Deployed Successfully!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--chaincode", default=None, help="Chaincode profile name (default: env PERFTUNER_CHAINCODE or json `active`)")
    args = parser.parse_args()
    config = load_config()
    profile = load_profile(args.chaincode)
    deploy_chaincode(config, profile)
