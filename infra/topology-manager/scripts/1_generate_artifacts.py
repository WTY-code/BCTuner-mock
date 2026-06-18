
import json
import os
import subprocess
import jinja2
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config/network_config.json"
TEMPLATES_DIR = BASE_DIR / "templates"
ARTIFACTS_DIR = BASE_DIR / "artifacts"
ANSIBLE_DIR = BASE_DIR / "ansible"

# Ensure directories exist
ARTIFACTS_DIR.mkdir(exist_ok=True)
(ARTIFACTS_DIR / "crypto-config").mkdir(exist_ok=True)
(ARTIFACTS_DIR / "channel-artifacts").mkdir(exist_ok=True)
(ANSIBLE_DIR / "inventory").mkdir(exist_ok=True)
(ANSIBLE_DIR / "playbooks").mkdir(exist_ok=True)

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def generate_inventory(config):
    """Generates Ansible hosts.ini"""
    print("Generating Ansible inventory...")
    machines = config['machines']
    network_type = config.get('network_type', 'private')
    inventory_path = ANSIBLE_DIR / "inventory/hosts.ini"
    
    with open(inventory_path, 'w') as f:
        f.write("[fabric_nodes]\n")
        for m in machines:
            ip_to_use = m['public_ip'] if network_type == 'public' else m['private_ip']
            f.write(f"{m['name']} ansible_host={ip_to_use} ansible_user={m['user']} ansible_password={m['password']}\n")
            
    print(f"Inventory saved to {inventory_path}")

def assign_ports_and_hosts(config):
    """
    Logically assigns ports to each node and creates a mapping of node -> machine IP.
    This modifies the config object in-memory to include port info.
    """
    print("Assigning ports and hosts...")
    network_type = config.get('network_type', 'private')
    
    # Track ports usage per machine to avoid collision
    # Structure: machine_name -> { "orderer": next_port, "peer": next_port }
    machine_ports = {}
    for m in config['machines']:
        m['host_ip'] = m['public_ip'] if network_type == 'public' else m['private_ip']
        machine_ports[m['name']] = {
            "orderer_listen": 7050,
            "orderer_admin": 7055,
            "orderer_ops": 9443,
            "peer_listen": 7051,
            "peer_cc": 7052,
            "peer_ops": 9643  # Changed from 9443 to 9643 to avoid collision
        }

    # 1. Assign Orderer Ports
    orderer_cfg = config['topology']['orderers']
    for placement in orderer_cfg['placements']:
        node_name = placement['node']
        machine_name = placement['machine']
        
        # Get machine IP (private IP is preferred for internal communication)
        machine_info = next(m for m in config['machines'] if m['name'] == machine_name)
        ip_to_use = machine_info['public_ip'] if network_type == 'public' else machine_info['private_ip']
        placement['host_ip'] = ip_to_use
        placement['public_ip'] = machine_info['public_ip'] # Used for local mapping if needed
        placement['private_ip'] = machine_info['private_ip']
        
        # Assign Ports
        ports = machine_ports[machine_name]
        placement['ports'] = {
            "listen": ports['orderer_listen'],
            "admin": ports['orderer_admin'],
            "operations": ports['orderer_ops']
        }
        
        # Increment ports for next orderer on this machine
        ports['orderer_listen'] += 1000  # 7050, 8050, 9050...
        ports['orderer_admin'] += 1000
        ports['orderer_ops'] += 10

    # 2. Assign Peer Ports
    for org in config['topology']['orgs']:
        for placement in org['placements']:
            node_name = placement['node']
            machine_name = placement['machine']
            
            machine_info = next(m for m in config['machines'] if m['name'] == machine_name)
            ip_to_use = machine_info['public_ip'] if network_type == 'public' else machine_info['private_ip']
            placement['host_ip'] = ip_to_use
            placement['public_ip'] = machine_info['public_ip']
            placement['private_ip'] = machine_info['private_ip']
            
            ports = machine_ports[machine_name]
            placement['ports'] = {
                "listen": ports['peer_listen'],
                "chaincode": ports['peer_cc'],
                "operations": ports['peer_ops']
            }
            
            # Increment ports
            ports['peer_listen'] += 1000 # 7051, 8051...
            ports['peer_cc'] += 1000
            ports['peer_ops'] += 10

    return config

import shutil

def generate_crypto(config):
    """Generates crypto material using cryptogen"""
    print("Generating crypto material...")
    
    # Check if crypto-config already exists and is not empty
    crypto_dir = ARTIFACTS_DIR / "crypto-config"
    if crypto_dir.exists() and any(crypto_dir.iterdir()):
        print(f"  Using cached crypto-config at {crypto_dir}")
        return
        
    # Clean up previous crypto-config specifically, not the whole artifacts dir
    if crypto_dir.exists():
        shutil.rmtree(crypto_dir)
        
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    (ARTIFACTS_DIR / "channel-artifacts").mkdir(exist_ok=True)
    
    # Generate crypto-config.yaml
    # We need to know the COUNT of orderers and peers per org to set 'Template.Count'
    # Actually, cryptogen uses 'Template.Count' to generate N peers sequentially (peer0, peer1...).
    # Our config.json defines explicit counts.
    # However, 'org.peers' in the template context comes from 'config['topology']['orgs']'.
    # In 'assign_ports_and_hosts', we didn't add a 'peers' count field to the org dict.
    # We only have 'placements' list.
    
    # We need to calculate the peer count for each org.
    for org in config['topology']['orgs']:
        if 'peers' not in org:
            org['peers'] = len(org['placements'])
            
    # Also for orderers
    if 'count' not in config['topology']['orderers']:
        config['topology']['orderers']['count'] = len(config['topology']['orderers']['placements'])
    
    template_loader = jinja2.FileSystemLoader(searchpath=TEMPLATES_DIR)
    template_env = jinja2.Environment(loader=template_loader)
    template = template_env.get_template("crypto-config.yaml.j2")
    
    # Prepare orderer names and domain
    orderer_cfg = config['topology']['orderers']
    orderer_names = [p['node'] for p in orderer_cfg['placements']]
    orderer_domain = orderer_cfg['domain']

    # Render
    output_text = template.render(
        orderer_count=len(orderer_names),
        orderer_names=orderer_names,
        orderer_domain=orderer_domain,
        orgs=config['topology']['orgs']
    )
    
    output_path = ARTIFACTS_DIR / "crypto-config.yaml"
    with open(output_path, 'w') as f:
        f.write(output_text)
    
    print(f"crypto-config.yaml saved to {output_path}")
    
    # Run cryptogen using Docker
    # Mount ARTIFACTS_DIR to /data
    cmd = (
        f"docker run --rm -v {ARTIFACTS_DIR}:/data "
        f"hyperledger/fabric-tools:3.0.0-preview "
        f"cryptogen generate --config=/data/crypto-config.yaml --output=/data/crypto-config"
    )
    print(f"Running: {cmd}")
    subprocess.run(cmd, shell=True, check=True)
    print("Certificates generated successfully.")
    
    # RENAME KEYSTORE FILES
    # Iterate through all orgs and peers to rename the private key in 'keystore' to 'priv_sk'
    print("Renaming keystore files...")
    
    crypto_dir = ARTIFACTS_DIR / "crypto-config"
    
    for root, dirs, files in os.walk(crypto_dir):
        if "keystore" in root:
            # Check if priv_sk already exists
            if "priv_sk" in files:
                continue

            # Look for *_sk file
            sk_files = [f for f in files if f.endswith("_sk")]
            
            if not sk_files:
                print(f"Warning: No private key found in {root}")
                continue
                
            # If multiple sk files (unlikely), pick the first one
            sk_file = sk_files[0]
            old_path = os.path.join(root, sk_file)
            new_path = os.path.join(root, "priv_sk")
            
            try:
                os.rename(old_path, new_path)
                print(f"Renamed {sk_file} to priv_sk in {root}")
            except Exception as e:
                print(f"Error renaming {sk_file} in {root}: {e}")

def generate_configtx(config):
    """Generates configtx.yaml and genesis block"""
    print("Generating configtx.yaml...")
    
    # Load Tuning Parameters if available
    tuning_params = {}
    tuning_file = BASE_DIR / "config/tuning_params.json"
    if tuning_file.exists():
        try:
            with open(tuning_file, 'r') as f:
                tuning_params = json.load(f)
            print(f"Loaded tuning parameters: {tuning_params}")
        except Exception as e:
            print(f"Failed to load tuning parameters: {e}")
            
    template_loader = jinja2.FileSystemLoader(searchpath=TEMPLATES_DIR)
    template_env = jinja2.Environment(loader=template_loader)
    template = template_env.get_template("configtx.yaml.j2")
    
    # We need to construct the list of Orderer Addresses for configtx
    # format: "host:port"
    orderer_addresses = []
    for placement in config['topology']['orderers']['placements']:
        # Use domain name + port
        # NOTE: Configtx expects hostnames that certificates are issued for.
        # cryptogen creates "ordererX.example.com". 
        # We must ensure our ports match what we assigned.
        addr = f"{placement['node']}.{config['topology']['orderers']['domain']}:{placement['ports']['listen']}"
        orderer_addresses.append(addr)
        
    output_text = template.render(
        profile_name=config['channel']['profile'],
        orderer_addresses=orderer_addresses,
        orgs=config['topology']['orgs'],
        orderer_domain=config['topology']['orderers']['domain'],
        # Pass tuning parameters
        batch_timeout=tuning_params.get("BatchTimeout", "2s"),
        max_message_count=tuning_params.get("MaxMessageCount", 10),
        absolute_max_bytes=tuning_params.get("AbsoluteMaxBytes", "99 MB"),
        preferred_max_bytes=tuning_params.get("PreferredMaxBytes", "512 KB")
    )
    
    output_path = ARTIFACTS_DIR / "configtx.yaml"
    with open(output_path, 'w') as f:
        f.write(output_text)
        
    print(f"configtx.yaml saved to {output_path}")
    
    # Run configtxgen for genesis block using Docker
    # Mount ARTIFACTS_DIR to /data
    # FABRIC_CFG_PATH inside container should point to /data where configtx.yaml is
    cmd = (
        f"docker run --rm -v {ARTIFACTS_DIR}:/data "
        f"-e FABRIC_CFG_PATH=/data "
        f"hyperledger/fabric-tools:3.0.0-preview "
        f"configtxgen -profile {config['channel']['profile']} -channelID {config['channel']['name']} -outputBlock /data/channel-artifacts/genesis.block"
    )
    print(f"Running: {cmd}")
    subprocess.run(cmd, shell=True, check=True)
    print("Genesis block generated successfully.")
    
    # Generate Anchor Peer Transactions
    for org in config['topology']['orgs']:
        org_name = org['name']
        # Assume MSP ID is OrgName + MSP (e.g., Org1MSP) - matching configtx.yaml.j2
        msp_id = f"{org_name}MSP"
        anchor_tx_file = f"{msp_id}anchors.tx"
        
        print(f"Generating anchor peer update for {org_name} ({msp_id})...")
        cmd = (
            f"docker run --rm -v {ARTIFACTS_DIR}:/data "
            f"-e FABRIC_CFG_PATH=/data "
            f"hyperledger/fabric-tools:3.0.0-preview "
            f"configtxgen -profile {config['channel']['profile']} -outputAnchorPeersUpdate /data/channel-artifacts/{anchor_tx_file} -channelID {config['channel']['name']} -asOrg {msp_id}"
        )
        subprocess.run(cmd, shell=True, check=True)
        
        # Create helper script to set anchor peer
        script_name = f"set{org_name}Anchor.sh"
        script_path = ARTIFACTS_DIR / "channel-artifacts" / script_name
        
        # We need a valid orderer to submit the update. Use orderer0 for simplicity.
        # Ensure the path to CA file is correct inside the CLI container.
        ca_file = "/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/ordererOrganizations/example.com/orderers/orderer0.example.com/msp/tlscacerts/tlsca.example.com-cert.pem"
        
        with open(script_path, 'w') as f:
            f.write("#!/bin/bash\n")
            f.write(f"peer channel update -o orderer0.example.com:7050 -c {config['channel']['name']} -f ./channel-artifacts/{anchor_tx_file} --tls --cafile {ca_file}\n")
        
        os.chmod(script_path, 0o755)
        print(f"Generated {script_name}")

def main():
    config = load_config()
    
    # 1. Enhance config with logic
    config = assign_ports_and_hosts(config)
    
    # 2. Generate Ansible Inventory
    generate_inventory(config)
    
    # 3. Generate Crypto
    generate_crypto(config)
    
    # 4. Generate Channel Artifacts
    generate_configtx(config)
    
    # Save the enhanced config (with ports) for the next script to use
    with open(BASE_DIR / "config/enhanced_network_config.json", 'w') as f:
        json.dump(config, f, indent=2)

if __name__ == "__main__":
    main()
