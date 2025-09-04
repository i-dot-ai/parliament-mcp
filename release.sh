#!/bin/bash
# Usage:
## Just pass the name of the env we want to tag and deploy.
## This will create a tag locally with a format of $$ENV-$BRANCH-$CURRENT_USER-$TIMESTAMP
## Then push it to the remote git.
## Optional: pass --wait as second argument to wait for build to complete
ENV=$1
WAIT_FLAG=$2
BRANCH=$(git rev-parse --abbrev-ref HEAD)
CURRENT_USER=$(whoami)
TIMESTAMP=$(date +%d-%m-%y--%H%M%S)
TAG_NAME="release-$ENV-$BRANCH-$CURRENT_USER-$TIMESTAMP"

echo "Current branch name is" "$BRANCH"
echo "Current environment name is" "$ENV"
echo "Timestamp assigned will be $TIMESTAMP"
echo "New tag name will be " "$TAG_NAME"

# Only check build status if --wait flag is provided
if [ "$WAIT_FLAG" == "--wait" ]; then
    if command -v gh &> /dev/null; then
        COMMIT_SHA=$(git rev-parse HEAD)
        echo "Checking build status for commit $COMMIT_SHA..."

        # Check if build exists and get its status
        BUILD_RUNS=$(gh run list --workflow=build.yml --limit=20 --json status,conclusion,headSha,databaseId 2>/dev/null)

        if [ -z "$BUILD_RUNS" ]; then
            echo "Error: Unable to fetch build status from GitHub"
            exit 1
        fi

        # Find the specific build for our commit (get first matching build)
        BUILD_INFO=$(echo "$BUILD_RUNS" | jq -r "[.[] | select(.headSha == \"$COMMIT_SHA\")][0]")

        if [ -z "$BUILD_INFO" ] || [ "$BUILD_INFO" == "null" ]; then
            echo "Warning: No build found for current commit."
            echo "Please ensure your changes are pushed first."
            exit 1
        fi

        BUILD_STATUS=$(echo "$BUILD_INFO" | jq -r '.status')
        BUILD_CONCLUSION=$(echo "$BUILD_INFO" | jq -r '.conclusion')
        RUN_ID=$(echo "$BUILD_INFO" | jq -r '.databaseId')

        if [ "$BUILD_STATUS" == "in_progress" ] || [ "$BUILD_STATUS" == "queued" ]; then
            echo "Build is currently in progress. Waiting for completion..."

            # Wait for build with timeout of 20 minutes
            TIMEOUT=1200
            ELAPSED=0
            INTERVAL=30

            while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
                BUILD_STATUS=$(gh run view "$RUN_ID" --json status --jq '.status' 2>/dev/null)
                BUILD_CONCLUSION=$(gh run view "$RUN_ID" --json conclusion --jq '.conclusion' 2>/dev/null)

                if [ "$BUILD_STATUS" == "completed" ]; then
                    break
                fi

                echo "  Still building... (${ELAPSED}s elapsed)"
                sleep $INTERVAL
                ELAPSED=$((ELAPSED + INTERVAL))
            done

            if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
                echo "Timeout waiting for build to complete"
                exit 1
            fi
        fi

        if [ "$BUILD_CONCLUSION" != "success" ] && [ "$BUILD_CONCLUSION" != "null" ]; then
            echo "Build failed with conclusion: $BUILD_CONCLUSION"
            echo "Please fix build issues before releasing."
            exit 1
        fi

        echo "Build completed successfully!"
    else
        echo "GitHub CLI (gh) not found. Cannot check build status."
        echo "Proceeding without build check..."
    fi
fi

if [ "$ENV" == 'prod' ]; then
    echo -e "\033[0;31mWarning: You are about to deploy to production!\033[0m"
    echo -n "Are you sure you want to continue? (y/N) "
    read -r confirmation
    case "$confirmation" in
        [yY]) ;;
        *) echo "Deployment cancelled"; exit 0 ;;
    esac
fi

##
echo "Removing Local tags"
git tag -d "$(git tag -l)"

# Command to run
echo "Applying local tag" && \
git tag "$TAG_NAME" && \
echo "Pushing tag" && \
git push origin "$TAG_NAME"
