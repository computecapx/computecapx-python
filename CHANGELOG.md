# Changelog

All notable changes to the ComputeCapX Python SDK are documented here.

This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) and
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.0.0] — 2026-06-28

### Added

#### Core Instrumentation
- `computecapx.instrument()` — primary entry point. `api_key` and `project_id` are now optional; credentials resolve automatically from environment variables (`COMPUTECAPX_API_KEY`, `COMPUTECAPX_PROJECT_ID`) or the CLI config file (`~/.computecapx/config.json`).
- Universal HTTP interceptor patching `httpx.Client.send`, `httpx.AsyncClient.send`, `requests.Session.send`, and `urllib3.HTTPConnectionPool.urlopen` — covers every REST-based AI provider with zero user code changes.
- Google Gemini gRPC instrumentation via a `sys.meta_path` post-import hook (replaces the previous `builtins.__import__` approach).
- Async variants of all Gemini patches (`generate_content_async`).
- Enterprise runaway loop circuit breaker with three escalation tiers:
  - 2-second throttle at 5 requests / 10 seconds
  - 5-second throttle at 10 requests / 20 seconds
  - Permanent task block (`ComputeCapRunawayLoopError`) at 20 requests / 60 seconds
- Per-project AI budget enforcement via synchronous pre-flight check with local near-limit caching to avoid unnecessary network round-trips.
- Distributed tracing for `urllib3` network calls, `sqlite3` queries, and `psycopg2` (PostgreSQL) queries with I/O delta attribution.
- Crash handler via `sys.excepthook` — emits `internal_error` and `trace_end` events on fatal process crashes.
- Background batch telemetry worker (`_batch_worker`) — drains up to 100 trace events per flush using a persistent `requests.Session`.
- `computecapx-run` CLI zero-code wrapper — instruments any existing Python script or module (`uvicorn`, `gunicorn`, etc.) without modifying source.
- `computecapx login`, `computecapx set-project`, `computecapx status` CLI commands.

#### Multi-Cloud Environment Detection
- AWS EC2 via IMDSv2 token + instance metadata (instance ID, region, instance type).
- Google Cloud via Compute Metadata Server (instance ID, zone, machine type).
- Microsoft Azure via Instance Metadata Service (resource ID, resource group, VM size).
- DigitalOcean via Droplet Metadata API (droplet ID, region, size slug).
- Oracle Cloud (OCI) via OPCv2 metadata endpoint (instance ID, region, shape).
- Vercel and Netlify via environment variable signatures.
- Local/container fallback using `platform.node()`.

#### Package & Distribution
- `py.typed` marker for PEP 561 compliance (mypy, Pylance, pyright support).
- Full `pyproject.toml` with 15 PyPI classifiers, all project URLs, optional `[dev]` extras, and `ruff`/`mypy`/`pytest`/`coverage` tool configuration.
- GitHub Actions CI/CD workflow — multi-version matrix (Python 3.8–3.12), gated publish to PyPI via OIDC trusted publishing on version tags.
- 8-test smoke suite (`tests/test_imports.py`) covering public API surface, version format, exception hierarchy, and environment detector contract.

### Fixed
- Removed dual `Authorization: Bearer` header from SDK HTTP client (uses only `X-API-Key`).
- Removed `builtins.__import__` global patch — replaced with `sys.meta_path` importlib hook.
- Fixed 5 trace payload dicts with duplicate `"provider"` key and misaligned indentation.
- Removed unused `import traceback` from `instrument_crash_handler`.
- Fixed incorrect `openai_client` parameter in README example (parameter does not exist).
- Replaced internal `[10]` development annotations throughout all source files.
- Replaced `# sdk/computecapx/xxx.py:` header comments with proper module docstrings.

---

[1.0.0]: https://github.com/computecapx/computecapx-python/releases/tag/v1.0.0
