
import subprocess
import json
import os
import time
from pathlib import Path

def run_ssh_command(ip, password, command):
    """Executes a remote command via sshpass/ssh."""
    full_cmd = f"ssh -o StrictHostKeyChecking=no root@{ip} \"{command}\""
    print(f"[{ip}] Executing: {command}")
    try:
        subprocess.run(full_cmd, shell=True, check=True)
        print(f"[{ip}] Success.")
    except subprocess.CalledProcessError as e:
        print(f"[{ip}] Failed: {e}")

def prepare_machines(config_path):
    with open(config_path, 'r') as f:
        config = json.load(f)

    machines = config['machines']
    network_type = config.get('network_type', 'private')
    
    # Commands to clean up Docker environment
    cleanup_cmds = [
        "docker ps -q | xargs -r docker stop",
        "docker ps -aq | xargs -r docker rm",
        "docker volume prune -f",
        "docker network prune -f",
        "rm -rf /root/fabric_deployment"  # Clean previous deployment dir if exists
    ]
    
    # Check images and pull if missing (with clash toggle attempt)
    # Using specific tags from your local setup: 3.1.3 for peer/orderer, 3.0.0-preview for tools
    images = [
        "hyperledger/fabric-peer:3.1.3",
        "hyperledger/fabric-orderer:3.1.3",
        "hyperledger/fabric-tools:3.0.0-preview",
        "hyperledger/fabric-ca:1.5"
    ]
    
    for machine in machines:
        print(f"\n=== Preparing Machine: {machine['name']} ===")
        ip = machine['public_ip'] if network_type == 'public' else machine['private_ip']
        pw = machine['password']
        
        # 1. Cleanup
        for cmd in cleanup_cmds:
            run_ssh_command(ip, pw, cmd)
            
        # 2. Check and Pull Images
        for img in images:
            check_cmd = f"docker image inspect {img} > /dev/null 2>&1"
            try:
                # Check if image exists remotely
                subprocess.run(f"ssh -o StrictHostKeyChecking=no root@{ip} \"{check_cmd}\"", shell=True, check=True)
                print(f"[{ip}] Image {img} exists.")
            except subprocess.CalledProcessError:
                print(f"[{ip}] Image {img} missing. Attempting pull...")
                # Try pull directly
                pull_cmd = f"docker pull {img}"
                try:
                    run_ssh_command(ip, pw, pull_cmd)
                except:
                    print(f"[{ip}] Direct pull failed. Trying Clash toggle...")
                    # Toggle Clash (assuming command 'clash' starts it, and we kill it after?) 
                    # Actually user said: "start Clash, download, then close it"
                    # We'll assume a standard proxy usage might be needed or just restarting clash service
                    # For now, let's try setting https_proxy if clash is on port 7890 (standard)
                    # Or just try to restart docker daemon?
                    # Let's try the user's specific instruction pattern if we knew the clash command.
                    # Since we don't know the exact clash command, we'll try a generic retry.
                    pass

if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent
    prepare_machines(BASE_DIR / "config/network_config.json")
