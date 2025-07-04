FROM public.ecr.aws/docker/library/python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV COLUMNS=200

# Install system dependencies and uv
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    build-essential && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir uv

# Set the working directory
WORKDIR /app

# Copy the library package first (for better caching)
COPY parliament_mcp parliament_mcp
COPY pyproject.toml uv.lock ./

# Copy service-specific files
COPY mcp_server/ mcp_server/

# Install service dependencies
WORKDIR /app/mcp_server
RUN uv sync --frozen

# Set Python path to include the app directory
ENV PYTHONPATH=/app

# Command to run the MCP server
CMD ["uv", "run", "python", "app/main.py", "--no-reload"]
