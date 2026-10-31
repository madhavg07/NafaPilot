"""Optional live test. Skipped by default (pyproject addopts excludes the `live` marker).

Run manually after setting the key in .env:
    RUN_LIVE_LLM=1 pytest -m live          (PowerShell: $env:RUN_LIVE_LLM=1; pytest -m live)
"""

import os

import pytest
from dotenv import dotenv_values

from app.services.llm.gemini import GeminiProvider
from tests.llm.fixtures import anand_context

pytestmark = pytest.mark.live


@pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM") != "1", reason="set RUN_LIVE_LLM=1")
def test_live_candidates() -> None:
    # conftest blanks the key in os.environ, so read the developer's .env directly.
    env = {**dotenv_values("../.env"), **dotenv_values(".env")}
    provider = GeminiProvider(api_key=env.get("GEMINI_API_KEY"), model=env.get("GEMINI_MODEL"))
    provider.ping()
    assert provider.generate_candidates(anand_context()).candidates
