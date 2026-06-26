# ComputeCapX Python SDK

[![PyPI version](https://badge.fury.io/py/computecapx.svg)](https://badge.fury.io/py/computecapx)
[![Python versions](https://img.shields.io/pypi/pyversions/computecapx.svg)](https://pypi.org/project/computecapx/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

The official Python SDK for [ComputeCapX](https://computecapx.com) — enterprise FinOps and AI governance. Monitor AI API costs, enforce budget policies, detect runaway agent loops, and stream real-time infrastructure telemetry across every major cloud provider — with **zero code changes** to your existing AI calls.

---

## Features

- **🔍 Universal AI Instrumentation** — Automatically intercepts OpenAI, Anthropic, Google Gemini, Groq, Mistral, Cohere, and any HTTP-based LLM API. No code changes required.
- **💰 Real-time Budget Enforcement** — Blocks API calls the moment a project exceeds its budget limit. The block is synchronous and cannot be bypassed.
- **🔄 Runaway Loop Circuit Breaker** — Detects and throttles AI agent feedback loops with three escalation tiers before they cause financial damage.
- **☁️ Multi-Cloud Infrastructure Telemetry** — Auto-detects AWS, GCP, Azure, Oracle Cloud, DigitalOcean, Vercel, and Netlify via metadata APIs with no configuration.
- **📊 Distributed Tracing** — Captures network calls (urllib3), database queries (SQLite, PostgreSQL), and AI call chains for full cost attribution per trace.
- **💥 Crash Handler** — Hooks `sys.excepthook` to emit a final trace on fatal errors so every execution has a complete record.
- **🚀 Zero-Code CLI Mode** — Use `computecapx-run` to instrument any existing script or server without touching its source.
- **⚡ Non-Blocking** — All telemetry is batched and sent asynchronously. Zero impact on your application latency.

---

## Installation

```bash
pip install computecapx
```

**Requirements:** Python 3.8+

---

## Quick Start

### Option 1: Environment Variables (Recommended)

Set your credentials once — the SDK picks them up automatically.

**.env file:**
```env
COMPUTECAPX_API_KEY=your_api_key_here
COMPUTECAPX_PROJECT_ID=your_project_id_here
```

**your_script.py:**
```python
from dotenv import load_dotenv
load_dotenv()

import computecapx
computecapx.instrument()  # reads from env vars automatically

# All AI calls below are now monitored — no other changes needed
import openai
client = openai.OpenAI(api_key="sk-...")
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

> `python-dotenv` is optional. If you export the variables in your shell or deployment platform, `load_dotenv()` is not needed.

---

### Option 2: Pass credentials directly

```python
import computecapx

computecapx.instrument(
    api_key="YOUR_API_KEY",
    project_id="YOUR_PROJECT_ID",
)
```

---

### Option 3: Zero-code CLI wrapper

Instrument any existing script without touching its source:

```bash
# Save credentials once
computecapx login --key YOUR_API_KEY --project YOUR_PROJECT_ID

# Run any script with full telemetry
computecapx-run python my_agent.py
computecapx-run uvicorn app:main   # Works with servers too
computecapx-run --no-cloud python my_local_script.py
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `COMPUTECAPX_API_KEY` | Your SDK API key |
| `COMPUTECAPX_PROJECT_ID` | Your project ID |
| `COMPUTECAPX_DISABLE_LOOP_CHECK` | Set to `true` to disable the AI loop circuit breaker |

---

## `instrument()` Reference

```python
computecapx.instrument(
    api_key=None,               # SDK API key (or set COMPUTECAPX_API_KEY)
    project_id=None,            # Project ID (or set COMPUTECAPX_PROJECT_ID)
    claim_infrastructure=True,  # Send cloud heartbeats — set False for local dev
    instrument_ai=True,         # Intercept AI calls and enforce policies
    backend_url=None,           # Override backend (self-hosted only)
)
```

**Credential resolution order:**
1. Direct argument
2. Environment variable (`COMPUTECAPX_API_KEY` / `COMPUTECAPX_PROJECT_ID`)
3. CLI config file (`~/.computecapx/config.json` written by `computecapx login`)

---

## CLI Commands

```bash
# Authenticate and save credentials to ~/.computecapx/config.json
computecapx login --key YOUR_API_KEY --project YOUR_PROJECT_ID

# Update the default project
computecapx set-project YOUR_PROJECT_ID

# Run diagnostics and test backend connectivity
computecapx status
```

---

## `computecapx-run` Flags

```bash
computecapx-run [flags] <script.py or module> [script args...]

  --no-cloud    Disable infrastructure heartbeats (auto-enabled on local)
  --no-ai       Disable AI call interception
  --no-loop     Disable the runaway loop circuit breaker
```

---

## Runaway Loop Circuit Breaker

The circuit breaker automatically detects repetitive AI agent patterns:

| Tier | Trigger | Action |
|---|---|---|
| Warning | 5 similar requests in 10 seconds | 2-second throttle applied |
| Throttle | 10 similar requests in 20 seconds | 5-second throttle applied |
| Block | 20 requests in 60 seconds | `ComputeCapRunawayLoopError` raised, task permanently blocked |

---

## Supported AI Providers

| Provider | Detection Method |
|---|---|
| OpenAI | HTTP intercept (httpx + requests) |
| Anthropic | HTTP intercept (httpx + requests) |
| Google Gemini | HTTP intercept + gRPC (`sys.meta_path` hook) |
| Groq | HTTP intercept |
| Mistral | HTTP intercept |
| Cohere | HTTP intercept |
| Any OpenAI-compatible API | HTTP intercept |

---

## Supported Cloud Providers

| Provider | Detection Method |
|---|---|
| AWS EC2 | IMDSv2 token + metadata endpoint |
| Google Cloud (GCP) | Compute Metadata Server |
| Microsoft Azure | Instance Metadata Service |
| DigitalOcean | Droplet Metadata API |
| Oracle Cloud (OCI) | OPCv2 metadata endpoint |
| Vercel | `VERCEL` environment variable |
| Netlify | `NETLIFY` environment variable |
| Local / Container | Hostname fallback |

---

## Error Classes

Both errors inherit from `BaseException` so they cannot be accidentally swallowed by a bare `except Exception` in user code.

```python
from computecapx import ComputeCapBudgetExceededError, ComputeCapRunawayLoopError

try:
    response = openai_client.chat.completions.create(...)
except ComputeCapBudgetExceededError:
    print("Monthly budget limit reached — request blocked by ComputeCapX.")
except ComputeCapRunawayLoopError:
    print("Runaway agent loop detected — task permanently halted.")
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.

© 2026 [ComputeCapX](https://computecapx.com)