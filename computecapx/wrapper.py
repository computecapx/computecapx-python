"""Universal AI API instrumentation layer for the ComputeCapX SDK."""

import os
import time
import platform
import threading
import functools
import collections
import json
import urllib.parse
import sys
from typing import Any, Optional, List, Dict, Tuple
import psutil
import contextvars
import asyncio
import uuid
import logging

_logger = logging.getLogger(__name__)
logging.getLogger(__name__).addHandler(logging.NullHandler())

trace_ctx: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar('computecapx_trace_ctx', default=None)

_global_telemetry_engine = None

def _get_active_trace() -> str:
    ctx = trace_ctx.get()
    now = time.time()
    # Traces auto-reset after 60 seconds to prevent context bleed across ThreadPool workers.
    if ctx is None or (now - ctx['started_at']) > 60.0:
        ctx = {
            "trace_id": f"txn_{uuid.uuid4().hex[:12]}",
            "started_at": now
        }
        trace_ctx.set(ctx)
    return ctx["trace_id"]

from .client import ComputeCapClient
from .detector import EnvironmentDetector

_request_history: contextvars.ContextVar[Any] = contextvars.ContextVar('computecapx_request_history', default=None)

def _get_token_similarity(s1: str, s2: str) -> float:
    if not s1 or not s2:
        return 0.0
    words1 = set(s1.split())
    words2 = set(s2.split())
    if not words1 and not words2:
        return 1.0
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    return len(intersection) / len(union)

def _has_internal_message_repetition(messages_list: List[str]) -> bool:
    if not messages_list:
        return False
    counts = collections.Counter(messages_list)
    for msg, count in counts.items():
        if len(msg) > 20 and count >= 3:
            return True
    return False

def _detect_loop_in_history(history_slice: List[Dict[str, Any]]) -> bool:
    if len(history_slice) < 3:
        return False
        
    prompts = [item["prompt"] for item in history_slice if item.get("prompt")]
    last_msgs = [item["last_msg"] for item in history_slice if item.get("last_msg")]
    
    if not prompts:
        return False
        
    unique_prompts = set(prompts)
    unique_last_msgs = set(last_msgs)
    
    # Check 1: Exact matches or high repetitions
    if len(prompts) >= 5 and len(unique_prompts) <= max(1, len(prompts) // 5):
        return True
    if len(last_msgs) >= 5 and len(unique_last_msgs) <= max(1, len(last_msgs) // 5):
        return True
        
    # Check 2: Token Jaccard similarity of consecutive prompts
    similar_pairs = 0
    for i in range(len(prompts) - 1):
        if _get_token_similarity(prompts[i], prompts[i+1]) > 0.85:
            similar_pairs += 1
    if len(prompts) >= 4 and similar_pairs / (len(prompts) - 1) >= 0.75:
        return True
        
    # Check 3: Token Jaccard similarity of consecutive last messages
    similar_last_msgs = 0
    for i in range(len(last_msgs) - 1):
        if _get_token_similarity(last_msgs[i], last_msgs[i+1]) > 0.85:
            similar_last_msgs += 1
    if len(last_msgs) >= 4 and similar_last_msgs / (len(last_msgs) - 1) >= 0.75:
        return True
        
    return False

def _extract_prompt_details(body_bytes: bytes) -> Tuple[str, str, List[str]]:
    try:
        body = json.loads(body_bytes)
        if not isinstance(body, dict):
            return "", "", []
        
        # Check for "messages" (Chat completion APIs)
        messages = body.get("messages")
        if isinstance(messages, list):
            msg_contents = []
            for msg in messages:
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str):
                        msg_contents.append(content)
                    elif isinstance(content, list):
                        parts = []
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                parts.append(item.get("text", ""))
                            elif isinstance(item, str):
                                parts.append(item)
                        msg_contents.append(" ".join(parts))
            
            full_prompt = "\n".join(msg_contents)
            last_msg = msg_contents[-1] if msg_contents else ""
            return full_prompt, last_msg, msg_contents
        
        # Check for "prompt" (completion APIs)
        prompt = body.get("prompt")
        if isinstance(prompt, str):
            return prompt, prompt, [prompt]
        elif isinstance(prompt, list):
            prompt_strs = [str(p) for p in prompt]
            full_prompt = "\n".join(prompt_strs)
            last_msg = prompt_strs[-1] if prompt_strs else ""
            return full_prompt, last_msg, prompt_strs

        # Check for Gemini REST API {"contents": [{"parts": [{"text": "..."}]}]}
        contents = body.get("contents")
        if isinstance(contents, list):
            msg_contents = []
            for content in contents:
                if isinstance(content, dict):
                    parts = content.get("parts")
                    if isinstance(parts, list):
                        for part in parts:
                            if isinstance(part, dict) and "text" in part:
                                msg_contents.append(part["text"])
            full_prompt = "\n".join(msg_contents)
            last_msg = msg_contents[-1] if msg_contents else ""
            return full_prompt, last_msg, msg_contents
            
    except Exception:
        pass
    return "", "", []

def _extract_gemini_details(contents: Any) -> Tuple[str, str, List[str]]:
    if not contents:
        return "", "", []
    
    # If it's a simple string
    if isinstance(contents, str):
        return contents, contents, [contents]
        
    # If it's a list (e.g. list of parts or list of contents)
    if isinstance(contents, list):
        msg_contents = []
        for item in contents:
            if isinstance(item, str):
                msg_contents.append(item)
            elif hasattr(item, "text"):
                msg_contents.append(item.text)
            elif isinstance(item, dict):
                # Could be {"role": ..., "parts": ...}
                subparts = item.get("parts")
                if isinstance(subparts, list):
                    for subpart in subparts:
                        if isinstance(subpart, str):
                            msg_contents.append(subpart)
                        elif isinstance(subpart, dict) and "text" in subpart:
                            msg_contents.append(subpart["text"])
                elif "text" in item:
                    msg_contents.append(item["text"])
            else:
                # Fallback for complex object
                try:
                    # Try to see if it's a protobuf Content / Part object
                    if hasattr(item, "parts"):
                        for part in item.parts:
                            if hasattr(part, "text"):
                                msg_contents.append(part.text)
                except Exception:
                    pass
        
        full_prompt = "\n".join(msg_contents)
        last_msg = msg_contents[-1] if msg_contents else ""
        return full_prompt, last_msg, msg_contents
        
    if hasattr(contents, "text"):
        val = contents.text
        return val, val, [val]
        
    val = str(contents)
    return val, val, [val]

def _extract_response_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    # OpenAI / Groq
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
    # Anthropic
    content_list = data.get("content")
    if isinstance(content_list, list) and content_list:
        first = content_list[0]
        if isinstance(first, dict):
            text = first.get("text")
            if isinstance(text, str):
                return text
    # Gemini REST
    candidates = data.get("candidates")
    if isinstance(candidates, list) and candidates:
        candidate = candidates[0]
        if isinstance(candidate, dict):
            content = candidate.get("content")
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list) and parts:
                    part = parts[0]
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str):
                            return text
    return ""

class ComputeCapBudgetExceededError(BaseException):
    """Raised when a workspace has exceeded its financial tier limits."""
    pass

class ComputeCapRunawayLoopError(BaseException):
    """Raised when an AI agent enters an infinite loop, threatening severe financial leak."""
    pass

class ComputeCapTelemetry:
    def __init__(self, client: ComputeCapClient, project_id: str):
        self.client = client
        self.project_id = project_id
        self.environment = EnvironmentDetector.detect_environment()
        self.os_type = platform.system()
        
        # Circuit Breaker history is now tracked via contextvars isolated per-task
        
        try:
            self.last_bytes_sent = float(psutil.net_io_counters().bytes_sent)
        except Exception:
            self.last_bytes_sent = 0.0

    def _check_runaway_loop(self, provider: str, model: str, prompt_text: str = "", last_msg: str = "", messages_list: Optional[List[str]] = None) -> float:
        """
        Enterprise-grade context-isolated circuit breaker.
        Returns the number of seconds the caller should throttle (sleep), or raises an Exception.
        """
        if os.getenv("COMPUTECAPX_DISABLE_LOOP_CHECK") == "true":
            return 0.0

        if messages_list is None:
            messages_list = []

        history = _request_history.get()
        if history is None:
            history = collections.deque(maxlen=20)
            _request_history.set(history)

        current_time = time.time()
        
        # Prune old events to fix Context Leak across ThreadPools
        while history and current_time - history[0]["timestamp"] > 120.0:
            history.popleft()

        # Append detailed request info before checking loop condition
        history.append({
            "timestamp": current_time,
            "prompt": prompt_text,
            "last_msg": last_msg,
            "response": "",
            "sleep_duration": 0.0
        })

        # Heuristic checks: is a loop actually active?
        is_loop = _has_internal_message_repetition(messages_list) or _detect_loop_in_history(list(history))
        
        # If not flagged as a loop, let it pass freely (avoids false positives on batch jobs)
        if not is_loop:
            return 0.0

        def get_net_elapsed(start_idx: int) -> float:
            slice_list = list(history)[start_idx:]
            if not slice_list:
                return 0.0
            raw_elapsed = time.time() - slice_list[0]["timestamp"]
            total_sleep = sum(item.get("sleep_duration", 0.0) for item in slice_list[:-1])
            return max(0.0, raw_elapsed - total_sleep)

        count = len(history)
        if count >= 20:
            net_window = get_net_elapsed(0)
            if net_window < 60.0:  # Evaluated on net elapsed time (excluding warn sleeps)
                emergency_payload = {
                    "project_id": self.project_id,
                    "provider": provider,
                    "model_name": model,
                    "request_count": 20,
                    "time_window_seconds": int(net_window)
                }
                self.client._send_async("telemetry/ai/emergency", emergency_payload)
                _logger.critical(
                    "[ComputeCapX] CRITICAL: Runaway AI Loop detected. "
                    "20 requests to %s (%s) attempted in %.2fs. Task permanently blocked.",
                    provider.upper(), model, net_window
                )
                history.clear()
                raise ComputeCapRunawayLoopError(
                    f"CRITICAL: Runaway AI Loop detected in isolated context. "
                    f"20 requests to {provider.upper()} ({model}) attempted in {net_window:.2f}s. "
                    f"Task permanently blocked."
                )
                
        elif count >= 10:
            net_window = get_net_elapsed(count - 10)
            if net_window < 20.0:
                _logger.warning(
                    "[ComputeCapX] Rapid AI loop detected. Applying 5s mitigation throttle."
                )
                throttle_payload = {
                    "project_id": self.project_id,
                    "provider": provider,
                    "model_name": model,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_delta": 0.0,
                    "action": "throttled",
                    "reason": "Rapid AI Loop Detected (5s Warning)"
                }
                self.client._send_async("telemetry/ai", throttle_payload)
                history[-1]["sleep_duration"] = 5.0
                return 5.0
                
        elif count >= 5:
            net_window = get_net_elapsed(count - 5)
            if net_window < 10.0:
                _logger.warning(
                    "[ComputeCapX] Fast AI usage detected. Applying 2s warning throttle."
                )
                throttle_payload = {
                    "project_id": self.project_id,
                    "provider": provider,
                    "model_name": model,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cost_delta": 0.0,
                    "action": "throttled",
                    "reason": "Fast AI Usage Detected (2s Warning)"
                }
                self.client._send_async("telemetry/ai", throttle_payload)
                history[-1]["sleep_duration"] = 2.0
                return 2.0

        return 0.0

    def _check_budget_and_raise(self, provider: str = "unknown", model: str = "unknown") -> None:
        """Check the AI budget limit and raise ComputeCapBudgetExceededError if exceeded."""
        if not self.client.check_budget_sync(self.project_id, provider=provider, model=model):
            _logger.critical(
                "[ComputeCapX] CRITICAL: Monthly AI budget limit reached. "
                "Request blocked to prevent cost overruns. "
                "Update your budget limit in the dashboard to continue."
            )
            raise ComputeCapBudgetExceededError("ComputeCap Active Block: Project budget limit exceeded.")

    def _transmit(self, provider: str, model: str, t_in: int, t_out: int):
        try:
            _logger.debug(
                "[ComputeCapX] AI request: %s | model: %s | tokens_in: %d | tokens_out: %d",
                provider.upper(), model, t_in, t_out
            )
            telemetry_payload = {
                "project_id": self.project_id,
                "trace_id": _get_active_trace(),
                "provider": provider,
                "model_name": model,
                "tokens_in": int(t_in),
                "tokens_out": int(t_out),
                "cost_delta": 0.0, 
                "metadata": {
                    "resource_id": self.environment.get("resource_id", "unknown"),
                    "env": self.environment.get("provider", "local"),
                    "region": self.environment.get("region", "local")
                }
            }
            self.client.record_ai_telemetry(telemetry_payload)
        except Exception:
            pass

    def _get_top_processes(self) -> List[Dict[str, Any]]:
        top_procs = []
        try:
            procs = []
            for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    info = p.info
                    procs.append({
                        "pid": info.get('pid', 0),
                        "name": str(info.get('name', 'unknown')),
                        "cpu_percent": float(info.get('cpu_percent') or 0.0),
                        "memory_percent": float(info.get('memory_percent') or 0.0)
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            procs = sorted(procs, key=lambda x: x['cpu_percent'] + x['memory_percent'], reverse=True)
            top_procs = procs[:3]
        except Exception:
            pass
        return top_procs

    def _heartbeat_loop(self, interval_seconds: int = 300) -> None:
        pulses_sent = 0
        while True:
            cpu_util = 0.0
            try:
                cpu_util = float(psutil.cpu_percent(interval=None))
            except Exception:
                pass
                
            ram_util = 0.0
            try:
                ram_util = float(psutil.virtual_memory().percent)
            except Exception:
                pass
                
            disk_util = 0.0
            try:
                disk_util = float(psutil.disk_usage(os.path.abspath(os.sep)).percent)
            except Exception:
                pass
                
            cpu_iowait = 0.0
            try:
                cpu_times = psutil.cpu_times_percent(interval=None)
                cpu_iowait = float(getattr(cpu_times, 'iowait', 0.0))
            except Exception:
                pass
                
            top_processes = []
            try:
                top_processes = self._get_top_processes()
            except Exception:
                pass
                
            egress_delta = 0.0
            try:
                current_bytes = float(psutil.net_io_counters().bytes_sent)
                egress_delta = current_bytes - self.last_bytes_sent
                self.last_bytes_sent = current_bytes
            except Exception:
                pass
                
            try:
                payload = {
                    "project_id": self.project_id,
                    "provider": self.environment.get("provider", "local"),
                    "resource_id": self.environment.get("resource_id", "unknown"),
                    "status": "ACTIVE",
                    "instance_type": self.environment.get("instance_type", "unknown"), 
                    "region": self.environment.get("region", "us-east-1"),
                    "os_type": self.os_type,
                    "cpu_utilization": cpu_util,
                    "ram_utilization": ram_util,
                    "disk_utilization": disk_util,
                    "cpu_iowait": cpu_iowait,
                    "network_egress": max(0.0, egress_delta),
                    "top_processes": top_processes
                }
                self.client.record_cloud_telemetry(payload)
                pulses_sent += 1
            except Exception:
                pass
            finally:
                try:
                    if interval_seconds <= 0:
                        break
                    # Send a follow-up heartbeat at 15 seconds to capture post-startup metrics,
                    # then settle into the regular interval.
                    sleep_time = 15 if pulses_sent == 1 else interval_seconds
                    time.sleep(sleep_time)
                except Exception:
                    pass

    def claim_resource(self, continuous: bool = True) -> None:
        if continuous:
            thread = threading.Thread(target=self._heartbeat_loop, args=(300,), daemon=True)
            thread.start()
        else:
            try:
                self._heartbeat_loop(interval_seconds=0)
            except Exception:
                pass

    def instrument_gemini(self, module: Any) -> bool:
        """Patches the Google Gemini Python SDK to intercept generate_content calls for cost tracking."""
        patched_anything = False
        try:
            if hasattr(module, "GenerativeModel"):
                original_generate = module.GenerativeModel.generate_content

                @functools.wraps(original_generate)
                def intercepted_generate(self_model, *args, **kwargs):
                    model_name = getattr(self_model, 'model_name', 'unknown').replace("models/", "")
                    
                    contents = args[0] if args else kwargs.get("contents", None)
                    p_text, l_msg, m_list = _extract_gemini_details(contents)
                    throttle = self._check_runaway_loop("google", model_name, p_text, l_msg, m_list)
                    if throttle > 0:
                        time.sleep(throttle)
                    self._check_budget_and_raise(provider="google", model=model_name)
                        
                    response = original_generate(self_model, *args, **kwargs)
                    try:
                        resp_text = getattr(response, "text", "")
                        history = _request_history.get()
                        if history:
                            history[-1]["response"] = resp_text

                        usage = getattr(response, 'usage_metadata', None)
                        if usage:
                            self._transmit("google", model_name, getattr(usage, 'prompt_token_count', 0), getattr(usage, 'candidates_token_count', 0))
                    except Exception:
                        pass
                    return response

                module.GenerativeModel.generate_content = intercepted_generate
                patched_anything = True

                if hasattr(module.GenerativeModel, "generate_content_async"):
                    original_generate_async = module.GenerativeModel.generate_content_async

                    @functools.wraps(original_generate_async)
                    async def intercepted_generate_async(self_model, *args, **kwargs):
                        model_name = getattr(self_model, 'model_name', 'unknown').replace("models/", "")
                        
                        contents = args[0] if args else kwargs.get("contents", None)
                        p_text, l_msg, m_list = _extract_gemini_details(contents)
                        throttle = self._check_runaway_loop("google", model_name, p_text, l_msg, m_list)
                        if throttle > 0:
                            await asyncio.sleep(throttle)
                        self._check_budget_and_raise(provider="google", model=model_name)
                            
                        response = await original_generate_async(self_model, *args, **kwargs)
                        try:
                            resp_text = getattr(response, "text", "")
                            history = _request_history.get()
                            if history:
                                history[-1]["response"] = resp_text

                            usage = getattr(response, 'usage_metadata', None)
                            if usage:
                                self._transmit("google", model_name, getattr(usage, 'prompt_token_count', 0), getattr(usage, 'candidates_token_count', 0))
                        except Exception:
                            pass
                        return response

                    module.GenerativeModel.generate_content_async = intercepted_generate_async

        except Exception:
            pass
        return patched_anything

    def instrument_non_http_sdks(self) -> None:
        """
        Instruments non-HTTP SDKs (such as Gemini) via sys.meta_path hooks.
        This avoids brittle builtins patching and does not require the target package
        to be installed at import time.
        """
        try:
            if "google.generativeai" in sys.modules:
                genai_mod = sys.modules["google.generativeai"]
                if not getattr(genai_mod, "_computecap_patched", False):
                    if self.instrument_gemini(genai_mod):
                        setattr(genai_mod, "_computecap_patched", True)

            import importlib.abc
            import importlib.util

            telemetry_engine = self

            class GeminiPostImportFinder(importlib.abc.MetaPathFinder):
                def find_spec(self_, fullname, path, target=None):
                    if fullname == "google.generativeai":
                        sys.meta_path.remove(self_)
                        try:
                            spec = importlib.util.find_spec(fullname)
                            if spec and spec.loader:
                                original_loader = spec.loader
                                
                                class PostImportLoader(importlib.abc.Loader):
                                    def create_module(self__, spec):
                                        if hasattr(original_loader, 'create_module'):
                                            return original_loader.create_module(spec)
                                        return None

                                    def exec_module(self__, module):
                                        original_loader.exec_module(module)
                                        if not getattr(module, "_computecap_patched", False):
                                            if telemetry_engine.instrument_gemini(module):
                                                setattr(module, "_computecap_patched", True)

                                spec.loader = PostImportLoader()
                            return spec
                        finally:
                            sys.meta_path.insert(0, self_)
                    return None

            sys.meta_path.insert(0, GeminiPostImportFinder())
        except Exception:
            pass

    def instrument_universal_http(self) -> None:
        """Universal Interceptor for all REST-based AI APIs."""

        def _parse_sse_usage(content_bytes: bytes) -> Tuple[int, int]:
            """
            Parse a fully-buffered SSE stream and extract token usage.

            Strategy (in priority order):
              1. Explicit `usage` object in any chunk — exact counts.
                 Works for: Anthropic always, OpenAI/Groq/Mistral when
                 stream_options={"include_usage": true} is passed.
              2. Anthropic event types: message_start (input) + message_delta (output).
              3. Fallback: count chars in delta content across all chunks,
                 then estimate output tokens as chars // 4.
                 Works for: OpenAI streaming WITHOUT include_usage (default).

            Returns (input_tokens, output_tokens).
            """
            t_in, t_out = 0, 0
            output_chars = 0  # accumulated delta content length for estimation
            try:
                text = content_bytes.decode("utf-8", errors="ignore")
                for line in text.splitlines():
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                        # ── Explicit usage (OpenAI with include_usage, Groq, Mistral) ──
                        usage = chunk.get("usage")
                        if usage and isinstance(usage, dict):
                            t_in = usage.get("prompt_tokens") or usage.get("input_tokens") or t_in
                            t_out = usage.get("completion_tokens") or usage.get("output_tokens") or t_out
                        # ── OpenAI / Groq delta content → output char counting ──
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            if content and isinstance(content, str):
                                output_chars += len(content)
                        # ── Anthropic: message_start → input_tokens ──
                        if chunk.get("type") == "message_start":
                            msg_usage = chunk.get("message", {}).get("usage", {})
                            if isinstance(msg_usage, dict):
                                t_in = msg_usage.get("input_tokens") or t_in
                        # ── Anthropic: message_delta → output_tokens ──
                        if chunk.get("type") == "message_delta":
                            delta_usage = chunk.get("usage", {})
                            if isinstance(delta_usage, dict):
                                t_out = delta_usage.get("output_tokens") or t_out
                        # ── Anthropic: content_block_delta text → output char counting ──
                        if chunk.get("type") == "content_block_delta":
                            delta = chunk.get("delta") or {}
                            if delta.get("type") == "text_delta":
                                output_chars += len(delta.get("text") or "")
                    except (json.JSONDecodeError, AttributeError):
                        continue
            except Exception:
                pass
            # If we still have no explicit output count, estimate from collected chars
            if t_out == 0 and output_chars > 0:
                t_out = max(1, output_chars // 4)
            return t_in, t_out

        def _estimate_input_tokens(body_bytes: bytes) -> int:
            """
            Estimate input token count from request body messages.
            Used as a fallback for streaming calls where the SSE stream
            does not include usage data (OpenAI default without include_usage).
            Approximation: 1 token ≈ 4 characters.
            """
            try:
                body = json.loads(body_bytes)
                messages = body.get("messages") or []
                total_chars = 0
                for msg in messages:
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        total_chars += len(content)
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict):
                                total_chars += len(str(part.get("text") or part.get("content") or ""))
                # Also count system prompt if present at top level
                system = body.get("system")
                if isinstance(system, str):
                    total_chars += len(system)
                return max(1, total_chars // 4) if total_chars > 0 else 0
            except Exception:
                return 0

        def _extract_ai_context(url_str: str, body_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
            try:
                body = json.loads(body_bytes)
                model_name = body.get("model")
                if not model_name:
                    return None, None 
                    
                parsed_url = urllib.parse.urlparse(url_str)
                hostname = parsed_url.hostname or ""
                
                if "googleapis.com" in hostname:
                    provider = "google"
                else:
                    domain_parts = hostname.split(".")
                    if len(domain_parts) >= 2:
                        provider = domain_parts[-2] 
                    else:
                        provider = hostname
                return provider, model_name
            except Exception:
                return None, None

        try:
            import httpx
            original_httpx_send = httpx.Client.send

            @functools.wraps(original_httpx_send)
            def intercepted_httpx_send(client_self, request, *args, **kwargs):
                provider, model = None, None
                if request.method == "POST":
                    try:
                        # Safely read body without consuming streaming iterators
                        body_bytes = b""
                        if hasattr(request, "stream") and isinstance(request.stream, httpx.ByteStream):
                            body_bytes = request.read()
                        if body_bytes:
                            provider, model = _extract_ai_context(str(request.url).lower(), body_bytes)
                            if provider and model:
                                p_text, l_msg, m_list = _extract_prompt_details(body_bytes)
                                throttle = self._check_runaway_loop(provider, model, p_text, l_msg, m_list)
                                if throttle > 0:
                                    time.sleep(throttle)
                                self._check_budget_and_raise(provider=provider, model=model)
                    except (ComputeCapRunawayLoopError, ComputeCapBudgetExceededError) as e:
                        raise e
                    except Exception:
                        pass
                
                response = original_httpx_send(client_self, request, *args, **kwargs)
                
                if provider and model:
                    try:
                        content_type = response.headers.get("content-type", "")
                        if response.status_code == 200 and "application/json" in content_type:
                            res_bytes = response.read()
                            data = json.loads(res_bytes)
                            resp_text = _extract_response_text(data)
                            history = _request_history.get()
                            if history:
                                history[-1]["response"] = resp_text
                            usage = data.get("usage") or data.get("usageMetadata") or data.get("x_groq", {}).get("usage")
                            if usage:
                                t_in = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                                t_out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                                self._transmit(provider, model, t_in, t_out)
                        elif response.status_code == 200 and "text/event-stream" in content_type:
                            # Streaming response — buffer full SSE and parse usage
                            stream_bytes = response.read()
                            t_in, t_out = _parse_sse_usage(stream_bytes)
                            # Fallback: OpenAI default streaming has no usage in SSE
                            # — estimate input tokens from request body
                            if t_in == 0:
                                t_in = _estimate_input_tokens(body_bytes)
                            if t_in > 0 or t_out > 0:
                                self._transmit(provider, model, t_in, t_out)
                    except Exception:
                        pass
                return response

            httpx.Client.send = intercepted_httpx_send  # type: ignore
            
            original_httpx_send_async = httpx.AsyncClient.send
            
            @functools.wraps(original_httpx_send_async)
            async def intercepted_httpx_send_async(client_self, request, *args, **kwargs):
                provider, model = None, None
                if request.method == "POST":
                    try:
                        # Safely read body without consuming streaming iterators
                        body_bytes = b""
                        if hasattr(request, "stream") and isinstance(request.stream, httpx.ByteStream):
                            body_bytes = request.read()
                        if body_bytes:
                            provider, model = _extract_ai_context(str(request.url).lower(), body_bytes)
                            if provider and model:
                                p_text, l_msg, m_list = _extract_prompt_details(body_bytes)
                                throttle = self._check_runaway_loop(provider, model, p_text, l_msg, m_list)
                                if throttle > 0:
                                    await asyncio.sleep(throttle)
                                self._check_budget_and_raise(provider=provider, model=model)
                    except (ComputeCapRunawayLoopError, ComputeCapBudgetExceededError) as e:
                        raise e
                    except Exception:
                        pass
                
                response = await original_httpx_send_async(client_self, request, *args, **kwargs)
                
                if provider and model:
                    try:
                        content_type = response.headers.get("content-type", "")
                        if response.status_code == 200 and "application/json" in content_type:
                            res_bytes = await response.aread()
                            data = json.loads(res_bytes)
                            resp_text = _extract_response_text(data)
                            history = _request_history.get()
                            if history:
                                history[-1]["response"] = resp_text
                            usage = data.get("usage") or data.get("usageMetadata") or data.get("x_groq", {}).get("usage")
                            if usage:
                                t_in = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                                t_out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                                self._transmit(provider, model, t_in, t_out)
                        elif response.status_code == 200 and "text/event-stream" in content_type:
                            # Streaming response — buffer full SSE and parse usage
                            stream_bytes = await response.aread()
                            t_in, t_out = _parse_sse_usage(stream_bytes)
                            # Fallback: estimate input tokens from request body
                            if t_in == 0:
                                t_in = _estimate_input_tokens(body_bytes)
                            if t_in > 0 or t_out > 0:
                                self._transmit(provider, model, t_in, t_out)
                    except Exception:
                        pass
                return response

            httpx.AsyncClient.send = intercepted_httpx_send_async  # type: ignore
            
        except ImportError:
            pass

        try:
            import requests
            original_requests_send = requests.Session.send

            @functools.wraps(original_requests_send)
            def intercepted_requests_send(client_self, request, *args, **kwargs):
                provider, model = None, None
                if request.method == "POST" and request.body:
                    try:
                        body_bytes = request.body if isinstance(request.body, bytes) else str(request.body).encode("utf-8")
                        provider, model = _extract_ai_context(str(request.url).lower(), body_bytes)
                        if provider and model:
                            p_text, l_msg, m_list = _extract_prompt_details(body_bytes)
                            wrapper = _global_telemetry_engine
                            if wrapper:
                                throttle = wrapper._check_runaway_loop(provider, model, p_text, l_msg, m_list)
                                if throttle > 0:
                                    time.sleep(throttle)
                                wrapper._check_budget_and_raise(provider=provider, model=model)
                    except (ComputeCapRunawayLoopError, ComputeCapBudgetExceededError) as e:
                        raise e
                    except Exception:
                        pass
                
                response = original_requests_send(client_self, request, *args, **kwargs)
                
                if provider and model:
                    try:
                        content_type = response.headers.get("content-type", "")
                        if response.status_code == 200 and "application/json" in content_type:
                            data = response.json()
                            usage = (
                                data.get("usage")
                                or data.get("usageMetadata")
                                or (data.get("x_groq") or {}).get("usage")
                            )
                            resp_text = _extract_response_text(data)
                            history = _request_history.get()
                            if history:
                                history[-1]["response"] = resp_text
                            if usage and _global_telemetry_engine:
                                t_in = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                                t_out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                                _global_telemetry_engine._transmit(provider, model, t_in, t_out)
                        elif response.status_code == 200 and "text/event-stream" in content_type:
                            # Streaming response — buffer full SSE and parse usage
                            stream_bytes = response.content  # .content buffers the full stream
                            t_in, t_out = _parse_sse_usage(stream_bytes)
                            # Fallback: estimate input tokens from request body
                            if t_in == 0:
                                t_in = _estimate_input_tokens(body_bytes)
                            if (t_in > 0 or t_out > 0) and _global_telemetry_engine:
                                _global_telemetry_engine._transmit(provider, model, t_in, t_out)
                    except Exception:
                        pass
                return response

            requests.Session.send = intercepted_requests_send  # type: ignore
        except ImportError:
            pass

        try:
            import urllib3
            original_urlopen = urllib3.connectionpool.HTTPConnectionPool.urlopen

            def intercepted_urlopen(self_pool, method, url, body=None, headers=None, *args, **kwargs):
                # Skip interception of outbound telemetry requests to the ComputeCapX backend.
                wrapper = _global_telemetry_engine
                if wrapper and wrapper.client:
                    import urllib.parse
                    parsed = urllib.parse.urlparse(wrapper.client.backend_url)
                    if self_pool.host == parsed.hostname:
                        return original_urlopen(self_pool, method, url, body=body, headers=headers, *args, **kwargs)

                start_time = time.time()
                
                # Snapshot Disk I/O Before
                io_before_read = 0
                io_before_write = 0
                try:
                    io_counters = psutil.Process().io_counters()
                    io_before_read = io_counters.read_bytes
                    io_before_write = io_counters.write_bytes
                except Exception:
                    pass
                    
                bytes_out = 0
                if body:
                    if isinstance(body, bytes): bytes_out = len(body)
                    elif isinstance(body, str): bytes_out = len(body.encode('utf-8'))
                
                bytes_in = 0
                status = "success"
                try:
                    response = original_urlopen(self_pool, method, url, body=body, headers=headers, *args, **kwargs)
                    # Capture exact egress/ingress for Micro-Attribution
                    if hasattr(response, 'data') and response.data:
                        bytes_in = len(response.data)
                    else:
                        cl = response.headers.get("content-length")
                        if cl and cl.isdigit():
                            bytes_in = int(cl)
                except Exception as e:
                    status = f"error: {str(e)}"
                    raise
                finally:
                    latency_ms = (time.time() - start_time) * 1000
                    
                    # Snapshot Disk I/O After
                    io_read_delta = 0
                    io_write_delta = 0
                    try:
                        io_counters = psutil.Process().io_counters()
                        io_read_delta = io_counters.read_bytes - io_before_read
                        io_write_delta = io_counters.write_bytes - io_before_write
                    except Exception:
                        pass
                    
                    wrapper = _global_telemetry_engine
                    if wrapper:
                        trace_payload = {
                            "project_id": wrapper.project_id,
                            "provider": wrapper.environment.get("provider", "local"),
                            "resource_id": wrapper.environment.get("resource_id", "local"),
                            "region": wrapper.environment.get("region", "unknown"),
                            "trace_id": _get_active_trace(),
                            "event_type": "network_call",
                            "service": f"{self_pool.scheme}://{self_pool.host}:{self_pool.port}",
                            "method": method,
                            "url": url,
                            "latency_ms": latency_ms,
                            "bytes_out": bytes_out,
                            "bytes_in": bytes_in,
                            "io_read_bytes": io_read_delta,
                            "io_write_bytes": io_write_delta,
                            "status": status
                        }
                        wrapper.client.record_trace_event(trace_payload)

                return response

            urllib3.connectionpool.HTTPConnectionPool.urlopen = intercepted_urlopen
        except ImportError:
            pass

        # ---------------------------------------------------------
        # Database Query Interceptors
        # ---------------------------------------------------------
        try:
            import sqlite3
            class ComputeCapSQLiteCursor(sqlite3.Cursor):
                def execute(self, sql, *args, **kwargs):
                    start_time = time.time()
                    
                    io_before_read = 0
                    io_before_write = 0
                    try:
                        io_counters = psutil.Process().io_counters()
                        io_before_read = io_counters.read_bytes
                        io_before_write = io_counters.write_bytes
                    except Exception:
                        pass
                        
                    status = "success"
                    try:
                        return super().execute(sql, *args, **kwargs)
                    except Exception as e:
                        status = f"error: {str(e)}"
                        raise
                    finally:
                        latency_ms = (time.time() - start_time) * 1000
                        io_read_delta = 0
                        io_write_delta = 0
                        try:
                            io_counters = psutil.Process().io_counters()
                            io_read_delta = io_counters.read_bytes - io_before_read
                            io_write_delta = io_counters.write_bytes - io_before_write
                        except Exception:
                            pass
                            
                        wrapper = _global_telemetry_engine
                        if wrapper:
                            trace_payload = {
                                "project_id": wrapper.project_id,
                                "provider": wrapper.environment.get("provider", "local"),
                                "resource_id": wrapper.environment.get("resource_id", "local"),
                                "region": wrapper.environment.get("region", "unknown"),
                                "trace_id": _get_active_trace(),
                                "event_type": "database_call",
                                "service": "sqlite3",
                                "method": "execute",
                                "query": str(sql)[:100], 
                                "rowcount": self.rowcount,
                                "io_read_bytes": io_read_delta,
                                "io_write_bytes": io_write_delta,
                                "latency_ms": latency_ms,
                                "status": status
                            }
                            wrapper.client.record_trace_event(trace_payload)
                            
            class ComputeCapSQLiteConnection(sqlite3.Connection):
                def cursor(self, factory=ComputeCapSQLiteCursor):
                    return super().cursor(factory)
                    
            original_sqlite_connect = sqlite3.connect
            def intercepted_sqlite_connect(*args, **kwargs):
                kwargs.setdefault('factory', ComputeCapSQLiteConnection)
                return original_sqlite_connect(*args, **kwargs)
                
            sqlite3.connect = intercepted_sqlite_connect
        except ImportError:
            pass

        try:
            import psycopg2
            from psycopg2.extensions import cursor as _psycopg2_cursor
            
            class ComputeCapPsycopg2Cursor(_psycopg2_cursor):
                def execute(self, query, vars=None):
                    start_time = time.time()
                    
                    io_before_read = 0
                    io_before_write = 0
                    try:
                        io_counters = psutil.Process().io_counters()
                        io_before_read = io_counters.read_bytes
                        io_before_write = io_counters.write_bytes
                    except Exception:
                        pass
                        
                    status = "success"
                    try:
                        return super().execute(query, vars)
                    except Exception as e:
                        status = f"error: {str(e)}"
                        raise
                    finally:
                        latency_ms = (time.time() - start_time) * 1000
                        io_read_delta = 0
                        io_write_delta = 0
                        try:
                            io_counters = psutil.Process().io_counters()
                            io_read_delta = io_counters.read_bytes - io_before_read
                            io_write_delta = io_counters.write_bytes - io_before_write
                        except Exception:
                            pass
                            
                        wrapper = _global_telemetry_engine
                        if wrapper:
                            trace_payload = {
                                "project_id": wrapper.project_id,
                                "provider": wrapper.environment.get("provider", "local"),
                                "resource_id": wrapper.environment.get("resource_id", "local"),
                                "region": wrapper.environment.get("region", "unknown"),
                                "trace_id": _get_active_trace(),
                                "event_type": "database_call",
                                "service": "postgresql",
                                "method": "execute",
                                "query": str(query)[:100],
                                "rowcount": getattr(self, 'rowcount', 0),
                                "io_read_bytes": io_read_delta,
                                "io_write_bytes": io_write_delta,
                                "latency_ms": latency_ms,
                                "status": status
                            }
                            wrapper.client.record_trace_event(trace_payload)
                            
            original_psycopg2_connect = psycopg2.connect
            def intercepted_psycopg2_connect(*args, **kwargs):
                kwargs.setdefault('cursor_factory', ComputeCapPsycopg2Cursor)
                return original_psycopg2_connect(*args, **kwargs)
                
            psycopg2.connect = intercepted_psycopg2_connect
        except ImportError:
            pass

        patched_anything = True
        
    def instrument_crash_handler(self) -> None:
        """Hooks sys.excepthook to catch fatal crashes and emit a trace_end event."""
        original_excepthook = sys.excepthook
        
        def intercepted_excepthook(exc_type, exc_value, exc_traceback):
            try:
                if _global_telemetry_engine:
                    wrapper = _global_telemetry_engine
                    # Emit internal error event
                    err_payload = {
                        "project_id": wrapper.project_id,
                        "provider": wrapper.environment.get("provider", "local"),
                        "resource_id": wrapper.environment.get("resource_id", "local"),
                        "region": wrapper.environment.get("region", "unknown"),
                        "trace_id": _get_active_trace(),
                        "event_type": "internal_error",
                        "service": "python_runtime",
                        "method": "excepthook",
                        "status": f"FATAL CRASH: {exc_type.__name__}: {str(exc_value)}",
                        "latency_ms": 0,
                    }
                    wrapper.client.record_trace_event(err_payload)
                    
                    # Emit a trace_end event to mark the crash as terminal.
                    end_payload = {
                        "project_id": wrapper.project_id,
                        "provider": wrapper.environment.get("provider", "local"),
                        "resource_id": wrapper.environment.get("resource_id", "local"),
                        "region": wrapper.environment.get("region", "unknown"),
                        "trace_id": _get_active_trace(),
                        "event_type": "trace_end",
                        "status": "fatal_crash"
                    }
                    wrapper.client.record_trace_event(end_payload)
            except Exception:
                pass
            
            # Call original excepthook to not swallow the exception
            original_excepthook(exc_type, exc_value, exc_traceback)
            
        sys.excepthook = intercepted_excepthook

def instrument(
    api_key: Optional[str] = None,
    project_id: Optional[str] = None,
    claim_infrastructure: bool = True,
    instrument_ai: bool = True,
    backend_url: Optional[str] = None,
) -> ComputeCapClient:
    """
    Activate ComputeCapX telemetry instrumentation.

    Credentials are resolved in this order:
      1. Direct argument (``api_key``, ``project_id``)
      2. Environment variables (``COMPUTECAPX_API_KEY``, ``COMPUTECAPX_PROJECT_ID``)
      3. CLI config file persisted by ``computecapx login``

    This means you can call ``computecapx.instrument()`` with no arguments
    if the environment variables are set (e.g. via a ``.env`` file loaded
    by ``python-dotenv``).

    Args:
        api_key: Your ComputeCapX SDK API key.
        project_id: Your ComputeCapX project ID.
        claim_infrastructure: Send cloud infrastructure heartbeats. Disable for local dev.
        instrument_ai: Intercept AI API calls and enforce budget/loop policies.
        backend_url: Override the backend URL (for self-hosted instances).

    Returns:
        A configured :class:`ComputeCapClient` instance.
    """
    from .client import _load_persisted_config
    stored_config = _load_persisted_config()

    # Credential resolution: direct arg -> env var -> CLI config file
    resolved_api_key: Optional[str] = (
        api_key
        or os.getenv("COMPUTECAPX_API_KEY")
        or stored_config.get("api_key")
    )
    resolved_project_id: str = (
        project_id
        or os.getenv("COMPUTECAPX_PROJECT_ID")
        or stored_config.get("project_id")
        or ""
    )

    global _global_telemetry_engine
    client = ComputeCapClient(api_key=resolved_api_key, backend_url=backend_url)
    telemetry_engine = ComputeCapTelemetry(client, resolved_project_id)
    _global_telemetry_engine = telemetry_engine

    if claim_infrastructure:
        telemetry_engine.claim_resource(continuous=True)

    if instrument_ai:
        telemetry_engine.instrument_universal_http()
        telemetry_engine.instrument_crash_handler()

        # Catch any non-HTTP SDKs (like Gemini) via dynamic import hook
        telemetry_engine.instrument_non_http_sdks()

    return client