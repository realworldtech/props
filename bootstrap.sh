#!/bin/sh
# Bootstrap script for PROPS
# Starts Garage, waits for initialization, and populates .env with S3 credentials
set -e

ENV_FILE=".env"
COMPOSE_CMD="docker compose"

echo "=== PROPS Bootstrap ==="

# Check for .env file
if [ ! -f "$ENV_FILE" ]; then
    echo "No .env file found. Creating from .env.example..."
    cp .env.example "$ENV_FILE"
    echo "Created $ENV_FILE - you may want to edit it after bootstrap completes."
fi

# Start garage and wait for init to complete
echo ""
echo "Starting Garage S3 storage and running initialization..."
$COMPOSE_CMD up -d garage-init

echo ""
echo "Waiting for garage-init to complete..."
if ! $COMPOSE_CMD wait garage-init; then
    echo "ERROR: garage-init failed"
    echo "Check logs with: $COMPOSE_CMD logs garage-init"
    exit 1
fi

echo "Garage initialization complete!"

# Get credentials from garage
echo ""
echo "Fetching S3 credentials from Garage..."
KEY_INFO=$($COMPOSE_CMD exec garage /garage -c /etc/garage.toml key info assets-key --show-secret 2>/dev/null)

KEY_ID=$(echo "$KEY_INFO" | grep "Key ID:" | awk '{print $3}' | tr -d '\r')
SECRET_KEY=$(echo "$KEY_INFO" | grep "Secret key:" | awk '{print $3}' | tr -d '\r')
BUCKET_NAME="${S3_BUCKET_NAME:-assets}"

if [ -z "$KEY_ID" ] || [ -z "$SECRET_KEY" ]; then
    echo "ERROR: Could not extract credentials from Garage"
    echo "Key info output:"
    echo "$KEY_INFO"
    exit 1
fi

echo "Key ID:     $KEY_ID"
echo "Secret Key: $(echo "$SECRET_KEY" | cut -c1-8)..."
echo "Bucket:     $BUCKET_NAME"

# Update .env file with credentials
echo ""
echo "Updating $ENV_FILE with S3 credentials..."

update_env_var() {
    VAR_NAME="$1"
    VAR_VALUE="$2"

    if grep -q "^${VAR_NAME}=" "$ENV_FILE" 2>/dev/null; then
        # Replace existing value (works on both macOS and Linux)
        if [ "$(uname)" = "Darwin" ]; then
            sed -i '' "s|^${VAR_NAME}=.*|${VAR_NAME}=${VAR_VALUE}|" "$ENV_FILE"
        else
            sed -i "s|^${VAR_NAME}=.*|${VAR_NAME}=${VAR_VALUE}|" "$ENV_FILE"
        fi
        echo "  Updated $VAR_NAME"
    elif grep -q "^# *${VAR_NAME}=" "$ENV_FILE" 2>/dev/null; then
        # Uncomment and set value
        if [ "$(uname)" = "Darwin" ]; then
            sed -i '' "s|^# *${VAR_NAME}=.*|${VAR_NAME}=${VAR_VALUE}|" "$ENV_FILE"
        else
            sed -i "s|^# *${VAR_NAME}=.*|${VAR_NAME}=${VAR_VALUE}|" "$ENV_FILE"
        fi
        echo "  Uncommented and set $VAR_NAME"
    else
        # Append to file
        echo "${VAR_NAME}=${VAR_VALUE}" >> "$ENV_FILE"
        echo "  Added $VAR_NAME"
    fi
}

update_env_var "S3_ACCESS_KEY_ID" "$KEY_ID"
update_env_var "S3_SECRET_ACCESS_KEY" "$SECRET_KEY"
update_env_var "S3_BUCKET_NAME" "$BUCKET_NAME"

echo ""
echo "=== Bootstrap Complete ==="
echo ""
echo "Your .env file has been updated with S3 credentials."
echo "Start the full stack with:"
echo "  docker compose --profile dev up -d"
echo ""
