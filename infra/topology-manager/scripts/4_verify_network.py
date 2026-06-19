
import subprocess
import json
from pathlib import Path

def verify_network():
    # Load config to get IPs
    BASE_DIR = Path(__file__).resolve().parent.parent
    with open(BASE_DIR / "config/network_config.json", 'r') as f:
        config = json.load(f)
    
    machines = config['machines']
    
    for machine in machines:
        ip = machine['host_ip']
        user = machine.get('user', 'root')
        pw = machine['password']
        print(f"\n=== Checking Machine: {ip} ({machine['name']}) ===")

        # Check all containers including exited ones
        cmd = "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
        full_cmd = f"ssh -o StrictHostKeyChecking=no {user}@{ip} \"{cmd}\""
        
        try:
            result = subprocess.run(full_cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print(result.stdout.decode())
        except subprocess.CalledProcessError as e:
            print(f"Failed to check machine {ip}: {e}")

if __name__ == "__main__":
    verify_network()
