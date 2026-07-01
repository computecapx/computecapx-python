"""Zero-code CLI execution wrapper for the ComputeCapX SDK."""

import sys
import os
import runpy
import importlib.util
from typing import List

from computecapx.client import _load_persisted_config
from computecapx.wrapper import instrument

def main():
    if len(sys.argv) < 2:
        print("Usage: computecapx-run [--no-cloud] [--no-ai] [--no-loop] <script.py> [args...]")
        print("   OR: computecapx-run [--no-cloud] [--no-ai] [--no-loop] python <script.py> [args...]")
        sys.exit(1)

    target_args = sys.argv[1:]
    
    no_cloud = False
    no_ai = False
    
    while target_args:
        if target_args[0] == "--no-cloud":
            no_cloud = True
            target_args.pop(0)
        elif target_args[0] == "--no-ai":
            no_ai = True
            target_args.pop(0)
        elif target_args[0] == "--no-loop":
            os.environ["COMPUTECAPX_DISABLE_LOOP_CHECK"] = "true"
            target_args.pop(0)
        elif target_args[0] in ("python", "python3"):
            target_args.pop(0)
        else:
            break

    if not target_args:
        print("Error: No target Python script specified.")
        sys.exit(1)

    target = target_args[0]
    is_file = os.path.exists(target)
    
    if not is_file:
        # Check if they are trying to run a global binary module (like uvicorn or gunicorn)
        try:
            if not importlib.util.find_spec(target):
                print(f"Error: Target script or module '{target}' not found.")
                sys.exit(1)
        except ValueError:
            print(f"Error: Invalid module structure or target '{target}' not found.")
            sys.exit(1)

    stored_config = _load_persisted_config()
    api_key = os.getenv("COMPUTECAPX_API_KEY") or stored_config.get("api_key")
    project_id = os.getenv("COMPUTECAPX_PROJECT_ID") or stored_config.get("project_id")
    backend_url = os.getenv("COMPUTECAPX_BACKEND_URL") or stored_config.get("backend_url")

    if not api_key or not project_id:
        print("[COMPUTECAPX] Warning: Missing API Key or Project ID. Telemetry will not be recorded.")
        _execute_script(target, is_file, target_args)
        return

    # Auto-disable cloud instrumentation if running locally and not explicitly overridden
    if not no_cloud:
        from computecapx.detector import EnvironmentDetector
        env = EnvironmentDetector.detect_environment()
        if env.get("provider") == "local":
            print("[COMPUTECAPX] Local environment detected. Auto-disabling Cloud Telemetry heartbeat.")
            no_cloud = True

    # Activate SDK telemetry before executing the target script
    instrument(
        api_key=api_key,
        project_id=project_id,
        backend_url=backend_url,
        claim_infrastructure=not no_cloud,
        instrument_ai=not no_ai
    )

    # Execute target script or module in the globally patched environment
    _execute_script(target, is_file, target_args)

def _execute_script(target: str, is_file: bool, target_args: List[str]):
    """Helper to execute the target Python script or module inside the current thread space."""
    sys.argv = target_args
    original_path = list(sys.path)
    if is_file:
        target_dir = os.path.dirname(os.path.abspath(target))
        if target_dir not in sys.path:
            sys.path.insert(0, target_dir)
    try:
        if is_file:
            runpy.run_path(target, run_name="__main__")
        else:
            runpy.run_module(target, run_name="__main__", alter_sys=True)
    finally:
        sys.path = original_path

if __name__ == "__main__":
    main()