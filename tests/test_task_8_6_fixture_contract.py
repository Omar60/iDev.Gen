"""The checked-in task 8.6 HTTP fixtures must remain reproducible."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_task_8_6_fixture_is_an_exact_normalized_backend_capture():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "task8_6_fixtures.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Verified frontend" in result.stdout
