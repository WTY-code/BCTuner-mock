"""Lightweight client for interacting with config_server, plus session helpers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Dict, Iterator, List, Mapping, Optional, Type
from urllib import request


@dataclass
class ConfigSDKResponse:
    status: int
    body: Dict[str, Any]


class ConfigSDKError(RuntimeError):
    """Raised when the server responds with an error payload or non-2xx status."""

    def __init__(self, status: int, payload: Any):
        super().__init__(f"Server error (status={status}): {payload}")
        self.status = status
        self.payload = payload


class ConfigSDK:
    """Simple HTTP JSON client for the config server."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        *,
        timeout: int = 1800,
        session_timeout: int = 1800,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_timeout = session_timeout

    def _post(self, payload: Mapping[str, Any], *, timeout: Optional[int] = None) -> ConfigSDKResponse:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.base_url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        from urllib.error import HTTPError
        try:
            with request.urlopen(req, timeout=timeout or self.timeout) as resp:
                status = resp.getcode()
                raw = resp.read()
        except HTTPError as e:
            status = e.getcode()
            raw = e.read()
        except Exception as exc:  # pragma: no cover - pass-through for callers
            raise RuntimeError(f"Failed to contact server: {exc}") from exc

        body_text = raw.decode("utf-8")
        try:
            body = json.loads(body_text)
        except json.JSONDecodeError as exc:  # pragma: no cover
            raise ConfigSDKError(status, f"Invalid JSON response: {body_text}") from exc
        if status >= 400 or ("error" in body):
            raise ConfigSDKError(status, body)
        return ConfigSDKResponse(status=status, body=body)

    def test(self, configs: Optional[Mapping[str, Any]] = None, tests: Optional[str] = None) -> Dict[str, Any]:
        """Invoke TEST method with optional configs and tests payload.

        This is the original single-shot mode: deploy network, run test, cleanup.
        For running multiple tests on the same network, use session() instead.
        """
        params: Dict[str, Any] = {}
        if configs is not None:
            params["configs"] = dict(configs)
        if tests is not None:
            params["tests"] = tests
        response = self._post({"method": "TEST", "params": params})
        return response.body

    def info(self) -> Dict[str, Any]:
        """Fetch current configuration snapshots from the server."""
        response = self._post({"method": "INFO"})
        return response.body

    def update_network_config(self, topology: Dict[str, Any]) -> Dict[str, Any]:
        """Update the underlying Fabric network topology configuration."""
        response = self._post({"method": "NETWORK_CONFIG", "params": {"topology": topology}})
        return response.body

    # ==================== Session Mode API ==================== #

    def session_status(self) -> Dict[str, Any]:
        """Get the current session status from the server."""
        response = self._post({"method": "SESSION_STATUS"})
        return response.body

    def session_start(self, configs: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """Start a new session with the given configs."""
        params: Dict[str, Any] = {}
        if configs is not None:
            params["configs"] = dict(configs)
        response = self._post({"method": "SESSION_START", "params": params})
        return response.body

    def session_test(self, session_id: str, tests: Optional[str] = None) -> Dict[str, Any]:
        """Run a test within the given session."""
        params: Dict[str, Any] = {"session_id": session_id}
        if tests is not None:
            params["tests"] = tests
        response = self._post({"method": "SESSION_TEST", "params": params}, timeout=self.session_timeout)
        return response.body

    def session_end(self, session_id: str) -> Dict[str, Any]:
        """End the session and cleanup the network."""
        params: Dict[str, Any] = {"session_id": session_id}
        response = self._post({"method": "SESSION_END", "params": params})
        return response.body

    def session(self, configs: Optional[Mapping[str, Any]] = None) -> "Session":
        """Create a session context manager for running multiple tests on one network.

        Usage:
            with client.session(configs={"BatchTimeout": "1s"}) as sess:
                result1 = sess.test(tests="...")
                result2 = sess.test(tests="...")

        The network is deployed once when entering the context and cleaned up
        when exiting. All tests share the same network configuration.
        """
        return Session(self, configs or {})


class Session:
    """Context manager for running multiple Caliper tests on a single Fabric network."""

    def __init__(self, client: ConfigSDK, configs: Mapping[str, Any]) -> None:
        self._client = client
        self._configs = dict(configs)
        self._session_id: Optional[str] = None
        self._test_count: int = 0
        self._test_results: List[Dict[str, Any]] = []
        self._start_response: Optional[Dict[str, Any]] = None
        self._end_response: Optional[Dict[str, Any]] = None
        self._active: bool = False

    @property
    def session_id(self) -> Optional[str]:
        """The session ID assigned by the server, or None if not started."""
        return self._session_id

    @property
    def configs(self) -> Dict[str, Any]:
        """The Fabric configuration used for this session."""
        return dict(self._configs)

    @property
    def test_count(self) -> int:
        """Number of tests run in this session."""
        return self._test_count

    @property
    def test_results(self) -> List[Dict[str, Any]]:
        """List of test results from this session."""
        return list(self._test_results)

    @property
    def is_active(self) -> bool:
        """Whether the session is currently active."""
        return self._active

    @property
    def summary(self) -> Optional[Dict[str, Any]]:
        """Session summary from the server after ending, or None if not ended."""
        if self._end_response is None:
            return None
        return self._end_response.get("summary")

    def __enter__(self) -> "Session":
        """Start the session and deploy the Fabric network."""
        if self._active:
            raise RuntimeError("Session is already active")

        self._start_response = self._client.session_start(self._configs)
        self._session_id = self._start_response["session_id"]
        self._active = True
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """End the session and cleanup the network."""
        if not self._active or self._session_id is None:
            return

        try:
            self._end_response = self._client.session_end(self._session_id)
            time.sleep(5)  # Wait for server cleanup
        finally:
            self._active = False

    def test(self, tests: Optional[str] = None) -> Dict[str, Any]:
        """Run a Caliper test within this session."""
        if not self._active or self._session_id is None:
            raise RuntimeError("Session is not active. Use 'with client.session(...) as sess:'")

        result = self._client.session_test(self._session_id, tests)
        self._test_count += 1
        self._test_results.append(result)
        return result

    def restart(self) -> None:
        """End and restart the session with the same configs."""
        if not self._active or self._session_id is None:
            raise RuntimeError("Session is not active. Use 'with client.session(...) as sess:'")

        old_session_id = self._session_id
        try:
            self._client.session_end(old_session_id)
        except Exception:
            pass

        try:
            status = self._client.session_status()
            if status.get("active") and status.get("session_id"):
                try:
                    self._client.session_end(status["session_id"])
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self._start_response = self._client.session_start(self._configs)
        except ConfigSDKError as exc:
            payload = getattr(exc, "payload", None)
            session_id = payload.get("session_id") if isinstance(payload, dict) else None
            if exc.status == 409 and isinstance(session_id, str) and session_id:
                try:
                    self._client.session_end(session_id)
                except Exception:
                    pass
                self._start_response = self._client.session_start(self._configs)
            else:
                raise

        self._session_id = self._start_response["session_id"]
        self._test_count = 0
        self._test_results = []

    def __iter__(self) -> Iterator["Session"]:
        """Allow iteration pattern for convenience."""
        return iter([self])


def reset_session(base_url: str) -> None:
    """Best-effort cleanup of any active session on the config server."""
    client = ConfigSDK(base_url)
    try:
        status = client.session_status()
        if status.get("active"):
            sid = status.get("session_id")
            if sid:
                client.session_end(sid)
                time.sleep(3)
    except Exception:
        pass
