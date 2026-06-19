
import subprocess
import json
import time
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config/enhanced_network_config.json"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
CHANNEL_ARTIFACTS_DIR = ARTIFACTS_DIR / "channel-artifacts"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def run_ssh_cmd(ip, user, password, cmd):
    ssh_cmd = f"ssh -o StrictHostKeyChecking=no {user}@{ip} \"{cmd}\""
    subprocess.run(ssh_cmd, shell=True, check=True)

def copy_artifacts(machines):
    print("Syncing channel artifacts to all machines...")
    for machine in machines:
        ip = machine['host_ip']
        user = machine.get('user', 'root')
        pw = machine['password']
        remote_base = machine.get('remote_base_dir', '/root/fabric_deployment')
        cmd = f"scp -o StrictHostKeyChecking=no -r {CHANNEL_ARTIFACTS_DIR}/* {user}@{ip}:{remote_base}/channel-artifacts/"
        try:
            subprocess.run(cmd, shell=True, check=True)
            print(f"  Synced to {machine['name']} ({ip})")
        except subprocess.CalledProcessError as e:
            print(f"  Failed to sync to {machine['name']}: {e}")

def get_machine_by_name(machines, name):
    for m in machines:
        if m['name'] == name:
            return m
    return None

def join_orderers(config):
    print("\n=== Joining Orderers to Channel ===")
    channel_name = config['channel']['name']
    
    # Iterate over orderer placements
    orderer_cfg = config['topology']['orderers']
    domain = orderer_cfg['domain']
    
    for placement in orderer_cfg['placements']:
        node_name = placement['node'] # e.g. orderer0
        full_name = f"{node_name}.{domain}"
        machine_name = placement['machine']
        machine = get_machine_by_name(config['machines'], machine_name)
        
        if not machine:
            print(f"Error: Machine {machine_name} not found for {full_name}")
            continue
            
        ip = machine['host_ip']
        user = machine.get('user', 'root')
        pw = machine['password']
        port = placement['ports']['admin'] # Use admin port for osnadmin
        
        print(f"Joining {full_name} on {machine_name} ({ip}:{port})...")
        
        # Find a valid CLI container on this machine to run osnadmin
        # We can try to find any peer assigned to this machine
        cli_container = None
        
        # Check peers
        for org in config['topology']['orgs']:
            for p in org['placements']:
                if p['machine'] == machine_name:
                    # Found a peer on this machine
                    peer_full_name = f"{p['node']}.{org['domain']}"
                    cli_container = f"cli-{peer_full_name}"
                    break
            if cli_container: break
            
        if not cli_container:
            # Fallback: Run from the first available machine that HAS a CLI
            fallback_machine = None
            for m in config['machines']:
                for org in config['topology']['orgs']:
                    for p in org['placements']:
                        if p['machine'] == m['name']:
                            cli_container = f"cli-{p['node']}.{org['domain']}"
                            fallback_machine = m
                            break
                    if cli_container: break
                if cli_container: break
            
            if not cli_container or not fallback_machine:
                print("Error: Could not find any CLI container on any machine to run osnadmin.")
                continue
            
            # Update target IP/Machine for SSH command to be the fallback machine
            ip = fallback_machine['host_ip']
            user = fallback_machine.get('user', 'root')
            pw = fallback_machine['password']
            target_address = f"{full_name}:{port}"
        else:
            # Local execution
            target_address = f"{full_name}:{port}" # Inside docker network, hostname resolves

        # Construct command
        crypto_base = "/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto"
        channel_artifacts = "/opt/gopath/src/github.com/hyperledger/fabric/peer/channel-artifacts"
        
        cmd = (
            f"osnadmin channel join --channelID {channel_name} "
            f"--config-block {channel_artifacts}/genesis.block "
            f"-o {target_address} "
            f"--ca-file {crypto_base}/ordererOrganizations/{domain}/tlsca/tlsca.{domain}-cert.pem "
            f"--client-cert {crypto_base}/ordererOrganizations/{domain}/orderers/{full_name}/tls/server.crt "
            f"--client-key {crypto_base}/ordererOrganizations/{domain}/orderers/{full_name}/tls/server.key"
        )
        
        full_cmd = f"docker exec {cli_container} {cmd}"
        try:
            run_ssh_cmd(ip, user, pw,full_cmd)
            print(f"  Successfully joined {full_name}")
        except subprocess.CalledProcessError:
            print(f"  Failed to join {full_name} (might already be joined)")

def join_peers(config):
    print("\n=== Joining Peers to Channel ===")
    
    for org in config['topology']['orgs']:
        org_domain = org['domain']
        for placement in org['placements']:
            node_name = placement['node']
            full_name = f"{node_name}.{org_domain}"
            machine_name = placement['machine']
            machine = get_machine_by_name(config['machines'], machine_name)
            
            if not machine:
                print(f"Error: Machine {machine_name} not found for {full_name}")
                continue
                
            ip = machine['host_ip']
            user = machine.get('user', 'root')
            pw = machine['password']
            cli_container = f"cli-{full_name}"
            
            print(f"Joining {full_name} on {machine_name}...")
            
            # Check if container is running
            status_cmd = f"docker inspect -f '{{{{.State.Running}}}}' {cli_container}"
            try:
                # We need to capture output to check if it's 'true'
                # But run_ssh_cmd doesn't return output. Let's assume if it fails, container is missing/stopped.
                # Or we can just try the exec command.
                pass
            except:
                pass

            cmd = "peer channel join -b ./channel-artifacts/genesis.block"
            full_cmd = f"docker exec {cli_container} {cmd}"
            
            try:
                run_ssh_cmd(ip, user, pw,full_cmd)
                print(f"  Successfully joined {full_name}")
            except subprocess.CalledProcessError:
                 print(f"  Failed to join {full_name} (might already be joined or container down)")

def set_anchors(config):
    print("\n=== Setting Anchor Peers ===")
    print("Skipping set anchor peers as they are already included in the Genesis Block via ConfigTx.")
    return

    # Original logic below is redundant and failing because version 0 matches current state
    # machines = config['machines']
    # ...

if __name__ == "__main__":
    config = load_config()
    copy_artifacts(config['machines'])
    
    # Wait a bit for services to be fully ready
    print("Waiting 10s for services to settle...")
    time.sleep(10)
    
    join_orderers(config)
    join_peers(config)
    set_anchors(config)
