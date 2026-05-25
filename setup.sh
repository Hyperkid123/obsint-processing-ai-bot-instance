#!/bin/bash
set -e

echo "obsint-processing-ai-bot-instance" > /home/botuser/app/.instance-id

# Instance-specific packages go here:
# dnf install -y --nodocs <package>
# pip3.12 install <package>
# npm install -g <package>

# Install syft for SBOM generation (matches grype installation pattern from Dockerfile)
echo "Installing syft..."
ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
curl -fsSL "https://github.com/anchore/syft/releases/download/v1.21.0/syft_1.21.0_linux_${ARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin syft

echo "Instance setup complete: obsint-processing-ai-bot-instance"