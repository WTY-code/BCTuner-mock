
import argparse
import subprocess
import os
from pathlib import Path

from _profile import load_profile

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
ANSIBLE_DIR = BASE_DIR / "ansible"
PLAYBOOK_PATH = ANSIBLE_DIR / "playbooks/site.yaml"

def deploy_network(profile):
    print(f"Starting Fabric Deployment via Ansible (chaincode profile: {profile['_key']}, src_dir: {profile['src_dir']})...")

    # Pass the active chaincode src_dir to ansible so the fallback copy task
    # picks up the right subdir (e.g., chaincode/smallbank instead of chaincode/).
    cmd = [
        "ansible-playbook",
        str(PLAYBOOK_PATH),
        "-e", f"chaincode_src_dir={profile['src_dir']}",
    ]

    try:
        subprocess.run(cmd, cwd=ANSIBLE_DIR, check=True)
        print("\n✅ Deployment Successful!")
        print("Network is up and running on all configured machines.")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Deployment Failed with exit code {e.returncode}")
        exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--chaincode", default=None, help="Chaincode profile name (default: env PERFTUNER_CHAINCODE or json `active`)")
    args = parser.parse_args()
    profile = load_profile(args.chaincode)
    deploy_network(profile)
