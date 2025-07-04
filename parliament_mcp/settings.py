from pydantic_settings import BaseSettings, SettingsConfigDict


class ParliamentMCPSettings(BaseSettings):
    """Configuration settings for Parliament MCP application with environment-based loading."""

    APP_NAME: str
    AWS_ACCOUNT_ID: str | None = None
    AWS_REGION: str
    ENVIRONMENT: str = "local"
    SENTRY_DSN: str | None = None
    AUTH_PROVIDER_PUBLIC_KEY: str | None = None
    DISABLE_AUTH_SIGNATURE_VERIFICATION: bool | None = ENVIRONMENT in ["local", "integration-test"]

    AZURE_OPENAI_API_KEY: str
    AZURE_OPENAI_ENDPOINT: str
    AZURE_OPENAI_RESOURCE_NAME: str
    AZURE_OPENAI_EMBEDDING_MODEL: str
    AZURE_OPENAI_API_VERSION: str = "preview"

    # Elasticsearch connection settings
    # Cloud connection (takes precedence if both are set)
    ELASTICSEARCH_CLOUD_ID: str | None = None
    ELASTICSEARCH_API_KEY: str | None = None

    # Local/direct connection (fallback)
    ELASTICSEARCH_HOST: str | None = "localhost"
    ELASTICSEARCH_PORT: int = 9200
    ELASTICSEARCH_SCHEME: str = "http"

    # Set to 0 for single-node cluster
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
