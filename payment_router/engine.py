"""Core simulation engine."""

from __future__ import annotations

import hashlib
import os
import random
import uuid
from collections import Counter
from contextlib import contextmanager
from math import log

import numpy as np


def _compare_seed(req) -> int:
    """Stable 64-bit seed derived from the request fields that affect ranking.

    Same inputs → same seed → same Monte Carlo draws → identical /compare,
    /recommend, /query rankings across calls. Excludes idempotency_key,
    provider, and any identifier that should not influence the ranking.

    `mcc` is included so that adding/removing the MCC field changes the seed
    deterministically (otherwise an MCC-bucket lift would not be reproducible
    across calls when callers vary the MCC).
    """
    parts = [
        str(req.country or "").upper(),
        str(req.issuer_country or "").upper(),
        str(req.card_brand.value if hasattr(req.card_brand, "value") else req.card_brand).lower(),
        str(req.card_type.value if hasattr(req.card_type, "value") else req.card_type).lower(),
        f"{float(req.amount):.4f}",
        str(req.currency or "").upper(),
        "1" if getattr(req, "use_3ds", False) else "0",
        str(getattr(req, "mcc", None) or "").strip(),
    ]
    digest = hashlib.blake2b("|".join(parts).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False)


@contextmanager
def _seeded_rng(seed: int):
    """Pin random.* and numpy.random.* to a deterministic seed for the
    duration of the block, restoring the prior global state on exit so
    the rest of the process keeps its non-deterministic behaviour
    (single /simulate, retry cascade, webhook IDs, etc.).
    """
    py_state = random.getstate()
    np_state = np.random.get_state()
    try:
        random.seed(seed)
        np.random.seed(seed & 0xFFFFFFFF)
        yield
    finally:
        random.setstate(py_state)
        np.random.set_state(np_state)

from payment_router.models import (
    CardBrand,
    CardType,
    CompareRequest,
    CompareResult,
    PaResStatus,
    ProviderResponse,
    RetryAttempt,
    RetryResult,
    SimulateRequest,
    ThreeDSResult,
    ThreeDSVersion,
    TransactionState,
)
from payment_router.issuer_tiers import issuer_modifier
from payment_router.pattern_rules import RuleContext, RuleResult, apply_rule_chain, is_retryable
from payment_router.provider_loader import list_providers, load_provider
from payment_router.response_codes import ISO_8583_CODES, is_soft_decline


# Feature flag: allow A/B perf comparison. Default True.
APPLY_PATTERN_RULES = os.environ.get("APPLY_PATTERN_RULES", "1") not in ("0", "false", "False", "")


# ---------------------------------------------------------------------------
# Approval probability
# ---------------------------------------------------------------------------

_CROSS_BORDER_FIT_LIFT: dict[str, float] = {
    # Archetype-corridor fit: applied only on cross-border transactions.
    # Positive = specialist advantage, negative = structural misfit.
    # Calibrated so that for textbook cross-border profiles (e.g. US→DE EUR),
    # cross-border-fx specialists surface in the top-2 of /compare rankings.
    "cross-border-fx-specialist-a": 0.08,
    "cross-border-fx-specialist-b": 0.08,
    "high-risk-or-orchestrator-a": -0.04,
    "high-risk-or-orchestrator-b": -0.04,
    "regional-bank-processor-a": -0.03,
    "regional-bank-processor-b": -0.03,
    "regional-bank-processor-c": -0.03,
    "regional-card-specialist-a": -0.02,
    "regional-card-specialist-b": -0.02,
    "global-acquirer-a": 0.00,
    "global-acquirer-b": 0.00,
}


# ---------------------------------------------------------------------------
# MCC bucketing (v1)
# Without this, the high-risk specialist orchestrator-a ranks dead last on
# every textbook profile because the simulator never receives an MCC signal.
# v1 = static high-risk allowlist + numeric mainstream demote. Replace with
# per-MCC YAML lookups when the YAMLs ship richer mcc tables.
# ---------------------------------------------------------------------------

# MCCs traditionally classified as high-risk by acquirers / scheme rules.
# Source: Visa Acquirer Risk MCC list (5944 jewelry, 5967 direct-mail/inbound
# telemarketing, 7273 dating, 7995 gambling, 5816 digital goods/games,
# 5993 cigar stores, 5912 drug stores, 4816 computer network info svcs).
_HIGH_RISK_MCCS: frozenset[str] = frozenset({
    "5944",  # Jewelry
    "5967",  # Direct marketing — inbound teleservices / adult content
    "7273",  # Dating services
    "7995",  # Gambling
    "5816",  # Digital goods — large game/digital downloads
    "5993",  # Cigar stores
    "4816",  # Computer network information services
})

# MCCs that flag specialised regulatory verticals — orchestrator-a is built
# for these, so we lift it. Mainstream MCCs (grocery, restaurant, electronics
# etc.) get the inverse treatment so the high-risk specialist does not
# dominate vanilla flows.
#
# 2026-04-27 audit fix:
#   • orchestrator-b was getting +4pp on high-risk MCCs, which is wrong.
#     orchestrator-b is the multi-PSP general-purpose routing brand; in
#     production it would either decline high-risk merchants outright or
#     route them to its high-risk-vertical sister. It does NOT get a lift
#     just for being an "orchestrator" — only the dedicated high-risk
#     specialist (orchestrator-a) does.
#   • Set orchestrator-b high-risk lift to -0.05 (small drag, mirrors
#     mainstream archetypes which all lose ~10pp on high-risk MCCs).
_MCC_BUCKET_LIFT: dict[str, dict[str, float]] = {
    # bucket -> {provider_name: lift_pp_as_decimal}
    "high_risk": {
        "high-risk-or-orchestrator-a": 0.18,
        # orchestrator-b is general-purpose multi-PSP routing — small drag
        # on high-risk MCCs, NOT a lift (was +0.04 → now -0.05).
        "high-risk-or-orchestrator-b": -0.05,
        # Mainstream archetypes lose ~10pp on high-risk MCCs because they
        # would decline these merchants outright in production.
        "global-acquirer-a": -0.10,
        "global-acquirer-b": -0.10,
        "regional-bank-processor-a": -0.12,
        "regional-bank-processor-b": -0.12,
        "regional-bank-processor-c": -0.12,
        "regional-card-specialist-a": -0.06,
        "regional-card-specialist-b": -0.06,
        "cross-border-fx-specialist-a": -0.04,
        "cross-border-fx-specialist-b": -0.04,
    },
    "mainstream": {
        # Slight demote for high-risk specialists when MCC is plainly low-risk.
        "high-risk-or-orchestrator-a": -0.05,
        # orchestrator-b is "multi-PSP routing" archetype; it is general
        # purpose, so do not penalise it here.
    },
}


# 3DS approval lift (additive, applied in _approval_probability when use_3ds=True).
# Pattern basis: 3DS authentication carries a liability shift to the issuer,
# which tightens issuer risk thresholds and increases approval likelihood by
# 1–4pp on CNP card volume (industry consensus, e.g. Visa Risk Manager studies,
# Stripe Radar baselines). The lift is smaller for already-high-3DS regions
# (EEA mandatory) and larger for opt-in regions (US/CA/AU). Engine applies
# a flat +0.02 baseline lift here; the 3DS challenge probability and
# liability_shift fields downstream do not affect approval re-draw.
_3DS_APPROVAL_LIFT: float = 0.02


def _classify_mcc(mcc: str | None) -> str | None:
    """Return 'high_risk', 'mainstream', or None if MCC is missing/unknown.

    None preserves backwards-compatible behaviour for callers that omit MCC
    (legacy clients, the chatbot tool calls that don't pass MCC yet).
    """
    if not mcc:
        return None
    raw = str(mcc).strip()
    if not raw:
        return None
    if raw in _HIGH_RISK_MCCS:
        return "high_risk"
    # Anything that parses as a 4-digit numeric MCC and isn't in the high-risk
    # set we treat as mainstream. Non-numeric / wrong length → unknown (None)
    # so we don't apply a lift on garbage input.
    if len(raw) == 4 and raw.isdigit():
        return "mainstream"
    return None


def _mcc_lift(provider_name: str, mcc: str | None) -> float:
    """Per-provider lift (additive on the multiplicative base) from MCC bucket."""
    bucket = _classify_mcc(mcc)
    if bucket is None:
        return 0.0
    return _MCC_BUCKET_LIFT.get(bucket, {}).get(provider_name, 0.0)


def _approval_probability(provider_name: str, req: SimulateRequest) -> float:
    profile = load_provider(provider_name)
    cp = profile.country(req.country)

    if cp is not None:
        base = cp.base
        if req.card_brand == CardBrand.VISA:
            base *= cp.card_modifiers.visa
        elif req.card_brand == CardBrand.MASTERCARD:
            base *= cp.card_modifiers.mastercard
        elif req.card_brand == CardBrand.AMEX:
            base *= cp.card_modifiers.amex
    else:
        base = profile.base_approval_rate * 0.95

    # Card type modifier (credit=1.0 baseline, debit/prepaid/commercial differ)
    if req.card_type == CardType.DEBIT:
        base *= profile.card_type_modifiers.debit
    elif req.card_type == CardType.PREPAID:
        base *= profile.card_type_modifiers.prepaid
    elif req.card_type == CardType.COMMERCIAL:
        base *= profile.card_type_modifiers.commercial
    # CREDIT and UNKNOWN: no modifier (1.0)

    # Cross-border handling — applies only when issuer_country is set AND != country.
    # Two signals:
    #   (a) issuer_modifier — tier-based drag, already calibrated in issuer_tiers.py
    #   (b) archetype-corridor fit — lift for cross-border-fx specialists,
    #       drag for non-specialists routing outside their home corridor.
    # Note: _CROSS_BORDER_FIT_LIFT is additive on top of the multiplicative base.
    if req.issuer_country and req.issuer_country != req.country:
        base *= issuer_modifier(req.issuer_country)
        fit_lift = _CROSS_BORDER_FIT_LIFT.get(provider_name, 0.0)
        base = max(0.0, base + fit_lift)

    thresholds = sorted(
        ((float(k), v) for k, v in profile.amount_modifier_thresholds.items()),
        key=lambda x: x[0],
    )
    amount_mod = 1.0
    for threshold, modifier in thresholds:
        if req.amount >= threshold:
            amount_mod = modifier

    # MCC bucket lift (v1) — additive on top of the multiplicative base, then
    # clamped to [0, 0.99]. No-op when req.mcc is None / unrecognised.
    mcc_lift = _mcc_lift(provider_name, getattr(req, "mcc", None))

    # 3DS approval lift — additive, applied when caller requests 3DS.
    # Pattern basis: liability shift to issuer tightens fraud thresholds and
    # raises approval probability ~2pp on CNP card volume. Without this lift
    # the engine treats 3DS as approval-neutral, which contradicts industry
    # data (Visa Risk Manager, Stripe Radar) and the AD003 EU baseline that
    # bakes 3DS friction into a 82-85% blended rate (i.e. lifts compensate
    # for SCA challenges). Patch 2026-04-27.
    threeds_lift = _3DS_APPROVAL_LIFT if getattr(req, "use_3ds", False) else 0.0

    return max(0.0, min(base * amount_mod + mcc_lift + threeds_lift, 0.99))


# ---------------------------------------------------------------------------
# Latency sampling (log-normal)
# Country-specific latency comes from YAML override (e.g. Pix BR, UPI IN).
# ---------------------------------------------------------------------------

def _sample_latency(provider_name: str, country: str) -> float:
    profile = load_provider(provider_name)
    lp = profile.effective_latency(country)

    mu = log(lp.p50_ms)
    sigma = (log(lp.p95_ms) - mu) / 1.645

    if random.random() < 0.01:
        return float(np.random.lognormal(log(lp.p99_ms), sigma * 0.3))

    return round(float(np.random.lognormal(mu, sigma)), 1)


# ---------------------------------------------------------------------------
# Decline code selection
# ---------------------------------------------------------------------------

def _select_decline_code(provider_name: str, country: str) -> str:
    profile = load_provider(provider_name)
    codes_list = profile.effective_decline_codes(country)
    codes = [d.code for d in codes_list]
    weights = [d.weight for d in codes_list]
    return random.choices(codes, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# 3DS simulation
# Liability shift rules (Visa/MC standard):
#   ECI 05 + PaRes Y  → issuer liable (full challenge auth)
#   ECI 06 + PaRes A  → issuer liable (v1 attempted auth)
#   ECI 07 + PaRes Y/A → issuer liable (frictionless, issuer risk-assessed)
#   ECI 07 + PaRes U/R/N → merchant liable (no auth guarantee)
# ---------------------------------------------------------------------------

def _simulate_3ds(provider_name: str, country: str, amount: float) -> ThreeDSResult:
    profile = load_provider(provider_name)
    t = profile.effective_three_ds(country)

    challenge_rate = t.challenge_rate
    if amount > 500:
        challenge_rate = min(challenge_rate * 1.2, 0.95)

    challenged = random.random() < challenge_rate

    if random.random() < t.version_2_2_rate:
        version = ThreeDSVersion.V2_2
        eci = "05" if challenged else "07"
    elif random.random() < 0.3:
        version = ThreeDSVersion.V2_1
        eci = "05" if challenged else "07"
    else:
        version = ThreeDSVersion.V1
        eci = "05" if challenged else "06"

    if challenged:
        pares = random.choices(
            [PaResStatus.Y, PaResStatus.N, PaResStatus.U, PaResStatus.R],
            weights=[75, 12, 8, 5],
        )[0]
    else:
        pares = random.choices(
            [PaResStatus.Y, PaResStatus.A, PaResStatus.U],
            weights=[88, 8, 4],
        )[0]

    liability_shift = (
        (eci in ("05", "06") and pares == PaResStatus.Y) or
        (eci == "06" and pares == PaResStatus.A) or
        (eci == "07" and pares in (PaResStatus.Y, PaResStatus.A))
    )

    return ThreeDSResult(
        version=version,
        challenged=challenged,
        pares_status=pares,
        eci=eci,
        liability_shift=liability_shift,
    )


# ---------------------------------------------------------------------------
# Single transaction
# ---------------------------------------------------------------------------

def simulate_transaction(req: SimulateRequest, db=None) -> ProviderResponse:
    """Simulate a single transaction.

    Pipeline (2026-04-19 refactor):
      1. YAML probability draw (distributional — unchanged).
      2. Class-A pattern rule chain (pattern_rules.apply_rule_chain).
      3. Re-draw approved flag if rules added an approval-probability adjust.
      4. Construct ProviderResponse with rules_applied audit list.

    When `db` is provided (SQLAlchemy Session), the transaction is persisted.
    """
    prob = _approval_probability(req.provider, req)
    approved = random.random() < prob
    latency_ms = _sample_latency(req.provider, req.country)

    if approved:
        response_code = "00"
        state = TransactionState.AUTHORIZED
        merchant_advice_code = None
    else:
        response_code = _select_decline_code(req.provider, req.country)
        state = TransactionState.DECLINED
        if is_soft_decline(response_code):
            merchant_advice_code = random.choice(["02", "24", "25"])
        else:
            merchant_advice_code = "03"

    code_entry = ISO_8583_CODES.get(response_code, ("unknown", "Unknown response code", False))
    response_message = code_entry[1]
    three_ds = _simulate_3ds(req.provider, req.country, req.amount) if req.use_3ds else None

    # -------------------------------------------------------------------
    # Class-A pattern rule chain
    # -------------------------------------------------------------------
    rules_applied: list[str] = []
    if APPLY_PATTERN_RULES:
        ctx = RuleContext.from_request(req)
        rr = RuleResult(
            approved=approved,
            response_code=response_code,
            response_message=response_message,
            merchant_advice_code=merchant_advice_code,
            three_ds_requested=bool(three_ds),
            three_ds_version=three_ds.version.value if three_ds else None,
            three_ds_eci=three_ds.eci if three_ds else None,
            three_ds_challenged=three_ds.challenged if three_ds else None,
        )
        apply_rule_chain(ctx, rr)
        rules_applied = list(rr.applied)

        # If a rule hard-rejected (CC087), commit that outcome.
        if rr.rejected_by_rule is not None:
            approved = rr.approved
            response_code = rr.response_code
            response_message = rr.response_message
            merchant_advice_code = rr.merchant_advice_code
            state = TransactionState.DECLINED
            three_ds = None  # rejected before 3DS settles
        else:
            # Re-draw approved if rules adjusted the probability.
            if rr.approval_prob_adjust != 0.0:
                new_prob = max(0.0, min(0.99, prob + rr.approval_prob_adjust))
                # Use a fresh draw so the rules-on path meaningfully differs
                # from rules-off where appropriate.
                approved = random.random() < new_prob
                if approved:
                    response_code = "00"
                    response_message = "Approved"
                    merchant_advice_code = None
                    state = TransactionState.AUTHORIZED
                else:
                    state = TransactionState.DECLINED

            # Reflect the rule-adjusted 3DS state back into ThreeDSResult.
            if three_ds is not None and not rr.three_ds_requested:
                three_ds = None
            elif three_ds is not None and rr.three_ds_eci and rr.three_ds_eci != three_ds.eci:
                three_ds = three_ds.model_copy(update={"eci": rr.three_ds_eci})

    txn_id = str(uuid.uuid4())

    # Persist to database when a session is provided
    if db is not None:
        _persist_transaction(db, txn_id, req, state, response_code, response_message, latency_ms)

    return ProviderResponse(
        transaction_id=txn_id,
        provider=req.provider,
        state=state,
        approved=approved,
        response_code=response_code,
        response_message=response_message,
        merchant_advice_code=merchant_advice_code,
        latency_ms=latency_ms,
        amount=req.amount,
        currency=req.currency,
        country=req.country,
        card_brand=req.card_brand,
        card_type=req.card_type,
        three_ds=three_ds,
        issuer_country=req.issuer_country,
        idempotency_key=req.idempotency_key,
        rules_applied=rules_applied,
        present_mode=req.present_mode,
        is_mit=bool(req.is_mit or req.is_recurring),
        is_recurring=bool(req.is_recurring),
        network_token_present=bool(req.network_token_present),
        bin_first6=req.bin_first6,
    )


def _persist_transaction(
    db,
    txn_id: str,
    req: SimulateRequest,
    state: TransactionState,
    response_code: str,
    response_message: str,
    latency_ms: float,
) -> None:
    """Write Transaction + initial StateTransition rows to the database.

    If an idempotency_key is provided and already exists, skips the insert
    silently — the caller will use the txn_id from the existing row via
    the idempotency middleware (Session 6). This prevents IntegrityError 500s.
    """
    from sqlalchemy import select
    from payment_router.db import StateTransition, Transaction

    if req.idempotency_key:
        existing = db.execute(
            select(Transaction).where(Transaction.idempotency_key == req.idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return

    txn = Transaction(
        id=txn_id,
        provider=req.provider,
        country=req.country,
        issuer_country=req.issuer_country,
        card_brand=req.card_brand.value,
        card_type=req.card_type.value,
        amount=req.amount,
        currency=req.currency,
        state=state.value,
        response_code=response_code,
        response_message=response_message,
        idempotency_key=req.idempotency_key,
        latency_ms=latency_ms,
        use_3ds=req.use_3ds,
    )
    db.add(txn)

    # pending → authorized/declined
    record = StateTransition(
        transaction_id=txn_id,
        from_state=TransactionState.PENDING.value,
        to_state=state.value,
        triggered_by="simulate",
    )
    db.add(record)
    db.commit()


# ---------------------------------------------------------------------------
# Retry across providers on soft decline
# ---------------------------------------------------------------------------

def simulate_with_retry(
    req: SimulateRequest,
    providers: list[str],
    max_attempts: int = 3,
) -> RetryResult:
    """Try providers in order. On soft decline, cascade to the next provider.
    On hard decline or approval, stop immediately."""
    attempts: list[RetryAttempt] = []
    cumulative_latency = 0.0
    final_response: ProviderResponse | None = None

    for i, provider_name in enumerate(providers[:max_attempts]):
        attempt_req = req.model_copy(update={"provider": provider_name})
        response = simulate_transaction(attempt_req)
        cumulative_latency += response.latency_ms
        soft = is_soft_decline(response.response_code) if not response.approved else False

        attempts.append(RetryAttempt(
            attempt=i + 1,
            provider=provider_name,
            response_code=response.response_code,
            approved=response.approved,
            latency_ms=response.latency_ms,
            was_soft_decline=soft,
        ))

        final_response = response

        if response.approved:
            break   # success — done

        # Pattern-rule gate: RC005/RC008/RC019/RC020/RC026/RC033.
        # soft=False short-circuits anyway; is_retryable adds MAC 01/02,
        # risk_skip, MIT, and APM gates.
        if not is_retryable(
            response.response_code,
            is_soft=soft,
            card_brand=response.card_brand.value if hasattr(response.card_brand, "value") else str(response.card_brand),
            mastercard_advice_code=response.merchant_advice_code,
            risk_skip_flag=False,
            is_mit=bool(response.is_mit),
            payment_method_is_card=True,
        ):
            break   # rule-gated — stop cascade

        # soft decline → cascade to next provider

    return RetryResult(
        attempts=attempts,
        final_response=final_response,
        total_latency_ms=round(cumulative_latency, 1),
        succeeded=final_response.approved,
        providers_tried=[a.provider for a in attempts],
    )


# ---------------------------------------------------------------------------
# Compare all providers
# ---------------------------------------------------------------------------

def compare_providers(req: CompareRequest) -> list[CompareResult]:
    """Rank all providers via Monte Carlo for the given transaction profile.

    Determinism: the Monte Carlo draws are seeded from a stable hash of the
    request fields that affect ranking (country, issuer_country, card_brand,
    card_type, amount, currency, use_3ds). Same input → same ranking, every
    call. /compare, /recommend, and /query (which all funnel through this
    function) therefore agree bit-for-bit on identical inputs. The seeding
    is scoped to this function — single /simulate calls, retry cascades,
    webhooks, UUIDs etc. retain their normal non-deterministic behaviour.
    """
    N = 500
    results = []

    with _seeded_rng(_compare_seed(req)):
        for provider_name in list_providers():
            sim_req = SimulateRequest(
                provider=provider_name,
                country=req.country,
                issuer_country=req.issuer_country,
                card_brand=req.card_brand,
                card_type=req.card_type,
                amount=req.amount,
                currency=req.currency,
                use_3ds=req.use_3ds,
                mcc=getattr(req, "mcc", None),
            )

            approved_count = 0
            latencies: list[float] = []
            decline_codes: Counter = Counter()
            declined_total = 0
            challenged_count = 0

            for _ in range(N):
                r = simulate_transaction(sim_req)
                latencies.append(r.latency_ms)
                if r.approved:
                    approved_count += 1
                else:
                    declined_total += 1
                    decline_codes[r.response_code] += 1
                if req.use_3ds and r.three_ds and r.three_ds.challenged:
                    challenged_count += 1

            latencies.sort()
            p50 = latencies[int(N * 0.50)]
            p95 = latencies[int(N * 0.95)]

            code_dist: dict[str, float] = (
                {code: count / declined_total for code, count in decline_codes.most_common(5)}
                if declined_total
                else {}
            )
            # Populate three_ds_challenge_rate from the empirical sample when use_3ds
            # was requested; otherwise fall back to the YAML-declared challenge rate
            # for this corridor so the field is never null.
            if req.use_3ds:
                challenge_rate = challenged_count / N
            else:
                challenge_rate = load_provider(provider_name).effective_three_ds(req.country).challenge_rate

            results.append(CompareResult(
                provider=provider_name,
                projected_approval_rate=approved_count / N,
                latency_p50_ms=p50,
                latency_p95_ms=p95,
                decline_code_distribution=code_dist,
                three_ds_challenge_rate=challenge_rate,
            ))

    # Deterministic sort: primary key = projected_approval_rate desc,
    # secondary key = provider name asc. This guarantees that even if two
    # providers tie on the seeded sample, the order is stable across calls.
    return sorted(results, key=lambda r: (-r.projected_approval_rate, r.provider))
