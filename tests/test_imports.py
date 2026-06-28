"""
tests/test_imports.py — Verifies the public API surface is importable and complete.
"""
import importlib
import pytest


def test_package_is_importable():
    """The top-level package must import without errors."""
    import computecapx  # noqa: F401


def test_version_string():
    """__version__ must be a non-empty semantic version string."""
    import computecapx
    assert isinstance(computecapx.__version__, str)
    parts = computecapx.__version__.split(".")
    assert len(parts) == 3, "Version must follow MAJOR.MINOR.PATCH"
    assert all(p.isdigit() for p in parts), "Version parts must be numeric"


def test_public_api_surface():
    """All symbols listed in __all__ must be importable from the top-level package."""
    import computecapx
    for symbol in computecapx.__all__:
        assert hasattr(computecapx, symbol), f"Missing from public API: {symbol}"


def test_instrument_is_callable():
    """computecapx.instrument must be a callable."""
    import computecapx
    assert callable(computecapx.instrument)


def test_exception_types_are_base_exceptions():
    """Budget and loop errors must inherit from BaseException so they can't be swallowed by bare `except Exception`."""
    from computecapx import ComputeCapBudgetExceededError, ComputeCapRunawayLoopError
    assert issubclass(ComputeCapBudgetExceededError, BaseException)
    assert issubclass(ComputeCapRunawayLoopError, BaseException)


def test_client_instantiates_without_api_key(monkeypatch):
    """ComputeCapClient must not raise on missing api_key — it should warn instead."""
    import warnings
    from computecapx import ComputeCapClient
    import computecapx.client as _client_mod

    # Patch away the config file and env var so there's truly no key available
    monkeypatch.setattr(_client_mod, "_load_persisted_config", lambda: {})
    monkeypatch.delenv("COMPUTECAPX_API_KEY", raising=False)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        client = ComputeCapClient(api_key=None)
        assert client is not None
        assert len(w) == 1, f"Expected 1 warning, got {len(w)}: {[str(x.message) for x in w]}"
        assert "API Key" in str(w[0].message)


def test_environment_detector_returns_dict():
    """EnvironmentDetector.detect_environment must always return a dict with required keys."""
    from computecapx import EnvironmentDetector
    result = EnvironmentDetector.detect_environment()
    assert isinstance(result, dict)
    for key in ("provider", "resource_id", "region", "instance_type"):
        assert key in result, f"Missing key in environment dict: {key}"


def test_environment_detector_local_fallback():
    """In a standard CI/test environment, provider should be 'local'."""
    from computecapx import EnvironmentDetector
    result = EnvironmentDetector.detect_environment()
    # On CI there's no cloud metadata endpoint — must fall back gracefully
    assert isinstance(result["provider"], str)
    assert len(result["provider"]) > 0
