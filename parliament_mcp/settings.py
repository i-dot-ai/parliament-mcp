import logging
import os
from functools import lru_cache

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


@lru_cache
def get_ssm_parameter(parameter_name: str, region: str = "eu-west-2") -> str:
    """Fetch a parameter from AWS Systems Manager Parameter Store."""
    try:
        ssm = boto3.client("ssm", region_name=region)
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except (ClientError, BotoCoreError) as e:
        logger.warning("Could not fetch SSM parameter %s: %s", parameter_name, e)
        return ""


def get_environment_or_ssm(env_var_name: str, ssm_path: str | None = None, default: str = "") -> str:
    """Get value from environment variable or fall back to SSM parameter."""
    env_value = os.environ.get(env_var_name)
    if env_value:
        return env_value

    if ssm_path and os.environ.get("AWS_REGION"):
        return get_ssm_parameter(ssm_path, os.environ.get("AWS_REGION"))

    return default


class ParliamentMCPSettings(BaseSettings):
    """Configuration settings for Parliament MCP application with environment-based loading."""

    APP_NAME: str
    AWS_ACCOUNT_ID: str | None = None
    AWS_REGION: str = "eu-west-2"
    ENVIRONMENT: str = "local"

    # Use SSM for sensitive parameters in AWS environments
    @property
    def SENTRY_DSN(self) -> str | None:
        return get_environment_or_ssm("SENTRY_DSN", f"/{self._get_project_name()}/env_secrets/SENTRY_DSN")

    @property
    def AZURE_OPENAI_API_KEY(self) -> str:
        return get_environment_or_ssm(
            "AZURE_OPENAI_API_KEY", f"/{self._get_project_name()}/env_secrets/AZURE_OPENAI_API_KEY"
        )

    @property
    def AZURE_OPENAI_ENDPOINT(self) -> str:
        return get_environment_or_ssm(
            "AZURE_OPENAI_ENDPOINT", f"/{self._get_project_name()}/env_secrets/AZURE_OPENAI_ENDPOINT"
        )

    @property
    def AZURE_OPENAI_RESOURCE_NAME(self) -> str:
        return get_environment_or_ssm(
            "AZURE_OPENAI_RESOURCE_NAME", f"/{self._get_project_name()}/env_secrets/AZURE_OPENAI_RESOURCE_NAME"
        )

    @property
    def AZURE_OPENAI_EMBEDDING_MODEL(self) -> str:
        return get_environment_or_ssm(
            "AZURE_OPENAI_EMBEDDING_MODEL", f"/{self._get_project_name()}/env_secrets/AZURE_OPENAI_EMBEDDING_MODEL"
        )

    @property
    def AZURE_OPENAI_API_VERSION(self) -> str:
        return get_environment_or_ssm(
            "AZURE_OPENAI_API_VERSION", f"/{self._get_project_name()}/env_secrets/AZURE_OPENAI_API_VERSION", "preview"
        )

    # Elasticsearch connection settings
    @property
    def ELASTICSEARCH_CLOUD_ID(self) -> str | None:
        return get_environment_or_ssm(
            "ELASTICSEARCH_CLOUD_ID", f"/{self._get_project_name()}/env_secrets/ELASTICSEARCH_CLOUD_ID"
        )

    @property
    def ELASTICSEARCH_API_KEY(self) -> str | None:
        return get_environment_or_ssm(
            "ELASTICSEARCH_API_KEY", f"/{self._get_project_name()}/env_secrets/ELASTICSEARCH_API_KEY"
        )

    @property
    def ELASTICSEARCH_HOST(self) -> str | None:
        return get_environment_or_ssm(
            "ELASTICSEARCH_HOST", f"/{self._get_project_name()}/env_secrets/ELASTICSEARCH_HOST", "localhost"
        )

    @property
    def ELASTICSEARCH_PORT(self) -> int:
        port_str = get_environment_or_ssm(
            "ELASTICSEARCH_PORT", f"/{self._get_project_name()}/env_secrets/ELASTICSEARCH_PORT", "9200"
        )
        return int(port_str) if port_str.isdigit() else 9200

    ELASTICSEARCH_SCHEME: str = "http"

    AUTH_PROVIDER_PUBLIC_KEY: str | None = None
    DISABLE_AUTH_SIGNATURE_VERIFICATION: bool = ENVIRONMENT == "local"

    def _get_project_name(self) -> str:
        """Get the project name from environment or use default."""
        return os.environ.get("PROJECT_NAME", "i-dot-ai-dev-parliament-mcp")

    # Set to 0 for single-node cluster
    ELASTICSEARCH_INDEX_PATTERN: str = "parliament_mcp_*"
    ELASTICSEARCH_NUMBER_OF_REPLICAS: int = 0

    EMBEDDING_INFERENCE_ENDPOINT_NAME: str = "openai-embedding-inference"
    EMBEDDING_DIMENSIONS: int = 1024

    # Chunking settings
    # See https://www.elastic.co/search-labs/blog/elasticsearch-chunking-inference-api-endpoints
    CHUNK_SIZE: int = 300
    SENTENCE_OVERLAP: int = 1
    CHUNK_STRATEGY: str = "sentence"

    PARLIAMENTARY_QUESTIONS_INDEX: str = "parliament_mcp_parliamentary_questions"
    HANSARD_CONTRIBUTIONS_INDEX: str = "parliament_mcp_hansard_contributions"

    # MCP settings
    MCP_HOST: str = "0.0.0.0"  # nosec B104 - Binding to all interfaces is intentional for containerized deployment
    MCP_PORT: int = 8080

    # The MCP server can be accessed at /{MCP_ROOT_PATH}/mcp
    MCP_ROOT_PATH: str = "/"

    # Rate limiting settings for parliament.uk API.
    HTTP_MAX_RATE_PER_SECOND: float = 10

    # Load environment variables from .env file in local environment
    # from pydantic_settings import SettingsConfigDict
    if ENVIRONMENT == "local":
        model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = ParliamentMCPSettings()
