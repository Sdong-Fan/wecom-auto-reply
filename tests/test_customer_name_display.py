"""TDD: add_record must use real customer_name, not hardcoded '客户'.

Bug: main.py 3 places hardcode '客户' as first arg to add_record.
Should pass customer_name variable instead.
"""

from pathlib import Path


def test_add_record_uses_customer_name_not_placeholder():
    """All add_record calls in main path must use customer_name, not '客户'."""
    source = Path("main.py").read_text(encoding="utf-8")

    # Find all add_record calls in the main scan path (after ">>> 客户消息")
    marker = 'log.info(">>> 客户消息")'
    idx = source.find(marker)
    assert idx >= 0, "main.py must have '>>> 客户消息' log"

    scan_section = source[idx:]

    # In the scan section, add_record should NOT have hardcoded "客户"
    import re
    calls = re.findall(r'add_record\(\s*"客户"', scan_section)
    assert len(calls) == 0, (
        f"Found {len(calls)} add_record calls with hardcoded '客户' "
        f"in scan section. Should use customer_name instead."
    )
