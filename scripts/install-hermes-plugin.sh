#!/bin/bash
set -e

# Install terminus-mind as a Hermes memory-provider plugin

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_VENV="${HERMES_HOME}/hermes-agent/venv/bin/python3"
PLUGIN_DEST="${HERMES_HOME}/plugins/memory/terminus-mind"

echo "Installing terminus-mind plugin for Hermes..."
echo "  Project: $PROJECT_ROOT"
echo "  Destination: $PLUGIN_DEST"
echo ""

# Verify Hermes venv exists
if [[ ! -f "$HERMES_VENV" ]]; then
    echo "✗ Hermes venv not found at: $HERMES_VENV"
    echo ""
    echo "Make sure Hermes is installed: https://github.com/anthropics/hermes"
    exit 1
fi

echo "1. Installing runtime dependencies in Hermes venv..."
"$HERMES_VENV" -m pip install -q "httpx>=0.27" "numpy>=2.0" || {
    echo "✗ Failed to install dependencies"
    exit 1
}
echo "   ✓ httpx, numpy installed"

echo ""
echo "2. Creating plugin directory..."
mkdir -p "$(dirname "$PLUGIN_DEST")"

echo ""
echo "3. Symlinking terminus-mind plugin..."
if [[ -L "$PLUGIN_DEST" ]]; then
    rm "$PLUGIN_DEST"
    echo "   (removed stale symlink)"
fi

if [[ -d "$PLUGIN_DEST" ]] && [[ ! -L "$PLUGIN_DEST" ]]; then
    echo "✗ Plugin directory exists as a non-symlink: $PLUGIN_DEST"
    echo "   Remove it manually and re-run this script."
    exit 1
fi

ln -sf "$PROJECT_ROOT/plugins/hermes/terminus_mind_provider" "$PLUGIN_DEST"
echo "   ✓ Symlink created"

echo ""
echo "4. Configuring Hermes..."
CONFIG_FILE="${HERMES_HOME}/profiles/herbie/config.yaml"
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "   ⚠ Hermes profile config not found: $CONFIG_FILE"
    echo "   Create a Hermes profile first, then add to config.yaml:"
    echo ""
    echo "     memory:"
    echo "       provider: terminus-mind"
    echo ""
else
    # Check if already configured
    if grep -q "provider: terminus-mind" "$CONFIG_FILE"; then
        echo "   ✓ Provider already configured in config.yaml"
    else
        # Try to add it (simple heuristic: after 'memory:' line)
        if grep -q "^memory:" "$CONFIG_FILE"; then
            echo "   ⚠ memory section exists but provider not set"
            echo "   Add to config.yaml under 'memory:'"
            echo ""
            echo "     provider: terminus-mind"
            echo ""
        else
            echo "   ⚠ No memory section in config.yaml"
            echo "   Add to config.yaml:"
            echo ""
            echo "     memory:"
            echo "       provider: terminus-mind"
            echo ""
        fi
    fi
fi

echo ""
echo "✓ Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Ensure memory.provider: terminus-mind is set in config.yaml"
echo "  2. Restart Hermes: pkill hermes; sleep 2"
echo "  3. Test with: tm curate"
echo ""
