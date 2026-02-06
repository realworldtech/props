#!/bin/sh
# Garage initialization script
# This runs after Garage starts to set up the bucket and access keys

set -e

GARAGE_ADMIN_TOKEN=$(cat /var/lib/garage/admin_token)
GARAGE_API="http://localhost:3903"

echo "Waiting for Garage to be ready..."
sleep 5

# Get node ID
NODE_ID=$(garage -c /etc/garage.toml node id -q 2>/dev/null | head -1)
echo "Node ID: $NODE_ID"

# Check if layout already exists
LAYOUT_STATUS=$(garage -c /etc/garage.toml layout show 2>&1 || true)

if echo "$LAYOUT_STATUS" | grep -q "No nodes"; then
    echo "Setting up layout..."
    garage -c /etc/garage.toml layout assign -z dc1 -c 1G "$NODE_ID"
    garage -c /etc/garage.toml layout apply --version 1
else
    echo "Layout already configured"
fi

# Create bucket if it doesn't exist
echo "Creating bucket..."
garage -c /etc/garage.toml bucket create assets 2>/dev/null || echo "Bucket may already exist"

# Allow public reads on the bucket for serving media files
garage -c /etc/garage.toml bucket allow --read --write assets --key assets-key 2>/dev/null || true
garage -c /etc/garage.toml bucket website --allow assets 2>/dev/null || true

# Create or get access key
echo "Setting up access key..."
KEY_INFO=$(garage -c /etc/garage.toml key create assets-key 2>/dev/null || garage -c /etc/garage.toml key info assets-key)

echo "$KEY_INFO"
echo ""
echo "Garage initialization complete!"
echo "S3 API: http://garage:3900"
echo "Admin API: http://garage:3903"
