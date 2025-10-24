#!/bin/bash

# MCP Kubernetes Demo Setup Script

set -e

echo "🔧 MCP Kubernetes Demo Setup Script"
echo "===================================="

# Check if .env already exists
if [ -f .env ]; then
    echo "⚠️  .env file already exists. Skipping creation."
    echo "Current .env contents:"
    cat .env
    echo ""
    echo "If you need to update it, edit .env manually."
else
    echo "📄 Creating .env file from template..."
    cp config.template .env
    echo "✅ Created .env file"
    echo ""
    echo "📝 Please edit .env with your actual values:"
    echo "   - DOCKER_USERNAME: Your Docker Hub username"
    echo "   - ANTHROPIC_API_KEY: Your Anthropic API key"
    echo ""
    echo "Example:"
    echo "   DOCKER_USERNAME=myusername"
    echo "   ANTHROPIC_API_KEY=sk-ant-api03-..."
    echo ""
    echo "After editing .env, run: ./deploy.sh"
fi

echo ""
echo "📋 Next steps:"
echo "1. Edit .env with your values"
echo "2. Run: ./deploy.sh"
echo "3. Access the application when deployment completes"
