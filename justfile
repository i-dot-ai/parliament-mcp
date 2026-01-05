# Justfile for parliament-mcp deployments

# Configuration
APP_NAME := "parliament-mcp"

# Show available commands
default:
    @just --list

# Check AWS authentication (dependency for AWS commands)
check-aws-auth:
    #!/usr/bin/env bash
    if ! aws sts get-caller-identity --no-cli-pager >/dev/null 2>&1; then
        echo "❌ AWS authentication required!"
        echo "   Please run: aws-vault exec ai-engineer-role"
        exit 1
    fi

# Full deployment: wait for build, trigger release, wait for release, force deployment
# Usage: just deploy [env=dev]
deploy env="dev": check-aws-auth
    #!/usr/bin/env bash
    set -euo pipefail

    echo "🚀 Starting deployment to {{env}}"

    # Check prerequisites
    command -v gh >/dev/null || { echo "❌ gh CLI required"; exit 1; }
    command -v jq >/dev/null || { echo "❌ jq required"; exit 1; }

    COMMIT=$(git rev-parse HEAD)

    # Step 1: Wait for build
    echo ""
    echo "📦 [1/4] Waiting for build..."
    BUILD_RUN=$(gh run list --workflow=build.yml --limit=20 --json headSha,status,conclusion,databaseId | \
                jq -r ".[] | select(.headSha == \"$COMMIT\") | .databaseId" | head -1)

    if [ -z "$BUILD_RUN" ]; then
        echo "❌ No build found for $COMMIT"
        exit 1
    fi

    while true; do
        STATUS=$(gh run view "$BUILD_RUN" --json status,conclusion --jq '.status')
        if [ "$STATUS" == "completed" ]; then
            CONCLUSION=$(gh run view "$BUILD_RUN" --json conclusion --jq '.conclusion')
            [ "$CONCLUSION" == "success" ] && echo "✅ Build complete" || { echo "❌ Build failed"; exit 1; }
            break
        fi
        echo "  Waiting for build..."
        sleep 30
    done

    # Step 2: Create release tag
    echo ""
    echo "🏷️  [2/4] Creating release tag..."
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    TAG="release-{{env}}-$BRANCH-$(whoami)-$(date +%d-%m-%y--%H%M%S)"

    git tag -d $(git tag -l) 2>/dev/null || true
    git tag "$TAG"
    git push origin "$TAG"
    echo "✅ Tag pushed: $TAG"

    # Step 3: Wait for release workflow
    echo ""
    echo "⏳ [3/4] Waiting for terraform..."
    sleep 10

    RELEASE_RUN=$(gh run list --workflow=release.yml --limit=1 --json databaseId --jq '.[0].databaseId')

    while true; do
        STATUS=$(gh run view "$RELEASE_RUN" --json status,conclusion --jq '.status')
        if [ "$STATUS" == "completed" ]; then
            CONCLUSION=$(gh run view "$RELEASE_RUN" --json conclusion --jq '.conclusion')
            [ "$CONCLUSION" == "success" ] && echo "✅ Terraform complete" || { echo "❌ Terraform failed"; exit 1; }
            break
        fi
        echo "  Terraform running..."
        sleep 30
    done

    # Step 4: Force ECS deployment
    echo ""
    echo "💪 [4/4] Forcing ECS deployment..."
    CLUSTER="i-dot-ai-{{env}}-ecs-cluster"

    for service in backend frontend; do
        aws ecs update-service \
            --cluster "$CLUSTER" \
            --service "i-dot-ai-{{env}}-{{APP_NAME}}-$service-ecs-service" \
            --force-new-deployment \
            --no-cli-pager >/dev/null 2>&1 && echo "  ✅ $service restarted" || echo "  ⚠️  $service not found"
    done

    echo ""
    echo "✅ Deployment complete!"

# Just force ECS restart (for stuck deployments)
# Usage: just force-deploy [env=dev]
force-deploy env="dev": check-aws-auth
    #!/usr/bin/env bash
    set -euo pipefail

    echo "💪 Forcing ECS restart for {{env}}..."
    CLUSTER="i-dot-ai-{{env}}-ecs-cluster"
    for service in backend frontend; do
        aws ecs update-service \
            --cluster "$CLUSTER" \
            --service "i-dot-ai-{{env}}-{{APP_NAME}}-$service-ecs-service" \
            --force-new-deployment \
            --no-cli-pager >/dev/null 2>&1 && echo "✅ $service" || echo "❌ $service"
    done

# Check status of ECS services
# Usage: just status [env=dev]
status env="dev": check-aws-auth
    #!/usr/bin/env bash
    set -euo pipefail

    ENV={{env}}
    CLUSTER_NAME="i-dot-ai-${ENV}-ecs-cluster"
    BACKEND_SERVICE="i-dot-ai-${ENV}-{{APP_NAME}}-backend-ecs-service"
    FRONTEND_SERVICE="i-dot-ai-${ENV}-{{APP_NAME}}-frontend-ecs-service"

    echo "📊 ECS Service Status for environment: $ENV"
    echo ""

    # Helper function to display service status
    show_service_status() {
        local SERVICE_NAME=$1
        local DISPLAY_NAME=$2

        echo "$DISPLAY_NAME"

        # Get service details
        local SERVICE_DATA=$(aws ecs describe-services \
            --cluster "$CLUSTER_NAME" \
            --services "$SERVICE_NAME" \
            --no-cli-pager \
            --output json 2>/dev/null)

        if [ -z "$SERVICE_DATA" ] || [ "$(echo "$SERVICE_DATA" | jq -r '.services | length')" -eq 0 ]; then
            echo "  ❌ Service not found or error occurred"
            echo ""
            return
        fi

        # Extract key information
        local STATUS=$(echo "$SERVICE_DATA" | jq -r '.services[0].status // "UNKNOWN"')
        local DESIRED=$(echo "$SERVICE_DATA" | jq -r '.services[0].desiredCount // 0')
        local RUNNING=$(echo "$SERVICE_DATA" | jq -r '.services[0].runningCount // 0')
        local PENDING=$(echo "$SERVICE_DATA" | jq -r '.services[0].pendingCount // 0')
        local TASK_DEF_ARN=$(echo "$SERVICE_DATA" | jq -r '.services[0].taskDefinition // "N/A"')
        local TASK_DEF=$(echo "$TASK_DEF_ARN" | sed 's|.*/||')

        # Get container image from task definition
        local IMAGE="N/A"
        local IMAGE_TAG=""
        local COMMIT_INFO=""
        if [ "$TASK_DEF_ARN" != "N/A" ] && [ -n "$TASK_DEF_ARN" ]; then
            local TASK_DEF_DATA=$(aws ecs describe-task-definition \
                --task-definition "$TASK_DEF_ARN" \
                --no-cli-pager \
                --output json 2>/dev/null)
            if [ -n "$TASK_DEF_DATA" ]; then
                IMAGE=$(echo "$TASK_DEF_DATA" | jq -r '.taskDefinition.containerDefinitions[0].image // "N/A"' 2>/dev/null)
                # Extract tag from image (part after last colon)
                if [ "$IMAGE" != "N/A" ] && [[ "$IMAGE" == *":"* ]]; then
                    IMAGE_TAG=$(echo "$IMAGE" | sed 's/.*://')
                    # Check if tag looks like a commit hash (7+ hex characters)
                    if [[ "$IMAGE_TAG" =~ ^[0-9a-f]{7,}$ ]]; then
                        # Try to get commit info
                        if git cat-file -e "$IMAGE_TAG" 2>/dev/null; then
                            local COMMIT_MSG=$(git log -1 --pretty=format:"%s" "$IMAGE_TAG" 2>/dev/null)
                            local COMMIT_AUTHOR=$(git log -1 --pretty=format:"%an" "$IMAGE_TAG" 2>/dev/null)
                            local COMMIT_DATE=$(git log -1 --pretty=format:"%ar" "$IMAGE_TAG" 2>/dev/null)
                            if [ -n "$COMMIT_MSG" ]; then
                                # Truncate commit message if too long
                                local SHORT_MSG=$(echo "$COMMIT_MSG" | head -c 50)
                                if [ ${#COMMIT_MSG} -gt 50 ]; then
                                    SHORT_MSG="${SHORT_MSG}..."
                                fi
                                COMMIT_INFO=" (commit: ${IMAGE_TAG:0:7}, $COMMIT_DATE by $COMMIT_AUTHOR, \"$SHORT_MSG\")"
                            else
                                COMMIT_INFO=" (commit: ${IMAGE_TAG:0:7})"
                            fi
                        else
                            # Tag looks like commit hash but not in current repo
                            COMMIT_INFO=" (tag: ${IMAGE_TAG:0:7})"
                        fi
                    fi
                fi
            fi
        fi

        # Get primary deployment info
        local PRIMARY_DEPLOYMENT=$(echo "$SERVICE_DATA" | jq -r '.services[0].deployments[] | select(.status == "PRIMARY")')
        local DEPLOYMENT_CREATED_RAW=$(echo "$PRIMARY_DEPLOYMENT" | jq -r '.createdAt // "N/A"')
        local DEPLOYMENT_ID=$(echo "$PRIMARY_DEPLOYMENT" | jq -r '.id // "N/A"')

        # Format deployment timestamp
        local DEPLOYMENT_CREATED="N/A"
        if [ "$DEPLOYMENT_CREATED_RAW" != "N/A" ] && [ -n "$DEPLOYMENT_CREATED_RAW" ]; then
            # Format: YYYY-MM-DD HH:MM:SS UTC
            local DEPLOYMENT_DATE=$(echo "$DEPLOYMENT_CREATED_RAW" | cut -d'T' -f1)
            local DEPLOYMENT_TIME=$(echo "$DEPLOYMENT_CREATED_RAW" | cut -d'T' -f2 | cut -d'.' -f1 | cut -d'Z' -f1)
            DEPLOYMENT_CREATED="${DEPLOYMENT_DATE} ${DEPLOYMENT_TIME} UTC"
        fi

        # Status indicator
        if [ "$STATUS" = "ACTIVE" ] && [ "$RUNNING" -eq "$DESIRED" ] && [ "$PENDING" -eq 0 ]; then
            echo "  Status: ✅ $STATUS"
        elif [ "$STATUS" = "ACTIVE" ]; then
            echo "  Status: ⚠️  $STATUS (scaling/deploying)"
        else
            echo "  Status: ❌ $STATUS"
        fi

        echo "  Tasks:  Running: $RUNNING / Desired: $DESIRED / Pending: $PENDING"
        echo "  Task Definition: $TASK_DEF"
        if [ -n "$COMMIT_INFO" ]; then
            echo "  Image: $IMAGE$COMMIT_INFO"
        else
            echo "  Image: $IMAGE"
        fi
        echo "  Deployment: $DEPLOYMENT_CREATED"

        # Show recent events (last 3)
        echo ""
        echo "  Recent Events:"
        local EVENTS=$(echo "$SERVICE_DATA" | jq -r '.services[0].events[0:3][] | "\(.createdAt) - \(.message)"' 2>/dev/null)
        if [ -n "$EVENTS" ] && [ "$EVENTS" != "null" ]; then
            echo "$EVENTS" | while IFS= read -r event; do
                if [ -n "$event" ] && [ "$event" != "null" ]; then
                    # Extract time and message, format time to just show HH:MM:SS
                    local EVENT_TIME=$(echo "$event" | cut -d' ' -f1 | cut -d'T' -f2 | cut -d'.' -f1 2>/dev/null || echo "")
                    local EVENT_MSG=$(echo "$event" | sed 's/^[^ ]* [^ ]* - //' 2>/dev/null || echo "$event")
                    if [ -n "$EVENT_TIME" ]; then
                        echo "    • $EVENT_TIME - $EVENT_MSG"
                    else
                        echo "    • $EVENT_MSG"
                    fi
                fi
            done
        else
            echo "    (no recent events)"
        fi

        echo ""
    }

    show_service_status "$BACKEND_SERVICE" "Backend Service"
    show_service_status "$FRONTEND_SERVICE" "Frontend Service"
