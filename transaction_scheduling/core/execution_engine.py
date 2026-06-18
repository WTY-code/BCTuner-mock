import re
import subprocess
import sys
from typing import Dict, List


class CaliperExecutor:
    def __init__(self, workspace: str, verbose: bool = False):
        self.workspace = workspace
        self.verbose = verbose

    def run(self, config_path: str) -> List[Dict]:
        cmd = [
            "npx", "caliper", "launch", "manager",
            "--caliper-workspace", self.workspace,
            "--caliper-networkconfig", "networks/fabric/network-config-gateway.yaml",
            "--caliper-benchconfig", config_path,
            "--caliper-fabric-gateway-enabled",
            "--caliper-flow-only-test",
        ]
        try:
            p = subprocess.run(
                cmd, cwd=self.workspace,
                capture_output=True, text=True,
            )
            output = p.stdout + p.stderr
            if self.verbose:
                print(output)
            return self._parse_table_rows(output)
        except Exception as e:
            print(f"[Executor] Critical Error: {e}")
            return []

    def _parse_table_rows(self, stdout: str) -> List[Dict]:
        # Caliper emits both "Test result" and "All test results" tables
        # with identical rows. Parse only the first table.
        records = []
        seen_groups = set()
        in_first_table = False
        table_ended = False

        pattern = re.compile(
            r"^\|\s*(?P<name>[\w_]+)\s*\|\s*"
            r"(?P<succ>\d+)\s*\|\s*"
            r"(?P<fail>\d+)\s*\|\s*"
            r"(?P<send_rate>[\d\.]+)\s*\|\s*"
            r"(?P<max_lat>[\d\.]+)\s*\|\s*"
            r"(?P<min_lat>[\d\.]+)\s*\|\s*"
            r"(?P<avg_lat>[\d\.]+)\s*\|\s*"
            r"(?P<throughput>[\d\.]+)\s*\|"
        )

        for line in stdout.splitlines():
            clean = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", line.strip())

            if "Test result" in clean:
                if table_ended:
                    break
                in_first_table = True
                continue
            if "All test" in clean and in_first_table:
                table_ended = True
                continue

            if not in_first_table or table_ended:
                continue

            m = pattern.match(clean)
            if not m:
                continue
            d = m.groupdict()
            if d["name"] == "Name":
                continue
            if d["name"] in seen_groups:
                continue
            seen_groups.add(d["name"])
            records.append({
                "group_name": d["name"],
                "succ": int(d["succ"]),
                "fail": int(d["fail"]),
                "send_rate": float(d["send_rate"]),
                "max_latency": float(d["max_lat"]),
                "min_latency": float(d["min_lat"]),
                "avg_latency": float(d["avg_lat"]),
                "throughput": float(d["throughput"]),
            })

        return records
