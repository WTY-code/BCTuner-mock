
import json
import jinja2
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config/enhanced_network_config.json"
TEMPLATES_DIR = BASE_DIR / "templates"
ARTIFACTS_DIR = BASE_DIR / "artifacts"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def build_extra_hosts(config, current_machine_name=None):
    """
    Builds a list of "hostname:ip" strings for all nodes in the network.
    Uses the assigned host_ip (which resolves to public or private IP based on network_type).
    """
    extra_hosts = []
    
    # Add Orderers
    for p in config['topology']['orderers']['placements']:
        # Format: "hostname.domain:ip"
        hostname = f"{p['node']}.{config['topology']['orderers']['domain']}"
        ip = p['host_ip'] 
        extra_hosts.append(f"{hostname}:{ip}")
        
    # Add Peers
    for org in config['topology']['orgs']:
        for p in org['placements']:
            hostname = f"{p['node']}.{org['domain']}"
            ip = p['host_ip'] 
            extra_hosts.append(f"{hostname}:{ip}")
            
    return extra_hosts

def generate_compose_files(config):
    print("Generating docker-compose files...")
    
    # Load Defaults
    defaults_file = BASE_DIR / "config/defaults.json"
    final_params = {}
    
    if defaults_file.exists():
        try:
            with open(defaults_file, 'r') as f:
                final_params = json.load(f)
            print(f"Loaded defaults from {defaults_file}")
        except Exception as e:
            print(f"Failed to load defaults: {e}")

    # Load Tuning Parameters if available
    tuning_params = {}
    tuning_file = BASE_DIR / "config/tuning_params.json"
    if tuning_file.exists():
        try:
            with open(tuning_file, 'r') as f:
                tuning_params = json.load(f)
            print(f"Loaded tuning parameters: {tuning_params}")
            # Merge: tuning overrides defaults
            final_params.update(tuning_params)
        except Exception as e:
            print(f"Failed to load tuning parameters: {e}")
            
    peer_env_overrides = {k: v for k, v in final_params.items() if k.startswith("CORE_")}
    orderer_env_overrides = {k: v for k, v in final_params.items() if k.startswith("ORDERER_")}
    
    template_loader = jinja2.FileSystemLoader(searchpath=TEMPLATES_DIR)
    template_env = jinja2.Environment(loader=template_loader)
    template = template_env.get_template("docker-compose-peer.yaml.j2")
    
    # Iterate over each physical machine defined in config
    for machine in config['machines']:
        machine_name = machine['name']
        print(f"  Processing {machine_name}...")
        
        # Build machine-specific extra_hosts list (identical for all if using private IPs)
        extra_hosts_list = build_extra_hosts(config, current_machine_name=machine_name)
        
        # Filter nodes assigned to THIS machine
        local_orderers = []
        for p in config['topology']['orderers']['placements']:
            if p['machine'] == machine_name:
                # Add full domain to node name for template convenience
                p['full_hostname'] = f"{p['node']}.{config['topology']['orderers']['domain']}"
                local_orderers.append(p)
                
        local_peers = []
        for org in config['topology']['orgs']:
            for p in org['placements']:
                if p['machine'] == machine_name:
                    p['full_hostname'] = f"{p['node']}.{org['domain']}"
                    p['org_msp'] = f"{org['name']}MSP" # e.g. Org1MSP
                    p['org_domain'] = org['domain']
                    local_peers.append(p)
        
        # Render template
        output_text = template.render(
            machine_name=machine_name,
            orderers=local_orderers,
            peers=local_peers,
            extra_hosts=extra_hosts_list,
            peer_env_overrides=peer_env_overrides,
            orderer_env_overrides=orderer_env_overrides
        )
        
        output_filename = f"docker-compose-{machine_name}.yaml"
        output_path = ARTIFACTS_DIR / output_filename
        with open(output_path, 'w') as f:
            f.write(output_text)
            
        print(f"    Saved to {output_path}")

if __name__ == "__main__":
    config = load_config()
    generate_compose_files(config)
