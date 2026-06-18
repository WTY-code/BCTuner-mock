#!/usr/bin/env python3
import json
import http.server
import subprocess
import tempfile
import time
import os
import re
import sys
import uuid
from pathlib import Path
from http import HTTPStatus
from typing import Any, Dict, List, Optional, Tuple, Mapping


def _run_with_output(cmd, **kwargs) -> Tuple[int, str, str]:
    """Run a subprocess, capturing stdout/stderr via real files (blocking IO).

    Ansible requires blocking IO; using subprocess.PIPE (capture_output=True)
    produces non-blocking file descriptors. Using os.open + os.fdopen creates
    a raw file descriptor with O_WRONLY|O_CREAT, which is always blocking.
    """
    tmpdir = tempfile.mkdtemp(prefix='perftuner_')
    out_path = os.path.join(tmpdir, 'stdout.log')
    err_path = os.path.join(tmpdir, 'stderr.log')
    try:
        fd_out = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        fd_err = os.open(err_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        f_out = os.fdopen(fd_out, 'w')
        f_err = os.fdopen(fd_err, 'w')
        result = subprocess.run(cmd, stdout=f_out, stderr=f_err, text=True, **kwargs)
        f_out.close()
        f_err.close()
        with open(out_path, 'r') as f:
            stdout = f.read()
        with open(err_path, 'r') as f:
            stderr = f.read()
    finally:
        for p in (out_path, err_path):
            try:
                os.unlink(p)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
    return result.returncode, stdout, stderr

BASE_DIR = Path(__file__).resolve().parent.parent / "topology-manager"
sys.path.insert(0, str(BASE_DIR / "scripts"))
from _profile import load_profile

_ACTIVE_PROFILE = load_profile()
CHAINCODE_NAME = _ACTIVE_PROFILE["name"]
CALIPER_BENCH_CONFIG = BASE_DIR.parent / "caliper-benchmarks" / _ACTIVE_PROFILE["caliper_config"]
TUNING_PARAMS_FILE = BASE_DIR / "config/tuning_params.json"
NETWORK_CONFIG_FILE = BASE_DIR / "config/network_config.json"

class Session:
    def __init__(self, configs: Dict[str, Any]):
        self.session_id = str(uuid.uuid4())
        self.configs = configs
        self.created_at = time.time()
        self.test_count = 0
        self.total_creates = 0
        self.test_results = []

class SessionManager:
    def __init__(self):
        self.active_session: Optional[Session] = None

    @property
    def has_active_session(self) -> bool:
        return self.active_session is not None

    def start_session(self, configs: Dict[str, Any]) -> Session:
        if self.active_session:
            raise RuntimeError(f"Session {self.active_session.session_id} is already active.")
        self.active_session = Session(configs)
        return self.active_session

    def get_session(self, session_id: str) -> Session:
        if not self.active_session or self.active_session.session_id != session_id:
            raise RuntimeError(f"Session {session_id} not found or not active.")
        return self.active_session

    def end_session(self, session_id: str) -> Session:
        session = self.get_session(session_id)
        self.active_session = None
        return session

    def force_end(self):
        self.active_session = None

    def record_test(self, session_id: str, result: Dict[str, Any]):
        session = self.get_session(session_id)
        session.test_count += 1
        session.test_results.append(result)

    def record_creates(self, session_id: str, count: int):
        session = self.get_session(session_id)
        session.total_creates += count

session_manager = SessionManager()

class RequestHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            self._send_json({"error": "Missing Content-Length"}, HTTPStatus.LENGTH_REQUIRED)
            return
        
        try:
            body = self.rfile.read(length).decode('utf-8')
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return

        method = payload.get("method")
        params = payload.get("params", {})

        if method == "SESSION_START":
            self._handle_session_start(params)
        elif method == "NETWORK_CONFIG":
            self._handle_network_config(params)
        elif method == "SESSION_TEST":
            self._handle_session_test(params)
        elif method == "SESSION_END":
            self._handle_session_end(params)
        elif method == "SESSION_STATUS":
            self._handle_session_status()
        elif method == "INFO":
            self._handle_info()
        else:
            self._send_json({"error": f"Method {method} not supported"}, HTTPStatus.NOT_FOUND)

    def _handle_network_config(self, params):
        topology = params.get("topology")
        if not topology:
            self._send_json({"error": "Missing 'topology' in params"}, HTTPStatus.BAD_REQUEST)
            return
            
        try:
            NETWORK_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(NETWORK_CONFIG_FILE, 'w') as f:
                json.dump(topology, f, indent=2)
            self._send_json({
                "status": "success",
                "message": "Network configuration updated successfully."
            })
        except Exception as e:
            self._send_json({"error": f"Failed to save network config: {str(e)}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_session_start(self, params):
        configs = params.get("configs", {})
        
        if session_manager.has_active_session:
             self._send_json({
                "error": f"Session {session_manager.active_session.session_id} is already active.",
                "session_id": session_manager.active_session.session_id
            }, HTTPStatus.CONFLICT)
             return

        try:
            session = session_manager.start_session(configs)
        except RuntimeError as e:
            self._send_json({"error": str(e)}, HTTPStatus.CONFLICT)
            return

        # Write configs to tuning_params.json
        with open(TUNING_PARAMS_FILE, 'w') as f:
            json.dump(configs, f, indent=2)

        # Deploy Network
        print(f"Deploying network with configs: {configs}")
        cmd = ["python3", str(BASE_DIR / "scripts/run_experiment.py"), "--deploy-only", "--chaincode", _ACTIVE_PROFILE["_key"]]
        
        start_time = time.time()
        try:
            ret, stdout, stderr = _run_with_output(cmd, cwd=str(BASE_DIR))
            duration = time.time() - start_time
            
            if ret != 0:
                session_manager.force_end()
                # Run cleanup just in case
                subprocess.run(["python3", str(BASE_DIR / "scripts/cleanup.py")], cwd=str(BASE_DIR))
                
                self._send_json({
                    "error": "Deployment failed",
                    "log": stdout + stderr,
                    "returncode": ret,
                    "time": duration
                }, HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._send_json({
                "status": "success",
                "session_id": session.session_id,
                "message": "Session started and network deployed",
                "log": stdout,
                "time": duration
            })
            
        except Exception as e:
            session_manager.force_end()
            self._send_json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_session_test(self, params):
        session_id = params.get("session_id")
        tests_content = params.get("tests")
        
        try:
            session = session_manager.get_session(session_id)
        except RuntimeError as e:
            self._send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return

        # Write test config
        if tests_content:
            CALIPER_BENCH_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            with open(CALIPER_BENCH_CONFIG, 'w') as f:
                f.write(tests_content)
        
        account_offset = session.total_creates

        # Run Caliper
        env = os.environ.copy()
        env["SMALLBANK_ACCOUNT_OFFSET"] = str(account_offset)

        cmd = ["python3", str(BASE_DIR / "scripts/run_experiment.py"), "--test-only", "--chaincode", _ACTIVE_PROFILE["_key"]]

        print(f"Running test (offset={account_offset})...")
        start_time = time.time()
        try:
            # Caliper might take a while, consider timeout
            ret, stdout, stderr = _run_with_output(cmd, cwd=str(BASE_DIR), env=env)
            duration = time.time() - start_time

            log_output = stdout + stderr
            result_summary = self._extract_results(log_output)

            # Derive create count from actual Caliper output (succ + fail)
            create_result = result_summary.get("create", {})
            succ = int(create_result.get("succ", 0))
            fail = int(create_result.get("fail", 0))
            actual_creates = succ + fail

            test_result = {
                "test_number": session.test_count + 1,
                "log": log_output,
                "result": result_summary,
                "status": "success" if ret == 0 else "failure",
                "returncode": ret,
                "time": duration,
                "account_offset": account_offset,
                "tx_number": actual_creates,
            }

            session_manager.record_test(session_id, {
                "result": result_summary,
                "status": test_result["status"],
                "time": duration
            })
            if actual_creates > 0:
                session_manager.record_creates(session_id, actual_creates)

            self._send_json({"session_id": session_id, **test_result})
            
        except Exception as e:
            self._send_json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_session_end(self, params):
        session_id = params.get("session_id")
        try:
            session = session_manager.end_session(session_id)
        except RuntimeError as e:
            self._send_json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            return

        # Cleanup
        print("Ending session, cleaning up...")
        cmd = ["python3", str(BASE_DIR / "scripts/cleanup.py")]
        start_time = time.time()
        ret, stdout, stderr = _run_with_output(cmd, cwd=str(BASE_DIR))
        duration = time.time() - start_time
        
        total_duration = time.time() - session.created_at
        
        self._send_json({
            "status": "success",
            "session_id": session_id,
            "message": "Session ended",
            "summary": {
                "total_tests": session.test_count,
                "total_duration": total_duration,
                "configs_used": session.configs,
                "test_results": session.test_results
            },
            "cleanup_log": stdout,
            "cleanup_time": duration
        })

    def _handle_session_status(self):
        if session_manager.active_session:
            session = session_manager.active_session
            self._send_json({
                "active": True,
                "session_id": session.session_id,
                "test_count": session.test_count,
                "created_at": session.created_at,
                "uptime": time.time() - session.created_at,
                "configs": session.configs
            })
        else:
            self._send_json({"active": False, "session_id": None})

    def _handle_info(self):
        # Return default configs if possible, or empty
        self._send_json({
            "core_cfg": {
                "CORE_PEER_GATEWAY_ENDORSEMENTTIMEOUT": "30s",
                "CORE_PEER_GATEWAY_BROADCASTTIMEOUT": "30s",
                "CORE_PEER_GATEWAY_DIALTIMEOUT": "2m",
                "CORE_PEER_KEEPALIVE_INTERVAL": "7200s",
                "CORE_PEER_KEEPALIVE_TIMEOUT": "20s",
                "CORE_PEER_KEEPALIVE_MININTERVAL": "60s",
                "CORE_PEER_KEEPALIVE_CLIENT_INTERVAL": "60s",
                "CORE_PEER_KEEPALIVE_CLIENT_TIMEOUT": "20s",
                "CORE_PEER_KEEPALIVE_DELIVERYCLIENT_INTERVAL": "60s",
                "CORE_PEER_KEEPALIVE_DELIVERYCLIENT_TIMEOUT": "20s",
                "CORE_PEER_GOSSIP_MEMBERSHIPTRACKERINTERVAL": "5s",
                "CORE_PEER_GOSSIP_MAXBLOCKCOUNTTOSTORE": "10",
                "CORE_PEER_GOSSIP_MAXPROPAGATIONBURSTLATENCY": "10ms",
                "CORE_PEER_GOSSIP_MAXPROPAGATIONBURSTSIZE": "10",
                "CORE_PEER_GOSSIP_PROPAGATEITERATIONS": "1",
                "CORE_PEER_GOSSIP_PROPAGATEPEERNUM": "3",
                "CORE_PEER_GOSSIP_PULLINTERVAL": "4s",
                "CORE_PEER_GOSSIP_PULLPEERNUM": "3",
                "CORE_PEER_GOSSIP_REQUESTSTATEINFOINTERVAL": "4s",
                "CORE_PEER_GOSSIP_PUBLISHSTATEINFOINTERVAL": "4s",
                "CORE_PEER_GOSSIP_PUBLISHCERTPERIOD": "10s",
                "CORE_PEER_GOSSIP_DIALTIMEOUT": "3s",
                "CORE_PEER_GOSSIP_CONNTIMEOUT": "2s",
                "CORE_PEER_GOSSIP_RECVBUFFSIZE": "20",
                "CORE_PEER_GOSSIP_SENDBUFFSIZE": "200",
                "CORE_PEER_GOSSIP_DIGESTWAITTIME": "1s",
                "CORE_PEER_GOSSIP_REQUESTWAITTIME": "1500ms",
                "CORE_PEER_GOSSIP_RESPONSEWAITTIME": "2s",
                "CORE_PEER_GOSSIP_ALIVETIMEINTERVAL": "5s",
                "CORE_PEER_GOSSIP_ALIVEEXPIRATIONTIMEOUT": "25s",
                "CORE_PEER_GOSSIP_RECONNECTINTERVAL": "25s",
                "CORE_PEER_GOSSIP_MAXCONNECTIONATTEMPTS": "120",
                "CORE_PEER_GOSSIP_MSGEXPIRATIONFACTOR": "20",
                "CORE_PEER_GOSSIP_ELECTION_STARTUPGRACEPERIOD": "15s",
                "CORE_PEER_GOSSIP_ELECTION_MEMBERSHIPSAMPLEINTERVAL": "1s",
                "CORE_PEER_GOSSIP_ELECTION_LEADERALIVETHRESHOLD": "10s",
                "CORE_PEER_GOSSIP_ELECTION_LEADERELECTIONDURATION": "5s",
                "CORE_PEER_GOSSIP_PVTDATA_PULLRETRYTHRESHOLD": "60s",
                "CORE_PEER_GOSSIP_PVTDATA_TRANSIENTSTOREMAXBLOCKRETENTION": "20000",
                "CORE_PEER_GOSSIP_PVTDATA_PUSHACKTIMEOUT": "3s",
                "CORE_PEER_GOSSIP_PVTDATA_BTLPULLMARGIN": "10",
                "CORE_PEER_GOSSIP_PVTDATA_RECONCILEBATCHSIZE": "10",
                "CORE_PEER_GOSSIP_PVTDATA_RECONCILESLEEPINTERVAL": "1m",
                "CORE_PEER_GOSSIP_PVTDATA_IMPLICITCOLLECTIONDISSEMINATIONPOLICY_REQUIREDPEERCOUNT": "0",
                "CORE_PEER_GOSSIP_PVTDATA_IMPLICITCOLLECTIONDISSEMINATIONPOLICY_MAXPEERCOUNT": "1",
                "CORE_PEER_GOSSIP_STATE_ENABLED": "true",
                "CORE_PEER_GOSSIP_STATE_CHECKINTERVAL": "10s",
                "CORE_PEER_GOSSIP_STATE_RESPONSETIMEOUT": "3s",
                "CORE_PEER_GOSSIP_STATE_BATCHSIZE": "10",
                "CORE_PEER_GOSSIP_STATE_BLOCKBUFFERSIZE": "20",
                "CORE_PEER_GOSSIP_STATE_MAXRETRIES": "3",
                "CORE_PEER_AUTHENTICATION_TIMEWINDOW": "15m",
                "CORE_PEER_CLIENT_CONNTIMEOUT": "3s",
                "CORE_PEER_DELIVERYCLIENT_RECONNECTTOTALTIMETHRESHOLD": "3600s",
                "CORE_PEER_DELIVERYCLIENT_CONNTIMEOUT": "3s",
                "CORE_PEER_DELIVERYCLIENT_RECONNECTBACKOFFTHRESHOLD": "3600s",
                "CORE_PEER_DELIVERYCLIENT_BLOCKCENSORSHIPTIMEOUTKEY": "30s",
                "CORE_PEER_DELIVERYCLIENT_MINIMALRECONNECTINTERVAL": "100ms",
                "CORE_PEER_DISCOVERY_AUTHCACHEMAXSIZE": "1000",
                "CORE_PEER_DISCOVERY_AUTHCACHEPURGERETENTIONRATIO": "0.75",
                "CORE_PEER_LIMITS_CONCURRENCY_ENDORSERSERVICE": "2500",
                "CORE_PEER_LIMITS_CONCURRENCY_DELIVERSERVICE": "2500",
                "CORE_PEER_LIMITS_CONCURRENCY_GATEWAYSERVICE": "500",
                "CORE_PEER_MAXRECVMSGSIZE": "104857600",
                "CORE_PEER_MAXSENDMSGSIZE": "104857600",
                "CORE_VM_DOCKER_HOSTCONFIG_LOGCONFIG_CONFIG_MAX_SIZE": "50m",
                "CORE_VM_DOCKER_HOSTCONFIG_LOGCONFIG_CONFIG_MAX_FILE": "5",
                "CORE_VM_DOCKER_HOSTCONFIG_MEMORY": "2147483648",
                "CORE_CHAINCODE_INSTALLTIMEOUT": "300s",
                "CORE_CHAINCODE_STARTUPTIMEOUT": "300s",
                "CORE_CHAINCODE_EXECUTETIMEOUT": "30s",
                "CORE_CHAINCODE_KEEPALIVE": "0",
                "CORE_LEDGER_STATE_TOTALQUERYLIMIT": "100000",
                "CORE_LEDGER_STATE_COUCHDBCONFIG_MAXRETRIES": "3",
                "CORE_LEDGER_STATE_COUCHDBCONFIG_MAXRETRIESONSTARTUP": "10",
                "CORE_LEDGER_STATE_COUCHDBCONFIG_REQUESTTIMEOUT": "35s",
                "CORE_LEDGER_STATE_COUCHDBCONFIG_INTERNALQUERYLIMIT": "1000",
                "CORE_LEDGER_STATE_COUCHDBCONFIG_MAXBATCHUPDATESIZE": "1000",
                "CORE_LEDGER_STATE_COUCHDBCONFIG_CACHESIZE": "64",
                "CORE_LEDGER_PVTDATASTORE_COLLELGPROCMAXDBBATCHSIZE": "5000",
                "CORE_LEDGER_PVTDATASTORE_COLLELGPROCDBBATCHESINTERVAL": "1000",
                "CORE_LEDGER_PVTDATASTORE_DEPRIORITIZEDDATARECONCILERINTERVAL": "60m",
                "CORE_LEDGER_PVTDATASTORE_PURGEINTERVAL": "100",
                "CORE_METRICS_STATSD_WRITEINTERVAL": "10s"
            },
            "orderer_cfg": {
                "ORDERER_GENERAL_KEEPALIVE_SERVERMININTERVAL": "60s",
                "ORDERER_GENERAL_KEEPALIVE_SERVERINTERVAL": "7200s",
                "ORDERER_GENERAL_KEEPALIVE_SERVERTIMEOUT": "20s",
                "ORDERER_GENERAL_BACKOFF_BASEDELAY": "1s",
                "ORDERER_GENERAL_BACKOFF_MULTIPLIER": "1.6",
                "ORDERER_GENERAL_BACKOFF_MAXDELAY": "2m",
                "ORDERER_GENERAL_MAXRECVMSGSIZE": "104857600",
                "ORDERER_GENERAL_MAXSENDMSGSIZE": "104857600",
                "ORDERER_GENERAL_THROTTLING_RATE": "0",
                "ORDERER_GENERAL_THROTTLING_INACTIVITYTIMEOUT": "5s",
                "ORDERER_GENERAL_CLUSTER_SENDBUFFERSIZE": "100",
                "ORDERER_GENERAL_AUTHENTICATION_TIMEWINDOW": "15m",
                "ORDERER_METRICS_STATSD_WRITEINTERVAL": "30s",
                "ORDERER_CHANNELPARTICIPATION_MAXREQUESTBODYSIZE": "1MB"
            },
            "tx_cfg": {
                "BatchTimeout": "2s",
                "MaxMessageCount": "10",
                "AbsoluteMaxBytes": "99 MB",
                "PreferredMaxBytes": "512 KB"
            },
            "session": {
                "active": session_manager.has_active_session,
                "session_id": session_manager.active_session.session_id if session_manager.active_session else None
            }
        })

    def _send_json(self, payload, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode('utf-8'))

    def _extract_tx_and_workers(self, tests_text: str) -> Tuple[int, int, int]:
        tx_number = 0
        workers = 1
        
        # Search for create_account
        create_match = re.search(r"create_account:\s*(\d+)", tests_text)
        if create_match:
            try:
                tx_number = int(create_match.group(1))
            except ValueError:
                pass
                
        # Search for workers
        workers_match = re.search(r"workers\s*:\s*\n\s*number\s*:\s*(\d+)", tests_text, re.IGNORECASE)
        if workers_match:
            try:
                workers = int(workers_match.group(1))
            except ValueError:
                pass
                
        total_creates = tx_number * workers
        return tx_number, workers, total_creates

    def _extract_results(self, log_output: str) -> Dict[str, Dict[str, object]]:
        # Caliper output usually contains a table.
        # We look for the table markers.
        # This regex is simplified based on typical Caliper output.
        
        # Look for table header
        # | Name | Succ | Fail | Send Rate (TPS) | Max Latency (s) | ...
        
        # Find the start of the table
        table_start_regex = r"\|\s*Name\s*\|\s*Succ\s*\|"
        match = re.search(table_start_regex, log_output)
        if not match:
            return {}
            
        # Extract the table block
        start_idx = match.start()
        # Find the line before (separator)
        lines = log_output[max(0, start_idx - 200):start_idx + 2000].splitlines()
        
        # Locate header line index in the snippet
        header_idx = -1
        for i, line in enumerate(lines):
            if "Name" in line and "Succ" in line:
                header_idx = i
                break
        
        if header_idx == -1:
            return {}
            
        # Parse header
        header_line = lines[header_idx]
        headers = [h.strip() for h in header_line.split('|') if h.strip()]
        
        results = {}
        
        # Parse rows
        for i in range(header_idx + 2, len(lines)):
            line = lines[i].strip()
            if not line or line.startswith('+'):
                continue
            if not line.startswith('|'):
                continue
                
            cols = [c.strip() for c in line.split('|') if c.strip() != '']
            if len(cols) != len(headers):
                continue
                
            row_data = {}
            name = cols[0]
            
            for j, col_val in enumerate(cols):
                header_name = headers[j]
                # Map header name to key
                key = header_name.lower().replace(" (tps)", "").replace(" (s)", "").replace(" ", "-")
                if key == "avg-latency":
                    key = "avg-lat"
                if key == "max-latency":
                    key = "max-lat"
                if key == "min-latency":
                    key = "min-lat"
                if key == "name":
                    continue
                
                # Convert numeric
                try:
                    val = float(col_val)
                except ValueError:
                    val = col_val
                row_data[key] = val
            
            results[name] = row_data
            
        return results

if __name__ == "__main__":
    server_address = ('0.0.0.0', 8080)
    httpd = http.server.HTTPServer(server_address, RequestHandler)
    print("Starting local config server on port 8080...")
    httpd.serve_forever()
