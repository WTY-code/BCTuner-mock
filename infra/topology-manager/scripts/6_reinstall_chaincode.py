
import argparse
import subprocess
import json
from pathlib import Path

from _profile import load_profile

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config/network_config.json"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def run_ssh_cmd(ip, user, password, cmd):
    ssh_cmd = f"ssh -o StrictHostKeyChecking=no {user}@{ip} \"{cmd}\""
    subprocess.run(ssh_cmd, shell=True, check=True)

def reinstall_chaincode(config, profile):
    chaincode_name = profile["name"]
    print(f"Re-installing chaincode '{chaincode_name}' (profile: {profile['_key']}) on all peers...")
    machines = config['machines']
    machine1 = machines[0]
    machine2 = machines[1]

    # Path inside container where package is located (synced previously)
    pkg_path = f"/opt/gopath/src/github.com/hyperledger/fabric/peer/channel-artifacts/{chaincode_name}.tar.gz"
    install_cmd = f"peer lifecycle chaincode install {pkg_path}"
    
    peers = [
        {"machine": machine1, "cli": "cli-peer0.org1.example.com"},
        {"machine": machine1, "cli": "cli-peer1.org1.example.com"},
        {"machine": machine2, "cli": "cli-peer0.org2.example.com"},
        {"machine": machine2, "cli": "cli-peer1.org2.example.com"},
    ]
    
    for p in peers:
        print(f"Installing on {p['cli']}...")
        try:
            run_ssh_cmd(p['machine']['host_ip'], p['machine'].get('user', 'root'), p['machine']['password'], f"docker exec {p['cli']} {install_cmd}")
            print(f"  Success: {p['cli']}")
        except subprocess.CalledProcessError as e:
            print(f"  Failed on {p['cli']}: {e}")

    # Verify installation
    print("\nVerifying installation...")
    for p in peers:
        print(f"Checking {p['cli']}...")
        try:
            run_ssh_cmd(p['machine']['host_ip'], p['machine'].get('user', 'root'), p['machine']['password'], f"docker exec {p['cli']} peer lifecycle chaincode queryinstalled")
        except:
            pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--chaincode", default=None, help="Chaincode profile name (default: env PERFTUNER_CHAINCODE or json `active`)")
    args = parser.parse_args()
    config = load_config()
    profile = load_profile(args.chaincode)
    reinstall_chaincode(config, profile)
