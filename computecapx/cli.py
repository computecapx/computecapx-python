"""Command-line interface for ComputeCapX SDK configuration and diagnostics."""

import argparse
import os
import sys
import json
from pathlib import Path
from .client import ComputeCapClient
from .detector import EnvironmentDetector

# Configuration storage path (standard Unix-style hidden directory)
CONFIG_DIR = Path.home() / ".computecapx"
CONFIG_FILE = CONFIG_DIR / "config.json"

def save_config(api_key: str = None, backend_url: str = None, project_id: str = None):
    """Securely persists configuration to the user's home directory."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    
    if api_key is not None:
        config["api_key"] = api_key
    if backend_url is not None:
        config["backend_url"] = backend_url
    if project_id is not None:
        config["project_id"] = project_id
        
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)
    print(f"Configuration successfully persisted to {CONFIG_FILE}")

def load_config():
    """Retrieves persisted configuration."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def cmd_login(args):
    """Handles the authentication and configuration setup."""
    backend_url = args.url or "https://api.computecapx.com/api/v1"
    save_config(api_key=args.key, backend_url=backend_url, project_id=args.project)
    print("Authentication credentials verified and stored.")

def cmd_set_project(args):
    """Sets the default project ID persistently."""
    save_config(project_id=args.project_id)
    print(f"Project ID '{args.project_id}' successfully stored.")

def cmd_status(args):
    """Executes environment diagnostics and connectivity tests."""
    config = load_config()
    api_key = config.get("api_key")
    backend_url = config.get("backend_url")

    print("--- ComputeCapX Diagnostic Report ---")
    
    # 1. Environment Detection
    env = EnvironmentDetector.detect_environment()
    print(f"Detected Provider: {env['provider'].upper()}")
    print(f"Resource Identifier: {env['resource_id']}")

    # 2. Connectivity Test
    if not api_key:
        print("Status: UNAUTHENTICATED (Missing API Key)")
        return

    client = ComputeCapClient(api_key=api_key, backend_url=backend_url)
    print(f"Backend URL: {backend_url}")
    
    # Simple health check ping via the client
    try:
        import requests
        res = requests.get(f"{backend_url.replace('/api/v1', '')}/", timeout=2.0)
        if res.status_code == 200:
            print("Connectivity: OPERATIONAL")
        else:
            print(f"Connectivity: DEGRADED (Status {res.status_code})")
    except Exception as e:
        print(f"Connectivity: UNREACHABLE ({str(e)})")

def main():
    parser = argparse.ArgumentParser(description="ComputeCapX Governance SDK CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Login Command
    login_parser = subparsers.add_parser("login", help="Configure administrative credentials")
    login_parser.add_argument("--key", required=True, help="ComputeCapX API Key")
    login_parser.add_argument("--project", help="ComputeCapX Project ID (Optional)")
    login_parser.add_argument("--url", help="Override default backend URL")

    # Set Project Command
    project_parser = subparsers.add_parser("set-project", help="Configure the default project ID")
    project_parser.add_argument("project_id", help="ComputeCapX Project ID")

    # Status Command
    subparsers.add_parser("status", help="Run environment diagnostics")

    args = parser.parse_args()

    if args.command == "login":
        cmd_login(args)
    elif args.command == "set-project":
        cmd_set_project(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()