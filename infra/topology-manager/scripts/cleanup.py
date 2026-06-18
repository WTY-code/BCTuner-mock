import subprocess
import json
import argparse
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config/network_config.json"
ANSIBLE_DIR = BASE_DIR / "ansible"
INVENTORY_PATH = ANSIBLE_DIR / "inventory/hosts.ini"
ARTIFACTS_DIR = BASE_DIR / "artifacts"


def regenerate_inventory():
    """Regenerate ansible inventory from the CURRENT network_config.json.

    This ensures cleanup targets the right machines even after switching configs.
    """
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)

    machines = config['machines']
    network_type = config.get('network_type', 'private')

    ANSIBLE_DIR.mkdir(parents=True, exist_ok=True)
    (ANSIBLE_DIR / "inventory").mkdir(parents=True, exist_ok=True)
    (ANSIBLE_DIR / "playbooks").mkdir(parents=True, exist_ok=True)

    with open(INVENTORY_PATH, 'w') as f:
        f.write("[fabric_nodes]\n")
        for m in machines:
            ip = m['public_ip'] if network_type == 'public' else m['private_ip']
            f.write(f"{m['name']} ansible_host={ip} ansible_user={m['user']} ansible_password={m['password']}\n")

    print(f"Inventory regenerated from current network_config.json ({len(machines)} machines)")


def run_cleanup(purge_cache=False):
    # Always regenerate inventory from current config so cleanup hits ALL machines
    regenerate_inventory()

    print("Cleaning up Fabric network on all nodes...")

    playbook_path = ANSIBLE_DIR / "playbooks/cleanup.yaml"

    cmd = ["ansible-playbook", "-i", str(INVENTORY_PATH), str(playbook_path)]
    if purge_cache:
        # Tell the Ansible playbook to purge remote caches
        cmd.extend(["-e", "purge_cache=true"])
        print("Purging remote caches (crypto-config + channel-artifacts) on all machines...")

        # Purge all local artifacts
        dirs_to_purge = [
            ARTIFACTS_DIR / "crypto-config",
            ARTIFACTS_DIR / "connection-profiles",
            ARTIFACTS_DIR / "channel-artifacts",
        ]
        files_to_purge = [
            ARTIFACTS_DIR / "configtx.yaml",
            ARTIFACTS_DIR / "crypto-config.yaml",
            BASE_DIR / "config/enhanced_network_config.json",
        ]
        compose_pattern = list(ARTIFACTS_DIR.glob("docker-compose-*.yaml"))

        for d in dirs_to_purge:
            if d.exists():
                print(f"  Purging: {d}")
                shutil.rmtree(d)
        for f in files_to_purge:
            if f.exists():
                print(f"  Purging: {f}")
                f.unlink()
        for f in compose_pattern:
            print(f"  Purging: {f}")
            f.unlink()

    subprocess.run(cmd, cwd=str(ANSIBLE_DIR), check=True)
    print("Cleanup completed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--purge-cache", action="store_true",
                        help="Purge all local and remote cached artifacts (crypto, channel-artifacts, etc.)")
    args = parser.parse_args()

    run_cleanup(purge_cache=args.purge_cache)
