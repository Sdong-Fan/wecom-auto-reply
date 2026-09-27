"""TDD: Empty customer_name must NOT skip bubble is_customer check.

Bug: When OCR returns '' (no text), clean_name_text('') returns ''.
'' != '客户' is True, so the bubble check is incorrectly skipped.
Red dot did NOT confirm a customer — OCR just failed.

Fix: customer_name not in ('', '客户').
"""

from pathlib import Path


def test_empty_customer_name_does_not_skip_bubble_check():
    """When customer_name is '', still run bubble is_customer check."""
    source = Path("main.py").read_text(encoding="utf-8")

    # Find the customer check line
    marker = '9. 客户判定'
    idx = source.find(marker)
    assert idx >= 0, "Could not find customer check section"

    section = source[idx:idx + 400]

    # Must check for empty string too (using `not in` tuple check)
    assert "not in" in section, \
        "customer_name must be checked for empty string as well as '客户'"
    assert '""' in section, \
        "Must check for empty string"
    assert '客户' in section, \
        "Must still check for '客户' default value"
