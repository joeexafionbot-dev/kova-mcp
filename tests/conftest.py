import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def example_var() -> dict:
    """The official wiki example of a full /api/v1/var discovery tree."""
    return json.loads((FIXTURES / "example_var.json").read_text(encoding="utf-8"))
