"""Tests for response_codes module."""

from payment_router.response_codes import ISO_8583_CODES, is_soft_decline, is_approved, lookup_bin


def test_all_codes_have_three_fields():
    for code, entry in ISO_8583_CODES.items():
        assert len(entry) == 3, f"Code {code} missing fields"


def test_approval_codes():
    assert is_approved("00")
    assert not is_approved("05")
    assert not is_approved("51")


def test_soft_decline_codes():
    assert is_soft_decline("05")
    assert is_soft_decline("51")
    assert is_soft_decline("91")
    assert not is_soft_decline("00")
    assert not is_soft_decline("41")  # stolen card — hard decline


def test_bin_lookup_visa_us():
    brand, card_type, country = lookup_bin("4111")
    assert brand == "visa"
    assert country == "US"


def test_bin_lookup_mastercard():
    brand, card_type, country = lookup_bin("5100")
    assert brand == "mastercard"


def test_bin_lookup_unknown():
    brand, card_type, country = lookup_bin("9999")
    assert brand == "unknown"
    assert country == "XX"
