"""Class-A (row-level invariant) rule chain for the payment-router engine.

Every rule is a pure function over a RuleContext and an in-flight RuleResult;
no I/O, no DB, no randomness tied to global state. Rules are applied in a
deterministic order (see RULE_CHAIN below) so that the outcome is independent
of YAML probability draws that happened upstream in engine.simulate_transaction.

Pattern IDs covered
-------------------
- CC038, CC036 (recurring/MIT)
- CC002, CC107, CC108, CC109, CC110, CC048-adjacent (BIN/brand)
- CC021, CC046, TS009, TS027/TS029, TS054 (3DS gates)
- CC087, NT001/NT006, AD088 (tokens)
- AD067, AD078, AD080 (routing flag lifts)
- AD103, AD107 (anti-patterns)
- RC005, RC008, RC020, RC033 (retry gates — consumed by simulate_with_retry)

Non-goals
---------
- Class B distributions (regional approval bands, decline-code shares) stay in
  the YAML profiles + engine probability draws.
- Class C sequences (retry recovery, chargeback classification) stay in
  generator / state_machine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from payment_router.models import CardBrand, ProviderResponse, SimulateRequest


# ---------------------------------------------------------------------------
# Context passed through the rule chain
# ---------------------------------------------------------------------------

@dataclass
class RuleContext:
    """Minimal request-level context a rule may read.

    Built from a SimulateRequest once per /simulate call; never mutated by rules.
    """
    provider: str
    country: str
    issuer_country: Optional[str]
    card_brand: str
    card_type: str
    amount: float
    currency: str
    use_3ds: bool
    present_mode: str               # "ecom" | "pos" | "moto"
    is_recurring: bool
    is_mit: bool
    network_token_present: bool
    bin_first6: Optional[str]
    mcc: Optional[str]
    routing_optimized: bool
    mcc_routing_optimized: bool
    smart_routed: bool

    @classmethod
    def from_request(cls, req: SimulateRequest) -> "RuleContext":
        return cls(
            provider=req.provider,
            country=req.country,
            issuer_country=req.issuer_country,
            card_brand=req.card_brand.value if isinstance(req.card_brand, CardBrand) else str(req.card_brand),
            card_type=req.card_type.value,
            amount=req.amount,
            currency=req.currency,
            use_3ds=req.use_3ds,
            present_mode=(req.present_mode or "ecom").lower(),
            is_recurring=bool(req.is_recurring),
            is_mit=bool(req.is_mit),
            network_token_present=bool(req.network_token_present),
            bin_first6=req.bin_first6,
            mcc=req.mcc,
            routing_optimized=bool(req.routing_optimized),
            mcc_routing_optimized=bool(req.mcc_routing_optimized),
            smart_routed=bool(req.smart_routed),
        )


@dataclass
class RuleResult:
    """Mutable draft response carried through the chain.

    engine.simulate_transaction constructs this from its YAML-probability draw;
    rules may mutate approved, response_code, response_message, approval_prob_adjust,
    and 3DS fields. They append their ID to `applied` for audit.
    """
    approved: bool
    response_code: str
    response_message: str
    merchant_advice_code: Optional[str]
    approval_prob_adjust: float = 0.0       # sum of rule lifts; caller re-draws if provided
    three_ds_requested: bool = False         # mirrors req.use_3ds by default
    three_ds_version: Optional[str] = None
    three_ds_eci: Optional[str] = None
    three_ds_challenged: Optional[bool] = None
    applied: list[str] = field(default_factory=list)
    rejected_by_rule: Optional[str] = None

    def mark(self, rule_id: str) -> None:
        self.applied.append(rule_id)


# ---------------------------------------------------------------------------
# Helpers used by multiple rule modules
# ---------------------------------------------------------------------------

EEA: frozenset[str] = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE",
})
UK_SET: frozenset[str] = frozenset({"GB"})
MEA_LATAM: frozenset[str] = frozenset({
    "BR", "AR", "CL", "MX", "CO", "PE", "AE", "SA", "ZA", "EG",
})

HARD_DECLINE_CODES: frozenset[str] = frozenset({"54", "04", "07", "41", "43", "14", "62"})


# ---------------------------------------------------------------------------
# Rule chain — order is load-bearing (documented)
# ---------------------------------------------------------------------------

from payment_router.pattern_rules import (  # noqa: E402  — circular guard via lazy import
    anti_patterns,
    bins,
    cascade,
    decline_codes,
    flags,
    mit,
    three_ds,
    tokens,
)

# Each entry: (id, callable(ctx, result) -> None, short description).
RULE_CHAIN: list[tuple[str, callable, str]] = [
    # 1. Normalise request — enforce CC038 (recurring ⇒ MIT) before anything else reads is_mit.
    ("CC038", mit.apply_recurring_implies_mit, "recurring=True forces is_mit=True"),

    # 2. BIN <> brand sanity. Must come before tokens because token rule assumes valid brand.
    ("CC002_CC107_CC108_CC109_CC110", bins.validate_bin_for_brand,
     "BIN prefix matches card_brand"),

    # 3. 3DS gates — POS, APM, MIT-subsequent all suppress 3DS.
    ("CC021", three_ds.suppress_3ds_on_pos, "POS never requests 3DS"),
    ("CC046", three_ds.suppress_3ds_on_apm, "non-card payment method never requests 3DS"),
    ("TS027", three_ds.suppress_3ds_on_mit, "MIT rebills do not request 3DS"),

    # 4. Hard anti-patterns that can reject the transaction outright.
    ("CC087", tokens.enforce_no_network_token_at_pos,
     "network_token_present=True AND present_mode=POS ⇒ reject"),

    # 5. Approval-probability lifts (routing optimisation flags).
    ("AD067", flags.apply_smart_routed_lift, "MEA/LATAM smart_routed approval lift +4pp"),
    ("AD078", flags.apply_routing_optimized_lift, "US debit routing_optimized +2.5pp"),
    ("AD080", flags.apply_mcc_routing_optimized_lift, "FR mcc_routing_optimized +7pp"),

    # 6. Token-related approval lifts.
    ("NT001", tokens.apply_network_token_lift, "network_token_present +4.5pp approval"),

    # 7. Cross-border issuer modifier (delegated to issuer_tiers).
    ("AD070", bins.apply_cross_border_penalty_if_not_tokenized,
     "cross-border, no network token ⇒ -10pp"),

    # 8. Decline-code quality rules.
    ("AD088", decline_codes.redraw_expired_card_on_network_token,
     "NT+code 54 extremely rare; promote to soft code"),

    # 9. Floors / caps (AD107).
    ("AD107", anti_patterns.apply_approval_floor, "min approval probability 32%"),

    # 10. ECI alignment after 3DS resolved.
    ("TS009", three_ds.align_eci_to_brand_and_outcome,
     "ECI from card_brand + 3DS outcome"),
]


import threading  # noqa: E402
from collections import Counter  # noqa: E402

_counters: Counter = Counter()
_counters_lock = threading.Lock()


def apply_rule_chain(ctx: RuleContext, result: RuleResult) -> RuleResult:
    """Run every rule in RULE_CHAIN against (ctx, result).

    Rules mutate `result` in place and append to `result.applied`. A rule may set
    `result.rejected_by_rule` to short-circuit the remaining chain (used by CC087).

    Per-rule eval/apply counts are incremented in `_counters` for observability.
    """
    local_counts: list[tuple[str, int]] = []
    for rule_id, fn, _desc in RULE_CHAIN:
        if result.rejected_by_rule is not None:
            break
        before = len(result.applied)
        fn(ctx, result)
        after = len(result.applied)
        local_counts.append((rule_id, after - before))
    with _counters_lock:
        for rule_id, delta in local_counts:
            _counters[f"{rule_id}:evaluated"] += 1
            if delta > 0:
                _counters[f"{rule_id}:applied"] += delta
    return result


def rule_ids() -> list[str]:
    """Stable list of rule IDs for logging / compliance harness."""
    return [rid for rid, _, _ in RULE_CHAIN]


def get_counters() -> dict[str, int]:
    """Snapshot of per-rule fire counts since process start or last reset."""
    with _counters_lock:
        return dict(_counters)


def reset_counters() -> None:
    """Zero all rule counters (admin / test use)."""
    with _counters_lock:
        _counters.clear()


# Also export the ported retry-gate helper (used by simulate_with_retry).
from payment_router.pattern_rules.cascade import is_retryable  # noqa: E402, F401
