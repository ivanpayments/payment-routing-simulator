"""Unit tests for payment_router.pattern_rules.

Covers each rule module with the minimum assertions needed to lock in the
Class-A invariants listed in Claude files/reviews/2026-04-19_refactor_rule_inventory.md.
"""
from __future__ import annotations

import pytest

from payment_router.models import CardBrand, CardType, SimulateRequest
from payment_router.pattern_rules import RuleContext, RuleResult, apply_rule_chain, is_retryable
from payment_router.pattern_rules.bins import bin_is_valid_for_brand


def _ctx(**overrides):
    base = dict(
        provider="global-acquirer-a", country="US", issuer_country=None,
        card_brand=CardBrand.VISA, card_type=CardType.CREDIT, amount=100.0,
    )
    base.update(overrides)
    return RuleContext.from_request(SimulateRequest(**base))


def _result(approved=True, code="00"):
    return RuleResult(
        approved=approved,
        response_code=code,
        response_message="Approved" if approved else "Decline",
        merchant_advice_code=None,
    )


# ---------------------------------------------------------------------------
# MIT
# ---------------------------------------------------------------------------

def test_cc038_recurring_implies_mit_flag():
    ctx = _ctx(is_recurring=True)
    res = _result()
    apply_rule_chain(ctx, res)
    assert "CC038" in res.applied


def test_mit_suppresses_3ds():
    ctx = _ctx(is_mit=True)
    res = _result()
    res.three_ds_requested = True
    apply_rule_chain(ctx, res)
    assert res.three_ds_requested is False
    assert "TS027" in res.applied


# ---------------------------------------------------------------------------
# BINs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("brand,bin6,ok", [
    ("visa", "400000", True),
    ("visa", "500000", False),
    ("mastercard", "510000", True),
    ("mastercard", "400000", False),
    ("amex", "370000", True),
    ("amex", "340000", True),
    ("amex", "400000", False),
    ("jcb", "352800", True),         # CC109 lower bound
    ("jcb", "358900", True),         # CC109 upper bound
    ("jcb", "359000", False),        # CC109 out of range
    ("jcb", "340000", False),
    ("unionpay", "620000", True),
    ("unionpay", "400000", False),
    ("discover", "601100", True),
    ("discover", "500000", False),
])
def test_bin_is_valid_for_brand(brand, bin6, ok):
    assert bin_is_valid_for_brand(brand, bin6) is ok


def test_bin_mismatch_marks_result():
    ctx = _ctx(card_brand=CardBrand.JCB, bin_first6="400000")
    res = _result()
    apply_rule_chain(ctx, res)
    assert "BIN_MISMATCH" in res.applied


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def test_cc087_nt_at_pos_rejects():
    ctx = _ctx(network_token_present=True, present_mode="pos")
    res = _result()
    apply_rule_chain(ctx, res)
    assert res.approved is False
    assert res.response_code == "14"
    assert "CC087" in res.applied
    assert res.rejected_by_rule == "CC087"


def test_nt_lift_applied_ecom():
    ctx = _ctx(network_token_present=True, present_mode="ecom")
    res = _result()
    apply_rule_chain(ctx, res)
    assert "NT001" in res.applied
    assert res.approval_prob_adjust >= 0.045


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

def test_ad067_smart_routed_mea_latam():
    ctx = _ctx(country="BR", smart_routed=True)
    res = _result()
    apply_rule_chain(ctx, res)
    assert "AD067" in res.applied


def test_ad067_smart_routed_outside_mea_latam_noop():
    ctx = _ctx(country="US", smart_routed=True)
    res = _result()
    apply_rule_chain(ctx, res)
    assert "AD067" not in res.applied


def test_ad078_us_debit_only():
    ctx = _ctx(country="US", card_type=CardType.DEBIT, routing_optimized=True)
    res = _result()
    apply_rule_chain(ctx, res)
    assert "AD078" in res.applied


def test_ad080_fr_only():
    ctx = _ctx(country="FR", mcc_routing_optimized=True)
    res = _result()
    apply_rule_chain(ctx, res)
    assert "AD080" in res.applied


# ---------------------------------------------------------------------------
# Cascade (retry gates)
# ---------------------------------------------------------------------------

def test_is_retryable_hard_decline():
    assert is_retryable("54", is_soft=False) is False
    assert is_retryable("04", is_soft=False) is False


def test_is_retryable_soft_decline():
    assert is_retryable("05", is_soft=True) is True


def test_is_retryable_mac_blocks():
    assert is_retryable("05", is_soft=True, mastercard_advice_code="01") is False
    assert is_retryable("05", is_soft=True, mastercard_advice_code="02") is False


def test_is_retryable_mit_blocks():
    assert is_retryable("05", is_soft=True, is_mit=True) is False


def test_is_retryable_risk_skip_blocks():
    assert is_retryable("05", is_soft=True, risk_skip_flag=True) is False


def test_is_retryable_apm_blocks():
    assert is_retryable("05", is_soft=True, payment_method_is_card=False) is False
