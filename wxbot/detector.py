"""Backward-compatible wrapper — delegates to wxbot.detector package.

All original imports from ``wxbot.detector`` continue to work.
For new code, import directly from ``wxbot.detector`` submodules.
"""
# Simply re-export everything from the package.
# `import wxbot.detector` now resolves to the package via __init__.py.
from wxbot.detector import *  # noqa: F401, F403
from wxbot.detector._message_detector import MessageDetector  # noqa: F401, E402

__all__ = []  # populated by the wildcard import above
