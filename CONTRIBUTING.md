# Contributing to `parliament-mcp`

You can contribute in many ways:

# Types of Contributions

## Report Bugs

Report bugs at https://github.com/i-dot-ai/parliament-mcp/issues

If you are reporting a bug, please include:

- Any details about your local setup that might be helpful in troubleshooting.
- Detailed steps to reproduce the bug.

## Fix Bugs

Look through the GitHub issues for bugs.
Anything tagged with "bug" and "help wanted" is open to whoever wants to implement a fix for it.

## Implement Features

Look through the GitHub issues for features.
Anything tagged with "enhancement" and "help wanted" is open to whoever wants to implement it.

## Submit Feedback

The best way to send feedback is to file an issue at https://github.com/i-dot-ai/parliament-mcp/issues.

If you are proposing a new feature:

- Explain in detail how it would work.
- Keep the scope as narrow as possible, to make it easier to implement.

# Get Started!

Ready to contribute? Here's how to set up `parliament-mcp` for local development.

## Initial Setup

1. Fork the `parliament-mcp` repo on GitHub.

2. Clone your fork locally:

```bash
cd <directory_in_which_repo_should_be_created>
git clone git@github.com:YOUR_NAME/parliament-mcp.git
cd parliament-mcp
```

3. **Follow the development setup instructions in the [README.md](README.md#development)** to:
   - Install dependencies with `uv`
   - Set up your environment variables
   - Start the development environment

4. Install pre-commit to run linters/formatters at commit time:

```bash
make pre-commit-install
```

5. Create a branch for local development:

```bash
git checkout -b name-of-your-bugfix-or-feature
```

## Development Workflow

6. Make your changes locally and add test cases for your added functionality.

7. Before committing, run the quality checks:

```bash
make lint               # Check code formatting
make test               # Run unit tests
make test_integration   # Run integration tests (slow on first run)
make safe               # Run security checks
make format             # Format code
```

8. Test your MCP server changes:

```bash
make mcp_test    # Test MCP server connection
make es_health   # Check Elasticsearch health
make logs        # View server logs
```

9. Commit your changes and push your branch to GitHub:

```bash
git add .
git commit -m "Your detailed description of your changes."
git push origin name-of-your-bugfix-or-feature
```

10. Submit a pull request through the GitHub website.

# MCP Server Development Guidelines

When contributing to the MCP server functionality, please follow these additional guidelines:

## Adding New MCP Tools

1. **Tool Definition**: Add new tools to `parliament_mcp/mcp_server/api.py` following the existing pattern
2. **Handler Implementation**: Implement the actual functionality in `parliament_mcp/mcp_server/handlers.py`
3. **Testing**: Add tests to `tests/mcp_server/` for any new tools or handlers
4. **Documentation**: Update the README.md to document the new tool in the "MCP Tools Available" section

## Data Loading and Elasticsearch

1. **Models**: Add new data models to `parliament_mcp/models.py` if needed
2. **Data Loaders**: Implement new data loaders in `parliament_mcp/data_loaders.py`
3. **CLI Integration**: Add CLI commands to `parliament_mcp/cli.py` for new data types
4. **Index Management**: Update Elasticsearch initialization if new indices are needed

## Environment and Configuration

1. **Settings**: Add new configuration options to `parliament_mcp/settings.py`
2. **Environment Variables**: Document new environment variables in the README.md
3. **Docker**: Update `docker-compose.yaml` if new services are needed

## Local Testing

Before submitting your changes, ensure:

1. **MCP Server starts**: `make run_mcp_server` works without errors
2. **Elasticsearch connectivity**: `make es_health` shows healthy status
3. **Data loading**: Test data loading commands work correctly
4. **MCP tools function**: Test your new tools via `make mcp_test` or Claude Desktop

# Pull Request Guidelines

Before you submit a pull request, check that it meets these guidelines:

1. The pull request should include tests.

2. If the pull request adds functionality, the docs should be updated.
   Put your new functionality into a function with a docstring, and add the feature to the list in `README.md`.

3. **MCP-specific requirements**:
   - New MCP tools should be documented with clear descriptions
   - Any new data types should include sample CLI commands
   - Environment variable changes should be documented
