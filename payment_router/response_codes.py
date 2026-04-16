"""ISO 8583 response codes, merchant advice codes, and provider mappings."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ISO 8583 response codes: code → (category, description, is_soft_decline)
# ---------------------------------------------------------------------------

ISO_8583_CODES: dict[str, tuple[str, str, bool]] = {
    # Approvals
    "00": ("approved", "Approved or completed successfully", False),
    "10": ("approved", "Partial approval", False),
    "85": ("approved", "No reason to decline", False),

    # Soft declines — retryable
    "05": ("soft_decline", "Do not honour", True),
    "51": ("soft_decline", "Insufficient funds", True),
    "61": ("soft_decline", "Exceeds withdrawal amount limit", True),
    "65": ("soft_decline", "Exceeds withdrawal frequency limit", True),
    "75": ("soft_decline", "PIN tries exceeded", True),
    "91": ("soft_decline", "Issuer or switch inoperative", True),
    "96": ("soft_decline", "System malfunction", True),
    "N7": ("soft_decline", "CVV2 failure", True),

    # Hard declines — do not retry
    "04": ("hard_decline", "Pick up card (no fraud)", False),
    "07": ("hard_decline", "Pick up card (fraud)", False),
    "14": ("hard_decline", "Invalid card number", False),
    "15": ("hard_decline", "No such issuer", False),
    "19": ("hard_decline", "Re-enter transaction", False),
    "25": ("hard_decline", "Unable to locate record on file", False),
    "28": ("hard_decline", "File temporarily unavailable", False),
    "30": ("hard_decline", "Format error", False),
    "33": ("hard_decline", "Expired card", False),
    "34": ("hard_decline", "Suspected fraud", False),
    "36": ("hard_decline", "Restricted card", False),
    "41": ("hard_decline", "Lost card", False),
    "43": ("hard_decline", "Stolen card", False),
    "54": ("hard_decline", "Expired card", False),
    "55": ("hard_decline", "Incorrect PIN", False),
    "57": ("hard_decline", "Transaction not permitted to cardholder", False),
    "58": ("hard_decline", "Transaction not permitted to terminal", False),
    "59": ("hard_decline", "Suspected fraud", False),
    "62": ("hard_decline", "Restricted card (country exclusion)", False),
    "63": ("hard_decline", "Security violation", False),
    "78": ("hard_decline", "No account", False),
    "82": ("hard_decline", "Policy — no credit", False),
    "93": ("hard_decline", "Transaction cannot be completed, law violation", False),
    "R0": ("hard_decline", "Stop payment order", False),
    "R1": ("hard_decline", "Revocation of authorisation order", False),
}


def is_soft_decline(code: str) -> bool:
    entry = ISO_8583_CODES.get(code)
    return entry[2] if entry else False


def is_approved(code: str) -> bool:
    entry = ISO_8583_CODES.get(code)
    return entry[0] == "approved" if entry else False


# ---------------------------------------------------------------------------
# Merchant advice codes (Visa / Mastercard)
# ---------------------------------------------------------------------------

MERCHANT_ADVICE_CODES: dict[str, str] = {
    "01": "New account information available — update on file and retry",
    "02": "Cannot approve at this time — retry after 72 hours",
    "03": "Do not retry — do not resubmit",
    "04": "Token requires update — update token and retry",
    "21": "Payment cancellation — do not retry",
    "24": "Retry after 1 hour",
    "25": "Retry after 24 hours",
    "26": "Retry after 48 hours",
    "27": "Retry after 72 hours",
    "28": "Retry after 30 days",
    "40": "Consumer non-reusable — obtain new credentials",
    "41": "Consumer multi-use — consumer to contact issuer",
    "42": "Maximum retry attempts reached — do not retry",
}


# ---------------------------------------------------------------------------
# BIN lookup table: prefix → (card_brand, card_type, issuing_country)
# 50 ranges covering major global BIN blocks
# ---------------------------------------------------------------------------

BIN_TABLE: dict[str, tuple[str, str, str]] = {
    # Visa
    "4000": ("visa", "credit", "US"),
    "4001": ("visa", "debit", "US"),
    "4003": ("visa", "prepaid", "US"),
    "4111": ("visa", "credit", "US"),
    "4242": ("visa", "credit", "US"),
    "4444": ("visa", "credit", "GB"),
    "4532": ("visa", "debit", "GB"),
    "4539": ("visa", "credit", "GB"),
    "4556": ("visa", "credit", "DE"),
    "4563": ("visa", "debit", "FR"),
    "4575": ("visa", "credit", "FR"),
    "4594": ("visa", "credit", "BR"),
    "4716": ("visa", "debit", "BR"),
    "4761": ("visa", "credit", "MX"),
    "4772": ("visa", "debit", "IN"),
    "4819": ("visa", "credit", "AU"),
    "4903": ("visa", "prepaid", "CA"),
    "4917": ("visa", "credit", "SG"),
    "4929": ("visa", "debit", "NL"),
    "4936": ("visa", "credit", "AE"),

    # Mastercard
    "5100": ("mastercard", "credit", "US"),
    "5200": ("mastercard", "debit", "US"),
    "5204": ("mastercard", "commercial", "US"),
    "5310": ("mastercard", "credit", "GB"),
    "5399": ("mastercard", "prepaid", "GB"),
    "5412": ("mastercard", "credit", "DE"),
    "5454": ("mastercard", "credit", "FR"),
    "5500": ("mastercard", "credit", "BR"),
    "5561": ("mastercard", "debit", "MX"),
    "5610": ("mastercard", "credit", "AU"),
    "5641": ("mastercard", "credit", "CA"),
    "5693": ("mastercard", "debit", "IN"),
    "5732": ("mastercard", "credit", "SG"),
    "5787": ("mastercard", "credit", "AE"),
    "5893": ("mastercard", "prepaid", "NL"),

    # Amex
    "3400": ("amex", "credit", "US"),
    "3411": ("amex", "credit", "US"),
    "3489": ("amex", "credit", "GB"),
    "3700": ("amex", "credit", "US"),
    "3782": ("amex", "credit", "DE"),

    # Discover
    "6011": ("discover", "credit", "US"),
    "6440": ("discover", "debit", "US"),
    "6500": ("discover", "credit", "US"),

    # JCB
    "3528": ("jcb", "credit", "JP"),
    "3589": ("jcb", "credit", "JP"),

    # UnionPay
    "6200": ("unionpay", "credit", "CN"),
    "6221": ("unionpay", "debit", "CN"),
    "6250": ("unionpay", "credit", "CN"),

    # Interac (Canada)
    "4506": ("visa", "debit", "CA"),  # Visa Debit / Interac
}


def lookup_bin(bin_prefix: str) -> tuple[str, str, str]:
    """Return (card_brand, card_type, issuing_country) for a BIN prefix.

    Tries 6-digit, then 4-digit prefix. Returns ('unknown', 'unknown', 'XX')
    if not found.
    """
    for length in (6, 4):
        key = bin_prefix[:length]
        if key in BIN_TABLE:
            return BIN_TABLE[key]
    return ("unknown", "unknown", "XX")
