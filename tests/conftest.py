from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_html():
    def _load(name: str) -> str:
        return (FIXTURES_DIR / name).read_text(encoding="utf-8")

    return _load
