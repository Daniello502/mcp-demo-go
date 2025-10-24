#!/bin/bash

# MCP Kubernetes Demo Deployment Script

set -e

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    echo "📄 Loading environment variables from .env file..."
    export $(grep -v '^#' .env | xargs)
fi

# Configuration with environment variable fallbacks
DOCKER_USERNAME=${DOCKER_USERNAME:-"yourusername"}
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-""}
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

# Check if API key is provided
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "❌ ANTHROPIC_API_KEY not found in environment variables"
    echo "Please set it in your .env file or export it:"
    echo "export ANTHROPIC_API_KEY=your-api-key-here"
    exit 1
fi

# Create secret with API key
echo "🔐 Creating Kubernetes secret with API key..."
kubectl create secret generic mcp-server-secret \
  --from-literal=anthropic-api-key="$ANTHROPIC_API_KEY" \
  --namespace=mcp-demo \
  --dry-run=client -o yaml | kubectl apply -f -

# Create ConfigMap with service endpoints
echo "⚙️ Creating ConfigMap with service endpoints..."
kubectl create configmap mcp-server-config \
  --from-literal=kiali-endpoint="${KIALI_ENDPOINT:-http://kiali.istio-system:20001}" \
  --from-literal=jaeger-endpoint="${JAEGER_ENDPOINT:-http://jaeger-query.istio-system:16686}" \
  --from-literal=prometheus-endpoint="${PROMETHEUS_ENDPOINT:-http://prometheus.istio-system:9090}" \
  --from-literal=grafana-endpoint="${GRAFANA_ENDPOINT:-http://grafana.istio-system:3000}" \
  --namespace=mcp-demo \
  --dry-run=client -o yaml | kubectl apply -f -

# Update deployment with correct image
echo "📝 Updating deployment with image: ${FULL_IMAGE_NAME}"
sed -i.bak "s|docker.io/yourusername/mcp-k8s-server:v1|${FULL_IMAGE_NAME}|g" k8s/deployment.yaml

# Deploy to Kubernetes (excluding secret.yaml and configmap.yaml since we created them dynamically)
echo "🚀 Deploying to Kubernetes..."
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/serviceaccount.yaml
kubectl apply -f k8s/clusterrole.yaml
kubectl apply -f k8s/clusterrolebinding.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml

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
echo "1. Access the application at the URL above"
echo "2. Try asking: 'Show me all pods in the default namespace'"
echo ""
echo "🔍 To check logs: kubectl logs -f deployment/mcp-server -n mcp-demo"
echo "🔍 To check health: curl http://localhost:8080/health"
echo ""
echo "📝 Environment variables used:"
echo "   DOCKER_USERNAME: ${DOCKER_USERNAME}"
echo "   ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:0:10}... (truncated for security)"
