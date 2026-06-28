# Contributing to ComputeCapX Python SDK

Thank you for your interest in contributing! This document covers how to get started.

---

## Development Setup

```bash
git clone https://github.com/computecap/computecapx-python
cd computecapx-python

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install in editable mode with all dev dependencies
pip install -e ".[dev]"
```

---

## Environment Variables

For running tests or local scripts with real credentials, create a `.env` file:

```env
COMPUTECAPX_API_KEY=your_api_key_here
COMPUTECAPX_PROJECT_ID=your_project_id_here
```

Load it in your script with [`python-dotenv`](https://pypi.org/project/python-dotenv/):

```python
from dotenv import load_dotenv
load_dotenv()

import computecapx
computecapx.instrument()   # credentials read from env automatically
```

---

## Running Tests

```bash
pytest                                              # run all tests
pytest --cov=computecapx --cov-report=term-missing # with coverage report
```

---

## Code Quality

```bash
ruff check .      # lint
ruff format .     # auto-format
mypy computecapx/ # type checking
```

All three must pass with no errors before opening a pull request.

---

## Project Structure

```
computecapx/
├── computecapx/
│   ├── __init__.py     # public API surface
│   ├── client.py       # HTTP telemetry client + credential resolution
│   ├── wrapper.py      # instrumentation engine + instrument() entry point
│   ├── detector.py     # cloud environment auto-detection
│   ├── cli.py          # computecapx CLI commands
│   ├── run.py          # computecapx-run zero-code wrapper
│   └── py.typed        # PEP 561 type marker
├── tests/
│   └── test_imports.py # public API smoke tests
├── .github/
│   └── workflows/
│       └── publish.yml # CI test matrix + PyPI publish on tag
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
├── README.md
└── pyproject.toml
```

---

## Submitting Changes

1. Fork the repository and create a feature branch from `main`.
2. Write or update tests for any changed behaviour.
3. Ensure `pytest`, `ruff check`, and `mypy` all pass with no errors.
4. Update `CHANGELOG.md` under an `[Unreleased]` section.
5. Open a Pull Request with a clear description of your changes.

---

## Release Process (Maintainers Only)

1. Update the version in `pyproject.toml` and `computecapx/__init__.py` (keep them in sync).
2. Move the `[Unreleased]` section in `CHANGELOG.md` to the new version with today's date.
3. Commit, tag, and push:
   ```bash
   git tag v1.x.x
   git push && git push --tags
   ```
4. The GitHub Actions workflow automatically builds and publishes to PyPI — no manual upload needed.

---

## Code of Conduct

Be respectful. We follow the [Contributor Covenant](https://www.contributor-covenant.org/).
