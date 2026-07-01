"""Runs the JS panel smoke test (tests/dom_stub_test.js) under Node."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_panel_js_smoke():
    script = Path(__file__).with_name("dom_stub_test.js")
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout
    assert "ALL JS SMOKE TESTS PASSED" in result.stdout
