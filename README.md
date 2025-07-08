# Parliament MCP Server

An MCP server that roughly maps onto a subset of https://developer.parliament.uk/, as well as offering additional semantic search capabilities.

## Architecture

This project provides:
- **MCP Server**: FastMCP-based server
- **Python package**: A small python package for querying and loading parliamentary data from https://developer.parliament.uk/ into Elasticsearch
- **Elasticsearch**: Document storage and search engine, setup to automatically embed documents and allow semantic search over Hansard and Parliamentary Questions data.
- **Claude Integration**: Connect to Claude Desktop via `mcp-remote` proxy

## Features

### MCP Tools Available

The MCP Server exposes 11 tools to assist in parliamentary research:

1. **`search_constituency`** - Search for constituencies by name or get comprehensive constituency details by ID
2. **`get_election_results`** - Get election results for constituencies or specific members
3. **`search_members`** - Search for members of the Commons or Lords by various criteria
4. **`get_detailed_member_information`** - Get comprehensive member information including biography, contact, interests, and voting record
5. **`get_state_of_the_parties`** - Get state of the parties for a house on a specific date
6. **`get_government_posts`** - Get exhaustive list of all government posts and their current holders
7. **`get_opposition_posts`** - Get exhaustive list of all opposition posts and their current holders
8. **`get_departments`** - Get reference data for government departments
9. **`search_parliamentary_questions`** - Search Parliamentary Written Questions (PQs) by topic, date, party, or member
10. **`search_debates`** - Search through debate titles to find relevant debates
11. **`search_contributions`** - Search Hansard parliamentary records for actual spoken contributions during debates

## Quick Start (local elasticsearch)

You will need

- Docker and Docker Compose
- Node.js (for mcp-remote)
- Claude Desktop (or another MCP client)
- **Azure OpenAI account with API access**

Create a `.env` file by copying the `.env.example` in the project root and replace the necessary variables.

After configuring your `.env` file, run the following command for a one shot setup of the MCP server and the Elasticsearch database with some example data from June 2025.

```bash
make dev_setup_from_scratch
```

Once this is run, you can connect to the MCP server using this config
```bash
# Add this to your Claude Desktop config, or another MCP client:
# On macOS Claude Desktop config is located at `~/Library/Application\ Support/Claude/claude_desktop_config.json`
{
  "mcpServers": {
    "parliament-mcp": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:8080/mcp/", "--allow-http", "--debug"]
    }
  }
}
```

## Manual Setup

### 1. Clone, setup environment, and start services

#### Note on Elasticsearch Configuration

The system supports two methods for connecting to Elasticsearch:

1. **Local/Self-hosted**: Use `ELASTICSEARCH_HOST`, `ELASTICSEARCH_PORT`, and `ELASTICSEARCH_SCHEME`
2. **Elasticsearch Cloud**: Use `ELASTICSEARCH_CLOUD_ID` and `ELASTICSEARCH_API_KEY` (automatically configures host, port, and scheme)

```bash
# Clone the repo
git clone git@github.com:i-dot-ai/parliament-mcp.git
cd parliament-mcp

# Set up the environment and complete the .env file
cp .env.example .env
nano .env

# Start Elasticsearch and MCP server
docker-compose up --build
```

The services will be available at:
- **MCP Server**: `http://localhost:8080/mcp/`
- **Elasticsearch**: `http://localhost:9200`

### 3. Initialize Elasticsearch and Load Data

```bash
# Initialise Elasticsearch
docker compose exec mcp-server uv run parliament-mcp --log-level INFO init-elasticsearch

# Load 2025-06-23 to 2025-06-27 hansard data
docker compose exec mcp-server uv run parliament-mcp load-data hansard --from-date 2025-06-23 --to-date 2025-06-27

# Load 2025-06-23 to 2025-06-27 parliamentary questions
docker compose exec mcp-server uv run parliament-mcp --log-level WARNING load-data parliamentary-questions --from-date 2025-06-23 --to-date 2025-06-27
```

### 2. Install mcp-remote

```bash
npm install -g mcp-remote
```

### 3. Configure Claude Desktop

Add the following to your Claude Desktop configuration file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "parliament-mcp": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "http://localhost:8080/mcp/",
        "--allow-http",
        "--debug"
      ]
    }
  }
}
```

You can also generate this configuration automatically:
```bash
make mcp_claude_config
```

### 4. Restart Claude Desktop

Claude should now have access to the Parliament MCP tools.

## Development

### Prerequisites for Local Development

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Docker and Docker Compose
- Node.js (for mcp-remote)

### Local Development Setup

1. **Install uv** (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone and set up the project**:
   ```bash
   git clone <repository>
   cd parliament-mcp

   # Install dependencies with uv
   uv sync --extra dev
   ```

3. **Available Make commands**:
   ```bash
   make install           # Install all dependencies
   make test              # Run tests
   make test_integration  # Run integration tests (slow on first run)
   make lint              # Check code formatting
   make format            # Format and fix code
   make safe              # Run security checks

   # Pre-commit hooks
   make pre-commit-install  # Install pre-commit hooks
   make pre-commit         # Run pre-commit on all files

   # Docker operations
   make run             # Start services with Docker Compose
   make stop            # Stop Docker services
   make logs            # View MCP server logs

   # Development helpers
   make mcp_test        # Test MCP server connection
   make es_health       # Check Elasticsearch health
   ```

4. **Run the MCP server locally**:
   ```bash
   make run_mcp_server
   # Or directly with uv:
   cd mcp_server && uv run python app/main.py
   ```

### Project Structure

```
parliament-mcp/
├── parliament_mcp/           # Main Python package
│   ├── cli.py               # CLI interface
│   ├── models.py            # Data models
│   └── ...                  # Other modules
├── mcp_server/              # MCP server application
│   ├── app/                 # Server implementation
│   └── Dockerfile           # Container configuration
├── docker-compose.yaml      # Service orchestration
└── README.md                # This file
```

### CLI Commands

The project includes a CLI for data management:

```bash
# Initialize Elasticsearch indices and inference endpoints
parliament-mcp init-elasticsearch

# Load data with flexible date parsing
parliament-mcp load-data hansard --from-date "3 days ago" --to-date "today"
parliament-mcp load-data parliamentary-questions --from-date "2025-01-01"

# Delete all data
parliament-mcp delete-elasticsearch
```

### Data Structure

The system works with two main types of parliamentary documents:

**Parliamentary Questions** (Index: `parliamentary_questions`):
- Written Questions with semantic search on question and answer text
- Member information for asking and answering members
- Date, reference numbers, and department details

**Hansard Contributions** (Index: `hansard_contributions`):
- Spoken contributions from parliamentary debates
- Semantic search on full contribution text
- Speaker information and debate context
- House (Commons/Lords) and sitting date

**Data Loading Process**:
1. **Fetch** from Parliamentary APIs (Hansard API, Parliamentary Questions API)
2. **Transform** into structured models with computed fields
3. **Embed** using Azure OpenAI for semantic search
4. **Index** into Elasticsearch with proper mappings

Data is loaded automatically from official Parliamentary APIs - no manual document creation needed.

### Daily Data Ingestion

To keep the data in Elasticsearch up-to-date, a daily ingestion mechanism is provided. This loads the last two days of data from both `hansard` and `parliamentary-questions` sources.

To run the daily ingestion manually:

```bash
make ingest_daily
```

This runs the equivalent of:
```bash
parliament-mcp load-data hansard --from-date "2 days ago" --to-date "today"
parliament-mcp load-data parliamentary-questions --from-date "2 days ago" --to-date "today"
```

For automated daily ingestion, you can use a cron job. Here are examples for standard cron and AWS EventBridge cron.

**Standard Cron**

This cron job will run every day at 4am.

```
0 4 * * * cd /path/to/parliament-mcp && make ingest_daily
```

**AWS EventBridge Cron**

This AWS EventBridge cron expression will run every day at 4am UTC.

```
cron(0 4 * * ? *)
```

### Notes on AWS Lambda Deployment for Daily ingestion

A docker based AWS lambda image is provided to run daily ingestion tasks.

**1. Build the Lambda Container Image**

Build the Docker image using the provided `Makefile` target:

```bash
make docker_build_lambda
```
This will create a Docker image named `parliament-mcp-lambda:latest`.

**2. Test the Lambda locally**

You can test the Lambda function locally using the AWS Lambda Runtime Interface Emulator (RIE), which is included in the base image.

**Prerequisites:**
- Your local Elasticsearch container must be running (`docker compose up -d elasticsearch`).
- The Lambda container image must be built (`make docker_build_lambda`).

**Run the container:**

A convenient way to provide the necessary environment variables is to use the `--env-file` flag with your `.env` file. You still need to override the `ELASTICSEARCH_HOST` to ensure the container can connect to the service running on your local machine.

```bash
docker run --rm -p 9000:8080 \
  --env-file .env \
  -e ELASTICSEARCH_HOST="host.docker.internal" \
  parliament-mcp-lambda:latest
```

**Trigger the function:**

Open a new terminal and run the following `curl` command to send a test invocation. The `from_date` and `to_date` parameters are optional. If not provided, it will default to loading everything from the last 2 days.

```bash
curl -X POST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{"from_date": "2025-06-23", "to_date": "2025-06-27"}'
```

3. Configure the Lambda in AWS

With the image pushed to ECR, you can create the Lambda with the following configurations

  - Use `ELASTICSEARCH_HOST`, `ELASTICSEARCH_PORT`, and `ELASTICSEARCH_SCHEME` to point to your elasticsearch cluster
  - Increase the default timeout to ~10 minutes to ensure the ingestion has enough time to complete
  - Use AWS's flavour of cron to schedule the task for ~4am every day - `cron(0 4 * * ? *)`

* Remember to increase the default timeout to ~10 minutes to ensure the ingestion has enough time to complete.

## Usage Examples

Once connected to Claude, you can use natural language queries like:

**Parliamentary Questions:**
- "Search for parliamentary questions about climate change policy"
- "Find questions asked by Conservative MPs about healthcare funding"
- "Show me recent questions about education from the last week"

**Hansard Contributions:**
- "Search for contributions about the budget debate"
- "Find speeches by Keir Starmer about economic policy"
- "Show me debates from the House of Lords about immigration"

**Member Information:**
- "Get detailed information about the MP for Birmingham Edgbaston"
- "Search for Labour MPs elected in 2024"
- "Find constituency information for postcode SW1A 0AA"

**Parliamentary Structure:**
- "Show me the current government ministers"
- "Get the state of the parties in the House of Commons"
- "List all opposition shadow cabinet positions"

**General Queries:**
- "Search for debates about artificial intelligence regulation"
- "Find election results for marginal constituencies"
- "Show me government departments and their responsibilities"

### Logs and Debugging

**View server logs**:
```bash
docker-compose logs mcp-server
```

**Enable debug mode** in Claude config by adding `--debug` flag.

**Check Elasticsearch status**:
```bash
curl http://localhost:9200/_cat/health?v
# Or use the make command:
make es_health
```

## Troubleshooting

### Common Issues

**MCP Connection Issues**
- Ensure MCP server is running on port 8080
- The MCP server runs on `/{MCP_ROOT_PATH}/mcp`, not `/MCP_ROOT_PATH`
- Verify Claude Desktop configuration is correct

**Data Loading Failures**
- Check Azure OpenAI credentials in `.env` file
- Ensure Elasticsearch is running and accessible
- Verify network connectivity to Parliamentary APIs
- Use `--ll DEBUG` flag for detailed logging

**Elasticsearch issues**
- Verify inference endpoints are created: `parliament-mcp init-elasticsearch`
- Use https://elasticvue.com/ to inspect the Elasticsearch instance

## Contributing

Contributions are welcome! Please see our [Contributing Guide](CONTRIBUTING.md) for details on how to get started.

## License

MIT License - see LICENSE file for details
