#!/bin/sh
# Garage initialization script
# This script is idempotent - safe to run multiple times

set -e

GARAGE_HOST="${GARAGE_HOST:-garage}"
GARAGE_PORT="${GARAGE_PORT:-3900}"
GARAGE_ADMIN_PORT="${GARAGE_ADMIN_PORT:-3903}"
BUCKET_NAME="${BUCKET_NAME:-assets}"
KEY_NAME="${KEY_NAME:-assets-key}"
CREDENTIALS_FILE="${CREDENTIALS_FILE:-/var/lib/garage/credentials.env}"

echo "=== Garage Initialization ==="
echo "Host: $GARAGE_HOST:$GARAGE_PORT"
echo "Bucket: $BUCKET_NAME"
echo "Key: $KEY_NAME"

# Wait for Garage to be ready
echo "Waiting for Garage to be ready..."
for i in $(seq 1 30); do
    if garage -c /etc/garage.toml status >/dev/null 2>&1; then
        echo "Garage is ready!"
        break
    fi
    echo "  Attempt $i/30..."
    sleep 2
done

# Get node ID (strip the @address part if present)
FULL_NODE_ID=$(garage -c /etc/garage.toml node id -q 2>/dev/null | head -1)
NODE_ID=$(echo "$FULL_NODE_ID" | cut -d'@' -f1)
echo "Node ID: $NODE_ID"

if [ -z "$NODE_ID" ]; then
    echo "ERROR: Could not get node ID"
    exit 1
fi

# Check if layout is configured
LAYOUT_VERSION=$(garage -c /etc/garage.toml layout show 2>&1 | grep "Current cluster layout version" | awk '{print $NF}' || echo "0")
echo "Current layout version: $LAYOUT_VERSION"

if [ "$LAYOUT_VERSION" = "0" ] || echo "$LAYOUT_VERSION" | grep -q "No nodes"; then
    echo "Configuring layout..."

    # Assign node to layout
    echo "  Assigning node to layout..."
    garage -c /etc/garage.toml layout assign -z dc1 -c 1G "$NODE_ID"

    # Apply layout version 1
    echo "  Applying layout version 1..."
    garage -c /etc/garage.toml layout apply --version 1

    echo "Layout configured, waiting for cluster to stabilize..."
    sleep 5

    # Wait for cluster to be ready for operations
    echo "Waiting for cluster to accept operations..."
    for i in $(seq 1 30); do
        # Try a simple operation to see if cluster is ready
        if garage -c /etc/garage.toml bucket list >/dev/null 2>&1; then
            echo "Cluster is ready!"
            break
        fi
        echo "  Waiting for cluster... attempt $i/30"
        sleep 2
    done
else
    echo "Layout already configured (version $LAYOUT_VERSION)"
fi

# Create bucket with retry
echo "Checking bucket '$BUCKET_NAME'..."
BUCKET_CREATED=false
for i in $(seq 1 10); do
    if garage -c /etc/garage.toml bucket info "$BUCKET_NAME" >/dev/null 2>&1; then
        echo "Bucket '$BUCKET_NAME' already exists"
        BUCKET_CREATED=true
        break
    else
        echo "Creating bucket '$BUCKET_NAME' (attempt $i/10)..."
        if garage -c /etc/garage.toml bucket create "$BUCKET_NAME" 2>&1; then
            echo "Bucket created successfully"
            BUCKET_CREATED=true
            break
        fi
        sleep 2
    fi
done

if [ "$BUCKET_CREATED" = "false" ]; then
    echo "ERROR: Failed to create bucket after 10 attempts"
    exit 1
fi

# Enable website mode for public access
garage -c /etc/garage.toml bucket website --allow "$BUCKET_NAME" 2>/dev/null || true

# Create or get access key
echo "Checking key '$KEY_NAME'..."
KEY_INFO=$(garage -c /etc/garage.toml key info "$KEY_NAME" --show-secret 2>/dev/null || echo "")

if [ -z "$KEY_INFO" ]; then
    echo "Creating key '$KEY_NAME'..."
    KEY_INFO=$(garage -c /etc/garage.toml key create "$KEY_NAME")
fi

# Extract credentials
KEY_ID=$(echo "$KEY_INFO" | grep "Key ID:" | awk '{print $3}')
SECRET_KEY=$(echo "$KEY_INFO" | grep "Secret key:" | awk '{print $3}')

if [ -z "$KEY_ID" ] || [ -z "$SECRET_KEY" ]; then
    echo "ERROR: Could not extract credentials"
    echo "Key info output:"
    echo "$KEY_INFO"
    exit 1
fi

echo "Key ID: $KEY_ID"
echo "Secret Key: $(echo "$SECRET_KEY" | cut -c1-8)..."

# Grant permissions to bucket
echo "Granting permissions..."
garage -c /etc/garage.toml bucket allow --read --write "$BUCKET_NAME" --key "$KEY_NAME" 2>/dev/null || true

# Write credentials file
echo "Writing credentials to $CREDENTIALS_FILE..."
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat > "$CREDENTIALS_FILE" << EOF
# Garage S3 Credentials - auto-generated
# Generated: $TIMESTAMP
S3_ACCESS_KEY_ID="$KEY_ID"
S3_SECRET_ACCESS_KEY="$SECRET_KEY"
S3_BUCKET_NAME="$BUCKET_NAME"
AWS_ACCESS_KEY_ID="$KEY_ID"
AWS_SECRET_ACCESS_KEY="$SECRET_KEY"
AWS_STORAGE_BUCKET_NAME="$BUCKET_NAME"
EOF

chmod 644 "$CREDENTIALS_FILE"

echo ""
echo "=== Garage Initialization Complete ==="
echo ""
echo "Add these to your .env file:"
echo "  S3_ACCESS_KEY_ID=$KEY_ID"
echo "  S3_SECRET_ACCESS_KEY=$SECRET_KEY"
echo "  S3_BUCKET_NAME=$BUCKET_NAME"
echo ""
echo "Or source the credentials file:"
echo "  source $CREDENTIALS_FILE"
echo ""
