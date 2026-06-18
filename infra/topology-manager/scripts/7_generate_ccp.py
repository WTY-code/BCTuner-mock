
import json
import yaml
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config/enhanced_network_config.json"
CRYPTO_CONFIG_DIR = BASE_DIR / "artifacts/crypto-config"
OUTPUT_DIR = BASE_DIR / "artifacts/connection-profiles"

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def generate_ccp(config):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # We will generate one CCP per Org
    orgs_cfg = config['topology']['orgs']
    orderers_cfg = config['topology']['orderers']
    
    for target_org in orgs_cfg:
        org_name = target_org['name'] # e.g. Org1
        org_domain = target_org['domain'] # e.g. org1.example.com
        
        print(f"Generating CCP for {org_name}...")
        
        ccp = {
            "name": f"fabric-network-{org_name}",
            "version": "1.0.0",
            "client": {
                "organization": org_name,
                "connection": {
                    "timeout": {
                        "peer": {
                            "endorser": "300"
                        }
                    }
                }
            },
            "organizations": {},
            "peers": {},
            "orderers": {},
            "certificateAuthorities": {}
        }
        
        # 1. Organizations Section
        # Add all known orgs
        for org in orgs_cfg:
            ccp["organizations"][org['name']] = {
                "mspid": f"{org['name']}MSP",
                "peers": [],
                "certificateAuthorities": [] # We don't really use CAs in this simple setup but required field
            }
            
            # Populate peers list for this org
            for p in org['placements']:
                peer_host = f"{p['node']}.{org['domain']}"
                ccp["organizations"][org['name']]["peers"].append(peer_host)

        # 2. Peers Section
        # Add ALL peers from ALL orgs
        for org in orgs_cfg:
            for p in org['placements']:
                peer_host = f"{p['node']}.{org['domain']}"
                port = p['ports']['listen']
                ip = p['host_ip'] # Private IP
                
                tls_cert_path = CRYPTO_CONFIG_DIR / f"peerOrganizations/{org['domain']}/peers/{peer_host}/tls/ca.crt"
                
                try:
                    with open(tls_cert_path, 'r') as f:
                        tls_cert = f.read()
                except FileNotFoundError:
                    print(f"Warning: TLS cert not found for {peer_host} at {tls_cert_path}")
                    tls_cert = ""

                ccp["peers"][peer_host] = {
                    "url": f"grpcs://{ip}:{port}", # Use IP for connection
                    "tlsCACerts": {
                        "pem": tls_cert
                    },
                    "grpcOptions": {
                        "ssl-target-name-override": peer_host,
                        "hostnameOverride": peer_host
                    }
                }

        # 3. Orderers Section
        for p in orderers_cfg['placements']:
            orderer_host = f"{p['node']}.{orderers_cfg['domain']}"
            port = p['ports']['listen']
            ip = p['host_ip']
            
            tls_cert_path = CRYPTO_CONFIG_DIR / f"ordererOrganizations/{orderers_cfg['domain']}/orderers/{orderer_host}/tls/ca.crt"
            
            try:
                with open(tls_cert_path, 'r') as f:
                    tls_cert = f.read()
            except FileNotFoundError:
                print(f"Warning: TLS cert not found for {orderer_host}")
                tls_cert = ""
                
            ccp["orderers"][orderer_host] = {
                "url": f"grpcs://{ip}:{port}",
                "tlsCACerts": {
                    "pem": tls_cert
                },
                "grpcOptions": {
                    "ssl-target-name-override": orderer_host,
                    "hostnameOverride": orderer_host
                }
            }
            
        # Save CCP
        output_path = OUTPUT_DIR / f"connection-{org_name.lower()}.yaml"
        with open(output_path, 'w') as f:
            yaml.dump(ccp, f, default_flow_style=False)
            
        print(f"  Saved to {output_path}")

if __name__ == "__main__":
    config = load_config()
    generate_ccp(config)
