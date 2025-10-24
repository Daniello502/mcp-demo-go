#!/bin/bash

# MCP Kubernetes Demo Deployment Script

set -e

# Configuration
DOCKER_USERNAME=${DOCKER_USERNAME:-"yourusername"}
IMAGE_NAME="mcp-k8s-server"
IMAGE_TAG="v1"
FULL_IMAGE_NAME="docker.io/${DOCKER_USERNAME}/${IMAGE_NAME}:${IMAGE_TAG}"

echo "🚀 MCP Kubernetes Demo Deployment Script"
echo "========================================"

# Check prerequisites
echo "📋 Checking prerequisites..."

if ! command -v podman &> /dev/null; then
    echo "❌ Podman is not installed. Please install Podman first."
    exit 1
fi

if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl is not installed. Please install kubectl first."
    exit 1
fi

if ! kubectl cluster-info &> /dev/null; then
    echo "❌ Kubernetes cluster is not accessible. Please ensure Minikube is running."
    exit 1
fi

echo "✅ Prerequisites check passed"

# Login to Docker Hub
echo "🔐 Logging into Docker Hub..."
echo "Please enter your Docker Hub credentials:"
podman login docker.io

# Build Docker image
echo "🔨 Building Docker image..."
podman build -t "${FULL_IMAGE_NAME}" .

echo "📤 Pushing image to Docker Hub..."
podman push "${FULL_IMAGE_NAME}"

# Update deployment with correct image
echo "📝 Updating deployment with image: ${FULL_IMAGE_NAME}"
sed -i.bak "s|docker.io/yourusername/mcp-k8s-server:v1|${FULL_IMAGE_NAME}|g" k8s/deployment.yaml

# Deploy to Kubernetes
echo "🚀 Deploying to Kubernetes..."
kubectl apply -k k8s/

# Wait for deployment
echo "⏳ Waiting for deployment to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment/mcp-server -n mcp-demo

# Get service URL
echo "🌐 Getting service URL..."
if kubectl get svc mcp-server -n mcp-demo -o jsonpath='{.spec.type}' | grep -q LoadBalancer; then
    echo "🔗 LoadBalancer service created. Getting URL..."
    minikube service mcp-server -n mcp-demo --url
else
    echo "🔗 Using port-forward..."
    echo "Run: kubectl port-forward svc/mcp-server 8080:8080 -n mcp-demo"
    echo "Then open: http://localhost:8080"
fi

echo "✅ Deployment complete!"
echo ""
echo "📋 Next steps:"
echo "1. Make sure you've set your Anthropic API key in k8s/secret.yaml"
echo "2. Access the application at the URL above"
echo "3. Try asking: 'Show me all pods in the default namespace'"
echo ""
echo "🔍 To check logs: kubectl logs -f deployment/mcp-server -n mcp-demo"
echo "🔍 To check health: curl http://localhost:8080/health"
