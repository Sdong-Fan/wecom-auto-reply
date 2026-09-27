"""Tests verifying watchdog.py is removed and main.py no longer starts it.

After removing the watchdog, main.py should:
- NOT import or call _start_watchdog()
- NOT reference watchdog.pid or heartbeat.txt for monitoring
- Be a standalone process with no external process manager
"""

from pathlib import Path

import pytest


class TestWatchdogRemoved:
    """watchdog.py should not exist."""

    def test_watchdog_file_deleted(self):
        """scripts/watchdog.py should not exist."""
        assert not Path("scripts/watchdog.py").exists(), (
            "scripts/watchdog.py should be deleted"
        )


class TestMainNoWatchdog:
    """main.py should not reference watchdog."""

    def test_main_no_start_watchdog_call(self):
        """main.py should not call _start_watchdog()."""
        source = Path("main.py").read_text(encoding="utf-8")
        assert "_start_watchdog()" not in source, (
            "main.py should not call _start_watchdog()"
        )

    def test_main_no_start_watchdog_function(self):
        """main.py should not define _start_watchdog()."""
        source = Path("main.py").read_text(encoding="utf-8")
        assert "def _start_watchdog" not in source, (
            "main.py should not define _start_watchdog()"
        )

    def test_main_no_watchdog_pid_reference(self):
        """main.py should not reference watchdog.pid."""
        source = Path("main.py").read_text(encoding="utf-8")
        assert "watchdog.pid" not in source, (
            "main.py should not reference watchdog.pid"
        )

    def test_main_no_heartbeat_write(self):
        """main.py should not write heartbeat.txt."""
        source = Path("main.py").read_text(encoding="utf-8")
        # Allow reading heartbeat (for cleanup) but not writing
        assert "heartbeat.txt" not in source or "_write_heartbeat" not in source, (
            "main.py should not have heartbeat write mechanism"
        )


class TestWatchdogTestsDeleted:
    """All watchdog-related test files should be deleted."""

    @pytest.mark.parametrize("test_file", [
        "tests/test_watchdog.py",
        "tests/test_watchdog_detach.py",
        "tests/test_watchdog_kill_error.py",
        "tests/test_watchdog_logging.py",
        "tests/test_watchdog_loop_timing.py",
        "tests/test_watchdog_pid_error.py",
        "tests/test_watchdog_restart_no_detach.py",
        "tests/test_start_watchdog_kills_old.py",
    ])
    def test_watchdog_test_deleted(self, test_file):
        """Watchdog test files should not exist."""
        assert not Path(test_file).exists(), (
            f"{test_file} should be deleted"
        )
