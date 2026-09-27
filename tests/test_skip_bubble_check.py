"""TDD: Skip bubble is_customer check when red dot already confirmed.

Bug: Red dot path sets customer_name (e.g. 'Will@微信'), but the
bubble is_customer() check can override it to '非客户' when the
bubble nick OCR fails or doesn't match.

Fix: When customer_name is already set by red dot path (not default
'客户'), skip the bubble is_customer check.
"""

from pathlib import Path


def test_skip_bubble_check_when_red_dot_confirmed():
    """When customer_name is set by red dot, skip is_customer check."""
    source = Path("main.py").read_text(encoding="utf-8")

    # Find the is_customer check section
    marker = "9. 客户判定"
    idx = source.find(marker)
    assert idx >= 0, "Could not find customer check section"

    # Get the section around is_customer
    section = source[idx:idx + 500]

    # Must check customer_name before is_customer
    assert "customer_name" in section, \
        "Customer check section must reference customer_name from red dot path"

    # The is_customer check must be conditional on customer_name
    # (skip if red dot already confirmed)
    lines = section.split("\n")
    has_conditional = False
    for line in lines:
        if "customer_name" in line and ("!=" in line or "==" in line or "not " in line):
            has_conditional = True
            break

    assert has_conditional, \
        "is_customer must be conditional: skip when red dot already confirmed customer"
