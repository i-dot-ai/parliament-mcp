"""Tests for ParliamentMCPSettings embedding backend validation."""

import pytest
from pydantic import ValidationError

from parliament_mcp.settings import ParliamentMCPSettings


class TestEmbeddingBackendDefault:
    """Test that the default embedding backend is azure_openai."""

    def test_default_backend_is_azure_openai(self, monkeypatch):
        monkeypatch.delenv("EMBEDDING_BACKEND", raising=False)
        monkeypatch.delenv("BEDROCK_EMBEDDING_MODEL_ID", raising=False)
        settings = ParliamentMCPSettings(_env_file=None)
        assert settings.EMBEDDING_BACKEND == "azure_openai"


class TestEmbeddingBackendValidation:
    """Test validation of EMBEDDING_BACKEND field."""

    def test_invalid_backend_raises_validation_error(self):
        with pytest.raises(ValidationError):
            ParliamentMCPSettings(EMBEDDING_BACKEND="invalid_backend", _env_file=None)


class TestBedrockBackendValidation:
    """Test bedrock backend requires BEDROCK_EMBEDDING_MODEL_ID."""

    def test_bedrock_without_model_id_raises_validation_error(self, monkeypatch):
        monkeypatch.delenv("BEDROCK_EMBEDDING_MODEL_ID", raising=False)
        with pytest.raises(ValidationError, match="BEDROCK_EMBEDDING_MODEL_ID is required"):
            ParliamentMCPSettings(EMBEDDING_BACKEND="bedrock", _env_file=None)

    def test_bedrock_with_model_id_succeeds(self):
        settings = ParliamentMCPSettings(
            EMBEDDING_BACKEND="bedrock",
            BEDROCK_EMBEDDING_MODEL_ID="amazon.titan-embed-text-v2:0",
            _env_file=None,
        )
        assert settings.EMBEDDING_BACKEND == "bedrock"
        assert settings.BEDROCK_EMBEDDING_MODEL_ID == "amazon.titan-embed-text-v2:0"


class TestAzureOpenAIBackendValidation:
    """Test azure_openai backend does not require Bedrock fields."""

    def test_azure_openai_does_not_require_bedrock_fields(self, monkeypatch):
        monkeypatch.delenv("BEDROCK_EMBEDDING_MODEL_ID", raising=False)
        settings = ParliamentMCPSettings(
            EMBEDDING_BACKEND="azure_openai",
            _env_file=None,
        )
        assert settings.EMBEDDING_BACKEND == "azure_openai"
        assert settings.BEDROCK_EMBEDDING_MODEL_ID is None
