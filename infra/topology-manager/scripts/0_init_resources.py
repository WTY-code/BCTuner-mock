import argparse
import subprocess
import json
import os
import sys
import time
from pathlib import Path

from _profile import load_profile

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config/network_config.json"
REMOTE_RESOURCE_DIR = "/root/fabric_resources/chaincode"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def _ensure_stdout_blocking():
    """Restore stdout to blocking mode.

    subprocess.run(..., shell=True) with ssh can set O_NONBLOCK on inherited
    file descriptors, which later causes Ansible to refuse to run.
    """
    import fcntl
    try:
        fd = sys.stdout.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if flags & os.O_NONBLOCK:
            fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
    except Exception:
        pass


def run_ssh_cmd(ip, password, cmd, retries=5):
    ssh_cmd = f"ssh -o StrictHostKeyChecking=no root@{ip} \"{cmd}\""
    for attempt in range(retries):
        try:
            subprocess.run(ssh_cmd, shell=True, check=True)
            _ensure_stdout_blocking()
            return
        except subprocess.CalledProcessError as e:
            print(f"SSH command failed (attempt {attempt + 1}/{retries}): {e}")
            _ensure_stdout_blocking()
            if attempt < retries - 1:
                time.sleep(5)
            else:
                raise

def init_resources(config, profile, force=False):
    print(f"Initializing persistent resources on remote machines (chaincode profile: {profile['_key']})...")
    local_chaincode_dir = Path(profile["local_src_dir"])
    machines = config['machines']
    network_type = config.get('network_type', 'private')

    for machine in machines:
        ip = machine['public_ip'] if network_type == 'public' else machine['private_ip']
        pw = machine['password']
        name = machine['name']

        print(f"\n=== Processing {name} ({ip}) ===")

        # Add delay between processing machines to avoid SSH rate limiting
        time.sleep(1)

        # 1. Create remote resource directory
        print(f"Creating remote directory: {REMOTE_RESOURCE_DIR}")
        run_ssh_cmd(ip, pw, f"mkdir -p {REMOTE_RESOURCE_DIR}")

        # Add delay
        time.sleep(1)

        # 2. Check if the correct chaincode profile is cached (marker file)
        marker_path = f"{REMOTE_RESOURCE_DIR}/.profile"
        check_cmd = f"[ -f {marker_path} ] && cat {marker_path} || echo 'missing'"

        # Retry logic for check command
        for attempt in range(3):
            result = subprocess.run(
                f"ssh -o StrictHostKeyChecking=no root@{ip} \"{check_cmd}\"",
                shell=True, capture_output=True, text=True
            )
            if result.returncode == 0:
                break
            print(f"SSH check failed (attempt {attempt + 1}/3). Retrying...")
            time.sleep(2)

        cached_profile = result.stdout.strip()
        if not force and cached_profile == profile["_key"]:
            print(f"Chaincode profile '{profile['_key']}' already cached on {name}. Skipping copy.")
        else:
            if cached_profile != "missing":
                print(f"Profile changed ({cached_profile} -> {profile['_key']}), refreshing cache on {name}...")
                # Clean old cache
                run_ssh_cmd(ip, pw, f"rm -rf {REMOTE_RESOURCE_DIR}/* {REMOTE_RESOURCE_DIR}/.* 2>/dev/null; mkdir -p {REMOTE_RESOURCE_DIR}")
            print(f"Copying chaincode (including vendor) to {name}...")
            cmd = f"scp -o StrictHostKeyChecking=no -r {local_chaincode_dir}/* root@{ip}:{REMOTE_RESOURCE_DIR}/"

            for attempt in range(3):
                try:
                    subprocess.run(cmd, shell=True, check=True)
                    print(f"Successfully copied chaincode resources to {name}")
                    break
                except subprocess.CalledProcessError as e:
                    print(f"Failed to copy resources to {name} (attempt {attempt + 1}/3): {e}")
                    if attempt < 2:
                        time.sleep(3)
                    else:
                        raise e
            # Write profile marker
            run_ssh_cmd(ip, pw, f"echo '{profile['_key']}' > {marker_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--chaincode", default=None, help="Chaincode profile name (default: env PERFTUNER_CHAINCODE or json `active`)")
    parser.add_argument("--force", action="store_true", help="Force re-copy even if profile marker matches (use after adding vendor/)")
    args = parser.parse_args()
    config = load_config()
    profile = load_profile(args.chaincode)
    init_resources(config, profile, force=args.force)
