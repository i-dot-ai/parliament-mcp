-include .env
export


install:
	uv sync --group dev

pre-commit-install:
	uv run pre-commit install

.PHONY: pre-commit
pre-commit:  ## Run pre-commit on all files
	uv run pre-commit run --all-files

test: install
	uv run python -m pytest --cov=parliament_mcp -v --cov-report=term-missing --cov-fail-under=0

test_integration: install
	uv run python -m pytest -s -v --with-integration

test_integration_cleanup:  ## Clean up files created by integration tests
	rm -rf .cache .pytest_cache tests/.parliament-test-qdrant-data

run_qdrant:
	docker compose up -d qdrant --wait

run_mcp_server:
	uv run parliament-mcp serve

run:
	docker compose up -d --wait

build_and_run:
	docker compose up -d --build --wait

stop:
	docker compose down

docker_remove_all:
	docker compose down -v

logs:
	docker compose logs -f mcp-server

logs_all:
	docker compose logs -f

# Qdrant Commands
init_qdrant:
	uv run parliament-mcp --log-level INFO init-qdrant

load_data_last_3_days: init_qdrant
	uv run parliament-mcp --log-level WARNING load-data hansard --from-date "3 days ago" --to-date "today"
	uv run parliament-mcp --log-level WARNING load-data parliamentary-questions --from-date "3 days ago" --to-date "today"

load_data_last_week: init_qdrant
	uv run parliament-mcp --log-level WARNING load-data hansard --from-date "1 week ago" --to-date "today"
	uv run parliament-mcp --log-level WARNING load-data parliamentary-questions --from-date "1 week ago" --to-date "today"

load_reference_week: init_qdrant
	uv run parliament-mcp --log-level WARNING load-data hansard --from-date 2025-06-23 --to-date 2025-06-27
	uv run parliament-mcp --log-level WARNING load-data parliamentary-questions --from-date 2025-06-23 --to-date 2025-06-27

load_data_since_2020: init_qdrant
	uv run parliament-mcp --log-level WARNING load-data hansard --from-date 2020-01-01 --to-date "today"
	uv run parliament-mcp --log-level WARNING load-data parliamentary-questions --from-date 2020-01-01 --to-date "today"

.PHONY: ingest_daily
ingest_daily: init_qdrant
	uv run parliament-mcp --log-level WARNING load-data hansard --from-date "2 days ago" --to-date "today"
	uv run parliament-mcp --log-level WARNING load-data parliamentary-questions --from-date "2 days ago" --to-date "today"

delete_qdrant_data:
	uv run parliament-mcp --log-level WARNING delete-qdrant

# MCP Development Commands
.PHONY: mcp_test
mcp_test:  ## Test MCP server connection
	npx mcp-remote http://localhost:8080/mcp/ --allow-http --debug

.PHONY: mcp_claude_config
mcp_claude_config:  ## Show Claude Desktop config
	@echo "\033[1;34mClaude Desktop Configuration\033[0m"
	@echo "\033[90mLocation on macOS: ~/Library/Application\ Support/Claude/claude_desktop_config.json\033[0m"
	@echo '{'
	@echo '  "mcpServers": {'
	@echo '    "parliament-mcp": {'
	@echo '      "command": "npx",'
	@echo '      "args": ["mcp-remote", "http://localhost:8080/mcp/", "--allow-http", "--debug"]'
	@echo '    }'
	@echo '  }'
	@echo '}'

# Development helper: Complete setup from scratch
dev_setup_from_scratch: docker_remove_all build_and_run load_reference_week mcp_claude_config

.PHONY: qdrant_health
qdrant_health:  ## Check Qdrant health
	curl -s http://localhost:6333/ | jq

.PHONY: lint
lint:  ## Check code formatting & linting
	uv run ruff format . --check
	uv run ruff check .

.PHONY: format
format:  ## Format and fix code
	uv run ruff format .
	uv run ruff check . --fix

.PHONY: safe
safe:  ## Run security checks
	uv run bandit -ll -r ./parliament_mcp



.PHONY: generate_aws_diagram
generate_aws_diagram:
	uv lock
	uv sync --extra dev
	uv run python terraform/diagram_script.py

# Docker

ECR_REPO_NAME=$(APP_NAME)-$(service)
ECR_URL=$(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com
ECR_REPO_URL=$(ECR_URL)/$(ECR_REPO_NAME)

IMAGE_TAG=$$(git rev-parse HEAD)
IMAGE=$(ECR_REPO_URL):$(IMAGE_TAG)

# Generate version string for the application
SHORT_COMMIT=$$(git rev-parse --short HEAD)
ISO_DATE=$$(date -u +%Y%m%d)
APP_VERSION=$(service)-$(SHORT_COMMIT)-$(ISO_DATE)

DOCKER_BUILDER_CONTAINER=$(APP_NAME)
cache ?= ./.build-cache
APP_CACHE_DIR = $(cache)/$(APP_NAME)/$(service)


docker_login:
	aws ecr get-login-password --region $(AWS_REGION) | docker login --username AWS --password-stdin $(ECR_URL)

docker_build: ## Build the docker container for the specified service when running in CI/CD
	@if [ "$(service)" = "lambda" ]; then \
		DOCKER_BUILDKIT=1 docker buildx build --platform linux/amd64 --load --builder=$(DOCKER_BUILDER_CONTAINER) -t $(IMAGE) \
		--build-arg VERSION="$(APP_VERSION)" \
		--cache-to type=local,dest=$(APP_CACHE_DIR) \
		--cache-from type=local,src=$(APP_CACHE_DIR) -f Dockerfile.lambda .; \
	elif [ "$(service)" = "mcp_server" ]; then \
		DOCKER_BUILDKIT=1 docker buildx build --platform linux/amd64 --load --builder=$(DOCKER_BUILDER_CONTAINER) -t $(IMAGE) \
		--build-arg VERSION="$(APP_VERSION)" \
		--cache-to type=local,dest=$(APP_CACHE_DIR) \
		--cache-from type=local,src=$(APP_CACHE_DIR) -f Dockerfile.mcp-server .; \
	fi

docker_build_local: ## Build the docker container for the specified service locally
	@if [ "$(service)" = "lambda" ]; then \
		DOCKER_BUILDKIT=1 docker build -t $(IMAGE) --build-arg VERSION="$(APP_VERSION)" -f Dockerfile.lambda .; \
	elif [ "$(service)" = "mcp_server" ]; then \
		DOCKER_BUILDKIT=1 docker build -t $(IMAGE) --build-arg VERSION="$(APP_VERSION)" -f Dockerfile.mcp-server .; \
	fi

docker_build_lambda: ## Build the docker container for the lambda function
	DOCKER_BUILDKIT=1 docker build -t $(APP_NAME)-lambda:latest -f Dockerfile.lambda .


docker_push:
	docker push $(IMAGE)

docker_tag_is_present_on_image:
	aws ecr describe-images --repository-name $(repo) --image-ids imageTag=$(IMAGE_TAG) --query 'imageDetails[].imageTags' | jq -e '.[]|any(. == "$(tag)")' >/dev/null

check_docker_tag_exists:
	if ! make docker_tag_is_present_on_image tag=$(IMAGE_TAG) 2>/dev/null; then \
		echo "Error: ECR tag $(IMAGE_TAG) does not exist." && exit 1; \
	fi

docker_update_tag: ## Tag the docker image with the specified tag
	# repo and tag variable are set from git-hub core workflow. example: repo=ecr-repo-name, tag=dev
	if make docker_tag_is_present_on_image 2>/dev/null; then echo "Image already tagged with $(tag)" && exit 0; fi && \
	MANIFEST=$$(aws ecr batch-get-image --repository-name $(repo) --image-ids imageTag=$(IMAGE_TAG) --query 'images[].imageManifest' --output text) && \
	aws ecr put-image --repository-name $(repo) --image-tag $(tag) --image-manifest "$$MANIFEST"

docker_echo:
	echo $($(value))

## Terraform

ifndef env
override env = default
endif
workspace = $(env)
tf_build_args =-var "image_tag=$(IMAGE_TAG)" -var-file="variables/global.tfvars" -var-file="variables/$(env).tfvars"
TF_BACKEND_CONFIG=backend.hcl

tf_set_workspace:
	terraform -chdir=terraform/ workspace select $(workspace)

tf_new_workspace:
	terraform -chdir=terraform/ workspace new $(workspace)

tf_set_or_create_workspace:
	make tf_set_workspace || make tf_new_workspace

tf_init_and_set_workspace:
	make tf_init && make tf_set_or_create_workspace

.PHONY: tf_init
tf_init:
	terraform -chdir=./terraform/ init \
		-backend-config=$(TF_BACKEND_CONFIG) \
		-backend-config="dynamodb_table=i-dot-ai-$(env)-dynamo-lock" \
		-reconfigure \

.PHONY: tf_fmt
tf_fmt:
	terraform fmt

.PHONY: tf_plan
tf_plan:
	make tf_init_and_set_workspace && \
	terraform -chdir=./terraform/ plan ${tf_build_args} ${args}

.PHONY: tf_apply
tf_apply:
	make tf_init_and_set_workspace && \
	terraform -chdir=./terraform/ apply ${tf_build_args} ${args}

.PHONY: tf_auto_apply
tf_auto_apply:  ## Auto apply terraform
	make check_docker_tag_exists repo=$(ECR_REPO_NAME)
	make tf_init_and_set_workspace && \
	terraform -chdir=./terraform/ apply  ${tf_build_args} ${args} -auto-approve


## Release app
.PHONY: release
release:
	chmod +x ./release.sh && ./release.sh $(env)
