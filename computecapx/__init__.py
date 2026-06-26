# sdk/computecapx/__init__.py
# Public API surface for the ComputeCapX instrumentation SDK.
#
# Usage:
#   import computecapx
#   computecapx.instrument(api_key="...", project_id="...")

"""
ComputeCapX — Enterprise FinOps & AI Governance SDK.

Automatically instruments AI API calls (OpenAI, Anthropic, Gemini, Groq, and
more), enforces per-project budget limits, detects runaway agent loops, and
streams real-time cost telemetry to the ComputeCapX platform — with two lines
of code and zero latency overhead.

Quick start::

    import computecapx

    computecapx.instrument(
        api_key="YOUR_API_KEY",
        project_id="YOUR_PROJECT_ID",
    )

See https://computecapx.com/docs for the full documentation.
"""

from .wrapper import instrument, ComputeCapTelemetry
from .wrapper import ComputeCapBudgetExceededError, ComputeCapRunawayLoopError
from .client import ComputeCapClient
from .detector import EnvironmentDetector

__version__ = "1.0.0"
__author__ = "ComputeCap"
__email__ = "support@computecap.io"
__license__ = "MIT"

__all__ = [
    # Primary entry point
    "instrument",
    # Core classes
    "ComputeCapClient",
    "ComputeCapTelemetry",
    "EnvironmentDetector",
    # Exception types
    "ComputeCapBudgetExceededError",
    "ComputeCapRunawayLoopError",
    # Package metadata
    "__version__",
]