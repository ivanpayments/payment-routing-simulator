"""Tests for provider_loader module."""

import pytest
from payment_router.provider_loader import clear_cache, list_providers, load_provider


def setup_function():
    clear_cache()


def test_list_providers_returns_list():
    providers = list_providers()
    assert isinstance(providers, list)
    assert len(providers) >= 5


def test_load_global_acquirer_a():
    p = load_provider("global-acquirer-a")
    assert p.name == "global-acquirer-a"
    assert 0 < p.base_approval_rate < 1
    assert p.latency.p50_ms < p.latency.p95_ms < p.latency.p99_ms
    assert len(p.decline_codes) > 0


def test_load_regional_bank_processor_b():
    p = load_provider("regional-bank-processor-b")
    assert p.name == "regional-bank-processor-b"
    assert 0 <= p.three_ds.challenge_rate <= 1.0


def test_provider_cached():
    p1 = load_provider("global-acquirer-a")
    p2 = load_provider("global-acquirer-a")
    assert p1 is p2


def test_unknown_provider_raises():
    with pytest.raises(FileNotFoundError, match="not found"):
        load_provider("nonexistent_provider_xyz")
