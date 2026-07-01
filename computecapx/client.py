"""Secure, non-blocking HTTP client for ComputeCapX telemetry transmission."""

import os
import json
import sys
import threading
import time
import queue
from pathlib import Path
import requests
import atexit
import logging
from typing import Dict, Any, Optional, List

_logger = logging.getLogger(__name__)
logging.getLogger(__name__).addHandler(logging.NullHandler())


def _load_dotenv_file() -> Dict[str, str]:
    """Load local .env values into os.environ without overriding explicit shell values."""
    loaded: Dict[str, str] = {}
    current_dir = Path.cwd()

    for _ in range(10):
        dotenv_path = current_dir / ".env"
        if dotenv_path.exists() and dotenv_path.is_file():
            try:
                with open(dotenv_path, "r", encoding="utf-8") as handle:
                    for raw_line in handle:
                        line = raw_line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("export "):
                            line = line[len("export "):].strip()
                        if "=" not in line:
                            continue
                        key, value = line.split("=", 1)
                        key = key.strip()
                        # Clean inline comments
                        if "#" in value:
                            # Avoid stripping if '#' is inside quotes
                            if not (value.startswith('"') and value.endswith('"')) and not (value.startswith("'") and value.endswith("'")):
                                value = value.split("#", 1)[0]
                        value = value.strip().strip('"').strip("'")
                        if key and key not in os.environ and key not in loaded:
                            os.environ[key] = value
                            loaded[key] = value
            except Exception:
                pass

        if current_dir.parent == current_dir:
            break
        current_dir = current_dir.parent

    return loaded


_config_lock = threading.Lock()

def _load_persisted_config() -> Dict[str, str]:
    """Helper to safely retrieve CLI-persisted configuration from the user's home directory."""
    config_file = Path.home() / ".computecapx" / "config.json"
    if config_file.exists():
        with _config_lock:
            try:
                with open(config_file, "r") as f:
                    return json.load(f)
            except Exception:
                # Silence internal file load exceptions to prevent host app disruption
                pass
    return {}

def _save_persisted_config(config: Dict[str, Any]) -> None:
    """Helper to safely save configuration to the user's home directory."""
    config_dir = Path.home() / ".computecapx"
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        
        with _config_lock:
            # Load existing config to merge it
            existing = {}
            if config_file.exists():
                try:
                    with open(config_file, "r") as f:
                        existing = json.load(f)
                except Exception:
                    pass
                    
            existing.update(config)
            
            # Atomic write pattern to prevent file corruption (with retry loop for Windows sharing violations)
            tmp_file = config_file.with_suffix(".tmp")
            try:
                with open(tmp_file, "w") as f:
                    json.dump(existing, f)
                
                # Retry replace to avoid Windows PermissionError under heavy parallel process loads
                for attempt in range(5):
                    try:
                        tmp_file.replace(config_file)
                        break
                    except PermissionError:
                        if attempt == 4:
                            raise
                        time.sleep(0.05)
            except Exception:
                if tmp_file.exists():
                    tmp_file.unlink()
                raise
    except Exception:
        pass

class ComputeCapClient:
    """
    Handles secure, asynchronous transmission of telemetry data to the ComputeCap backend.
    """
    
    DEFAULT_API_URL = "https://api.computecapx.com/api/v1"
    
    def __init__(self, api_key: Optional[str] = None, backend_url: Optional[str] = None):
        _load_dotenv_file()

        # Read the CLI configuration file fallbacks
        stored_config = _load_persisted_config()
        
        # 1. Resolve API Key: Constructor parameter -> Environment Variable -> CLI JSON Stored Config
        self.api_key = (
            api_key 
            or os.getenv("COMPUTECAPX_API_KEY")
            or stored_config.get("api_key")
        )
        
        # 2. Resolve Backend URL: Constructor parameter -> Environment Variable -> CLI JSON Stored Config -> Local Fallback
        raw_url = (
            backend_url 
            or os.getenv("COMPUTECAPX_BACKEND_URL")
            or stored_config.get("backend_url") 
            or self.DEFAULT_API_URL
        )
        raw_url = raw_url.rstrip("/")
        if not raw_url.endswith("/api/v1"):
            raw_url = f"{raw_url}/api/v1"
        self.backend_url = raw_url
        
        self._budget_blocked = False
        self._budget_blocked_until = 0.0
        
        if not self.api_key:
            import warnings
            warnings.warn("ComputeCapX API Key is missing. Telemetry will not be recorded.")
            
        self._active_threads = []
        self._thread_lock = threading.Lock()
        self._preflight_lock = threading.Lock()
        self._trace_queue = queue.Queue()
        self._shutdown_event = threading.Event()
        
        self._batch_thread = threading.Thread(target=self._batch_worker, daemon=True)
        self._batch_thread.start()
        
        atexit.register(self.flush)
        self._require_preflight = bool(stored_config.get("require_preflight", False))
        self._has_done_initial_check = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self) -> None:
        """Closes the client, triggers flush, and unregisters exit hooks to release resources."""
        try:
            atexit.unregister(self.flush)
        except Exception:
            pass
        self.flush()

    def flush(self) -> None:
        """Blocks until all active telemetry requests are completed."""
        self._shutdown_event.set()
        
        # Sentinel to unblock get() instantly in batch worker
        self._trace_queue.put(None)
        
        # Gracefully join the daemon batch thread to prevent telemetry data loss on exit
        if hasattr(self, "_batch_thread") and self._batch_thread.is_alive():
            self._batch_thread.join(timeout=2.0)

        with self._thread_lock:
            threads_to_join = list(self._active_threads)
        for t in threads_to_join:
            if t.is_alive():
                t.join(timeout=4.0)
                
        # Drain remaining events in the batch queue
        batch = []
        while not self._trace_queue.empty():
            try:
                batch.append(self._trace_queue.get_nowait())
            except queue.Empty:
                break
                
        if batch and self.api_key:
            try:
                base = self.backend_url.replace("/api/v1", "").rstrip("/")
                requests.post(
                    f"{base}/api/v1/telemetry/traces",
                    json=batch,
                    headers=self._get_headers(),
                    timeout=3.0
                )
            except Exception:
                pass

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key is not None:
            headers["X-API-Key"] = str(self.api_key)
        return headers

    def _batch_worker(self) -> None:
        """Daemon thread that reuses a single connection to batch telemetry events."""
        session = requests.Session()
        session.headers.update(self._get_headers())
        
        while not self._shutdown_event.is_set():
            batch = []
            try:
                # Wait up to 1 second for the first event
                first_event = self._trace_queue.get(timeout=1.0)
                if first_event is None:
                    break
                batch.append(first_event)
                
                # Drain up to 99 more immediately
                while len(batch) < 100:
                    try:
                        item = self._trace_queue.get_nowait()
                        if item is None:
                            break
                        batch.append(item)
                    except queue.Empty:
                        break
            except queue.Empty:
                continue
                
            if batch:
                for attempt in range(3):
                    try:
                        base = self.backend_url.replace("/api/v1", "").rstrip("/")
                        res = session.post(
                            f"{base}/api/v1/telemetry/traces",
                            json=batch,
                            timeout=5.0
                        )
                        if res.status_code == 200:
                            break
                    except Exception:
                        pass
                    if attempt < 2:
                        for _ in range(50):
                            if self._shutdown_event.is_set():
                                break
                            time.sleep(0.1)

    def _send_async(self, endpoint: str, payload: Dict[str, Any], max_retries: int = 1) -> None:
        """Internal method to execute HTTP POST requests in a detached thread with retry support."""
        if not self.api_key:
            return

        def _post():
            retry_delay = 10
            for attempt in range(max_retries):
                try:
                    res = requests.post(
                        f"{self.backend_url}/{endpoint}",
                        json=payload,
                        headers=self._get_headers(),
                        timeout=5.0  # Background thread can have a slightly larger timeout for reliability
                    )
                    # Succeed on standard HTTP OK
                    if res.status_code == 200:
                        if endpoint == "telemetry/ai":
                            try:
                                data = res.json()
                                if isinstance(data, dict):
                                    near_limit = data.get("near_limit", False)
                                    if self._require_preflight != near_limit:
                                        self._require_preflight = near_limit
                                        _save_persisted_config({"require_preflight": near_limit})
                            except Exception:
                                pass
                        break
                    elif res.status_code == 403:
                        if endpoint == "telemetry/ai":
                            if not self._require_preflight:
                                self._require_preflight = True
                                _save_persisted_config({"require_preflight": True})
                        break
                except requests.exceptions.RequestException:
                    pass
                
                # Wait before next retry attempt
                if attempt < max_retries - 1:
                    sleep_slices = int(retry_delay / 0.1)
                    for _ in range(sleep_slices):
                        if self._shutdown_event.is_set():
                            break
                        time.sleep(0.1)

        with self._thread_lock:
            self._active_threads = [t for t in self._active_threads if t.is_alive()]
            # Bound parallel thread spawning to 15 to avoid resource exhaustion
            if len(self._active_threads) >= 15:
                return

        thread = threading.Thread(target=_post, daemon=True)
        with self._thread_lock:
            self._active_threads.append(thread)
            thread.start()

    def record_ai_telemetry(self, payload: Dict[str, Any]) -> None:
        """Transmits AI token usage and cost metrics asynchronously with up to 3 retries."""
        self._send_async("telemetry/ai", payload, max_retries=3)

    def record_cloud_telemetry(self, payload: Dict[str, Any]) -> None:
        """Transmits infrastructure state transitions asynchronously with up to 15 retries (2.5 minutes)."""
        self._send_async("telemetry/cloud", payload, max_retries=15)

    def record_trace_event(self, payload: Dict[str, Any]) -> None:
        """Transmits raw trace telemetry using the bulk queue worker to avoid socket exhaustion."""
        if not self.api_key:
            return
        self._trace_queue.put(payload)


    def check_budget_sync(self, project_id: str, provider: str = "unknown", model: str = "unknown") -> bool:
        """
        Synchronous pre-flight check to determine if the project has exceeded its budget.
        Sends a 0-cost diagnostic pulse. If the backend Firewall is active, it returns 403.
        """
        current_time = time.time()
        if self._budget_blocked and current_time < self._budget_blocked_until:
            # Budget exceeded: fail-fast locally during cooldown to avoid application latency
            return False

        if not self._require_preflight and self._has_done_initial_check:
            # Fast path: budget check is not required, skip the network round-trip.
            return True
            
        with self._preflight_lock:
            # Recheck under lock to avoid duplicate parallel requests on startup
            if not self._require_preflight and self._has_done_initial_check:
                return True
            self._has_done_initial_check = True

            if not self.api_key:
                return True # Fail open if no API key is provided
            
        try:
            payload = {
                "project_id": project_id,
                "provider": provider,
                "model_name": f"preflight_check:{model}",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_delta": 0.0
            }
            
            res = requests.post(
                f"{self.backend_url}/telemetry/ai",
                json=payload,
                headers=self._get_headers(),
                timeout=3.0
            )
            
            # A 403 response indicates the budget limit has been reached.
            if res.status_code == 403:
                try:
                    error_msg = res.json().get("detail", "Unknown 403 Forbidden")
                    _logger.warning("[ComputeCapX] Budget limit reached: %s", error_msg)
                except Exception:
                    pass
                
                # Cache budget failure for 60 seconds to avoid blocking HTTP calls
                self._budget_blocked = True
                self._budget_blocked_until = time.time() + 60.0

                if not self._require_preflight:
                    self._require_preflight = True
                    threading.Thread(
                        target=_save_persisted_config,
                        args=({"require_preflight": True},),
                        daemon=True
                    ).start()
                return False
            elif res.status_code == 200:
                self._budget_blocked = False
                self._budget_blocked_until = 0.0
                try:
                    data = res.json()
                    if isinstance(data, dict):
                        near_limit = data.get("near_limit", False)
                        if self._require_preflight != near_limit:
                            self._require_preflight = near_limit
                            threading.Thread(
                                target=_save_persisted_config,
                                args=({"require_preflight": near_limit},),
                                daemon=True
                            ).start()
                except Exception:
                    pass
                
        except requests.exceptions.RequestException as e:
            pass # Fail open on network errors to ensure the host app never crashes
            
        return True