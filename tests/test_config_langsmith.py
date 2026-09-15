import os
from unittest.mock import patch
from src.config import Settings
from src.llm import get_langsmith_client


def test_settings_langsmith_defaults():
    s = Settings(
        LANGCHAIN_TRACING_V2=False,
        LANGCHAIN_API_KEY=None,
        LANGCHAIN_PROJECT="test-project",
        LANGCHAIN_ENDPOINT="https://api.smith.langchain.com"
    )
    assert s.LANGCHAIN_TRACING_V2 is False
    assert s.LANGCHAIN_API_KEY is None
    assert s.LANGCHAIN_PROJECT == "test-project"
    assert s.LANGCHAIN_ENDPOINT == "https://api.smith.langchain.com"


def test_get_langsmith_client_when_disabled():
    with patch("src.llm.settings.LANGCHAIN_TRACING_V2", False):
        import src.llm as llm_module
        llm_module._langsmith_client = None
        client = get_langsmith_client()
        assert client is None


def test_get_langsmith_client_when_enabled():
    with patch("src.llm.settings.LANGCHAIN_TRACING_V2", True), \
         patch("src.llm.settings.LANGCHAIN_API_KEY", "lsv2_pt_dummykey_12345"), \
         patch("src.llm.settings.LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"):
        import src.llm as llm_module
        llm_module._langsmith_client = None
        client = get_langsmith_client()
        assert client is not None
        # Should return singleton on subsequent call
        assert get_langsmith_client() is client
