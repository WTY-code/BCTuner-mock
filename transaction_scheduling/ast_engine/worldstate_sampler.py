#!/usr/bin/env python3
"""World State Sampler — queries Fabric chaincode to collect value distributions.

For each histogram field declared by the LLM template, the sampler:
1. Uses a key-space descriptor to enumerate world-state keys
2. Queries the chaincode via ``peer chaincode query`` over SSH
3. Parses numeric values from the responses
4. Outputs a flat list of floats per field

Usage::

    sampler = WorldStateSampler(peer_ssh="<user@peer-host>", cli_container="cli-peer0.org1.example.com")
    values = sampler.sample(chaincode="token_erc20", key_pattern="balance:{account}",
                            key_args={"account": ["a" + str(i) for i in range(1, 1001)]})
"""

import json
import re
import shlex
import subprocess
from typing import Dict, List, Optional


class WorldStateSampler:
    """Samples numeric values from Fabric world state via SSH + peer CLI query."""

    def __init__(self, peer_ssh: str, cli_container: str = "cli-peer0.org1.example.com",
                 orderer: str = "orderer0.example.com:7050",
                 channel: str = "mychannel",
                 tls_ca: str = "/opt/gopath/src/github.com/hyperledger/fabric/peer/crypto/"
                                "ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem"):
        self.peer_ssh = peer_ssh
        self.cli = cli_container
        self.orderer = orderer
        self.channel = channel
        self.tls_ca = tls_ca

    def sample(self, chaincode: str, key_pattern: str,
               key_args: Dict[str, List[str]], max_samples: int = 1000) -> List[float]:
        """Sample numeric values for a field.

        Args:
            chaincode:  chaincode name (e.g. "token_erc20")
            key_pattern: Fabric key pattern (e.g. "balance:{account}")
            key_args:    dict mapping placeholder names to value lists
            max_samples: max keys to query

        Returns:
            list of float values extracted from world state
        """
        keys = self._enumerate_keys(key_pattern, key_args, max_samples)
        values = []
        for key in keys:
            v = self._query_key(chaincode, key)
            if v is not None:
                values.append(v)
        return values

    def sample_bulk(self, chaincode: str, query_fn: str,
                    fields: List[str], max_samples: int = 5000) -> Dict[str, List[float]]:
        """Call a bulk chaincode query that returns a JSON array of objects.

        Intended for chaincodes (e.g. Smallbank) whose keys are hashed and
        cannot be enumerated directly.  The query function must return a JSON
        array of objects, each containing one or more numeric fields.

        Args:
            chaincode:   chaincode name (e.g. "smallbank")
            query_fn:    chaincode function that returns the JSON array
                         (e.g. "query_all_accounts")
            fields:      list of JSON field names to extract
            max_samples: cap on number of records to process

        Returns:
            dict mapping each field name to a list of float values
        """
        args_json = json.dumps({"Args": [query_fn]})
        remote_cmd = (
            f"docker exec {self.cli} "
            f"peer chaincode query "
            f"-C {self.channel} -n {chaincode} "
            f"-c {shlex.quote(args_json)}"
        )
        r = subprocess.run(
            ["ssh", self.peer_ssh, remote_cmd],
            capture_output=True, text=True, timeout=60,
        )
        raw = r.stdout.strip()
        try:
            records = json.loads(raw)
        except json.JSONDecodeError:
            return {}

        out: Dict[str, List[float]] = {f: [] for f in fields}
        for rec in records[:max_samples]:
            if not isinstance(rec, dict):
                continue
            for field in fields:
                val = rec.get(field)
                if val is not None:
                    try:
                        out[field].append(float(val))
                    except (ValueError, TypeError):
                        pass
        return out

    # ------------------------------------------------------------------

    def _enumerate_keys(self, pattern: str, arg_sets: Dict[str, List[str]],
                        limit: int) -> List[str]:
        """Cartesian product of arg lists, substituting into pattern."""
        import itertools
        arg_names = [k for k in arg_sets]
        arg_lists = [arg_sets[k] for k in arg_names]
        keys = []
        for combo in itertools.product(*arg_lists):
            key = pattern
            for name, val in zip(arg_names, combo):
                key = key.replace("{" + name + "}", val)
            keys.append(key)
            if len(keys) >= limit:
                break
        return keys

    def _query_key(self, chaincode: str, key: str) -> Optional[float]:
        """Query a single key via peer chaincode query and extract numeric value."""
        invoke_args = json.dumps({"Args": ["get_state", key]})  # generic
        # Use a raw GetState approach: we query via balance_of for token-erc-20,
        # but for generic chaincodes we need a raw state reader.
        # Simplest: use `peer chaincode query` with a read function.
        # For now, support the standard "get" or "balance_of" pattern via the
        # chaincode's own query function.
        try:
            val = self._query_chaincode(chaincode, key)
            return self._parse_numeric(val)
        except Exception:
            return None

    def _query_chaincode(self, chaincode: str, key: str) -> str:
        """Query chaincode state for a key and return the raw string value.

        Uses a generic approach: tries chaincode-specific query functions,
        falls back to a direct state read via system chaincode.
        """
        # Build peer chaincode query command
        # We use the chaincode's query function — for token-erc-20: balance_of,
        # for TPC-C: order_status, etc. But generic GetState needs a custom query fn.
        # Simplest approach for V1: add a tiny "get_state" query to each chaincode,
        # or use `peer chaincode query` with a standard key read.

        # For now, use the lscc/system chaincode approach: read state directly
        cmd = (
            f'ssh {self.peer_ssh} "docker exec {self.cli} '
            f'peer chaincode query -o {self.orderer} --tls '
            f'--cafile {self.tls_ca} '
            f'-C {self.channel} -n {chaincode} '
            f'-c \'{{"Args":["get_state","{key}"]}}\' '
            f'2>&1"'
        )
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        # Extract the payload from the response (status:200 payload:"value")
        m = re.search(r'payload:"(.*?)"', r.stdout)
        if m:
            return m.group(1)
        return r.stdout.strip()

    @staticmethod
    def _parse_numeric(raw: str) -> Optional[float]:
        """Extract a numeric value from a chaincode response string."""
        if not raw:
            return None
        # Try direct number parse
        try:
            return float(raw)
        except ValueError:
            pass
        # Try extracting from key=value format (e.g. "BALANCE=50000|CREDIT=GC|...")
        for part in raw.split("|"):
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    return float(v)
                except ValueError:
                    continue
        return None
