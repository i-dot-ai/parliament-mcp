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
    logger.debug("Attempting to fetch SSM parameter: %s from region: %s", parameter_name, region)
    try:
        ssm = boto3.client("ssm", region_name=region)
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        value = response["Parameter"]["Value"]
        # Log parameter fetch success (mask sensitive values)
        if "API_KEY" in parameter_name or "key" in parameter_name.lower():
            logger.info(
                "Successfully fetched SSM parameter %s: ***REDACTED*** (length: %d)", parameter_name, len(value)
            )
        else:
            logger.info("Successfully fetched SSM parameter %s: %s", parameter_name, value)
    except (ClientError, BotoCoreError):
        logger.exception("Failed to fetch SSM parameter %s", parameter_name)
        return ""
    else:
        return value


def get_environment_or_ssm(env_var_name: str, ssm_path: str | None = None, default: str = "") -> str:
    """Get value from environment variable or fall back to SSM parameter."""
    env_value = os.environ.get(env_var_name)
    if env_value:
        logger.debug("Using environment variable %s (value exists)", env_var_name)
        return env_value

    # Only use SSM if not in local environment
    environment = os.environ.get("ENVIRONMENT", "local")
    aws_region = os.environ.get("AWS_REGION")

    logger.debug(
        "Environment check for %s: ENVIRONMENT=%s, AWS_REGION=%s, ssm_path=%s",
        env_var_name,
        environment,
        aws_region,
        ssm_path,
    )

    if ssm_path and aws_region and environment != "local":
        logger.info("Fetching %s from SSM path: %s", env_var_name, ssm_path)
        return get_ssm_parameter(ssm_path, aws_region)

    logger.debug("Using default value for %s (env=%s, region=%s)", env_var_name, environment, aws_region)
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
            "AZURE_OPENAI_API_KEY",
            f"/{self._get_project_name()}/env_secrets/AZURE_OPENAI_API_KEY",
        )

    @property
    def AZURE_OPENAI_ENDPOINT(self) -> str:
        return get_environment_or_ssm(
            "AZURE_OPENAI_ENDPOINT",
            f"/{self._get_project_name()}/env_secrets/AZURE_OPENAI_ENDPOINT",
        )

    @property
    def AZURE_OPENAI_EMBEDDING_MODEL(self) -> str:
        return get_environment_or_ssm(
            "AZURE_OPENAI_EMBEDDING_MODEL",
            f"/{self._get_project_name()}/env_secrets/AZURE_OPENAI_EMBEDDING_MODEL",
        )

    @property
    def AZURE_OPENAI_API_VERSION(self) -> str:
        return get_environment_or_ssm(
            "AZURE_OPENAI_API_VERSION",
            f"/{self._get_project_name()}/env_secrets/AZURE_OPENAI_API_VERSION",
            "preview",
        )

    # Qdrant connection settings
    @property
    def QDRANT_URL(self) -> str | None:
        logger.info("Accessing QDRANT_URL property")
        url = get_environment_or_ssm("QDRANT_URL", f"/{self._get_project_name()}/env_secrets/QDRANT_URL")
        if url:
            logger.info("QDRANT_URL resolved to: %s", url)
        else:
            logger.warning("QDRANT_URL is empty or None")
        return url

    @property
    def QDRANT_API_KEY(self) -> str | None:
        logger.info("Accessing QDRANT_API_KEY property")
        api_key = get_environment_or_ssm("QDRANT_API_KEY", f"/{self._get_project_name()}/env_secrets/QDRANT_API_KEY")
        if api_key:
            logger.info("QDRANT_API_KEY resolved successfully (length: %d)", len(api_key))
        else:
            logger.warning("QDRANT_API_KEY is empty or None")
        return api_key

    AUTH_PROVIDER_PUBLIC_KEY: str | None = None
    DISABLE_AUTH_SIGNATURE_VERIFICATION: bool = ENVIRONMENT == "local"

    def _get_project_name(self) -> str:
        """Get the project name from environment or use default."""
        return os.environ.get("PROJECT_NAME", "i-dot-ai-dev-parliament-mcp")

    # Qdrant collection names
    QDRANT_COLLECTION_PREFIX: str = "parliament_mcp_"

    EMBEDDING_DIMENSIONS: int = 1024

    # Sparse text embedding model
    SPARSE_TEXT_EMBEDDING_MODEL: str = "Qdrant/bm25"

    # Chunking settings
    # See https://www.elastic.co/search-labs/blog/elasticsearch-chunking-inference-api-endpoints
    CHUNK_SIZE: int = 300
    SENTENCE_OVERLAP: int = 1
    CHUNK_STRATEGY: str = "sentence"

    PARLIAMENTARY_QUESTIONS_COLLECTION: str = "parliament_mcp_parliamentary_questions"
    HANSARD_CONTRIBUTIONS_COLLECTION: str = "parliament_mcp_hansard_contributions"

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

# Log environment detection on settings initialization
logger.info("Parliament MCP Settings initialized:")
logger.info("  - ENVIRONMENT: %s", settings.ENVIRONMENT)
logger.info("  - AWS_REGION: %s", settings.AWS_REGION)
logger.info("  - PROJECT_NAME: %s", settings._get_project_name())  # noqa: SLF001
logger.info("  - Running in Lambda: %s", "AWS_LAMBDA_FUNCTION_NAME" in os.environ)
if "AWS_LAMBDA_FUNCTION_NAME" in os.environ:
    logger.info("  - Lambda Function: %s", os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
