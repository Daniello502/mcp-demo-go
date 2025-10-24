#!/bin/bash

# MCP Kubernetes Demo Test Script

set -e

echo "🧪 MCP Kubernetes Demo Test Script"
echo "=================================="

# Configuration
NAMESPACE="mcp-demo"
SERVICE_NAME="mcp-server"
LOCAL_PORT="8080"

echo "📋 Testing deployment..."

# Check if namespace exists
if ! kubectl get namespace $NAMESPACE &> /dev/null; then
    echo "❌ Namespace $NAMESPACE does not exist. Please deploy first."
    exit 1
fi

# Check if deployment is ready
echo "⏳ Checking deployment status..."
if ! kubectl wait --for=condition=available --timeout=60s deployment/$SERVICE_NAME -n $NAMESPACE; then
    echo "❌ Deployment is not ready. Check logs:"
    kubectl logs deployment/$SERVICE_NAME -n $NAMESPACE
    exit 1
fi

echo "✅ Deployment is ready"

# Start port-forward in background
echo "🔗 Starting port-forward..."
kubectl port-forward svc/$SERVICE_NAME $LOCAL_PORT:8080 -n $NAMESPACE &
PORT_FORWARD_PID=$!

# Wait for port-forward to be ready
sleep 5

# Test health endpoint
echo "🏥 Testing health endpoint..."
if curl -s http://localhost:$LOCAL_PORT/health | grep -q "healthy"; then
    echo "✅ Health check passed"
else
    echo "❌ Health check failed"
    kill $PORT_FORWARD_PID 2>/dev/null || true
    exit 1
fi

# Test tools endpoint
echo "🔧 Testing tools endpoint..."
if curl -s http://localhost:$LOCAL_PORT/tools | grep -q "list_pods"; then
    echo "✅ Tools endpoint working"
else
    echo "❌ Tools endpoint failed"
    kill $PORT_FORWARD_PID 2>/dev/null || true
    exit 1
fi

# Test chat endpoint
echo "💬 Testing chat endpoint..."
CHAT_RESPONSE=$(curl -s -X POST http://localhost:$LOCAL_PORT/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, can you list pods in default namespace?"}')

if echo "$CHAT_RESPONSE" | grep -q "response"; then
    echo "✅ Chat endpoint working"
    echo "📝 Sample response:"
    echo "$CHAT_RESPONSE" | jq '.response' 2>/dev/null || echo "$CHAT_RESPONSE"
else
    echo "❌ Chat endpoint failed"
    echo "Response: $CHAT_RESPONSE"
fi

# Test specific tool
echo "🔍 Testing list_pods tool..."
TOOL_RESPONSE=$(curl -s -X POST http://localhost:$LOCAL_PORT/tools/list_pods \
  -H "Content-Type: application/json" \
  -d '{"namespace": "default"}')

if echo "$TOOL_RESPONSE" | grep -q "pods"; then
    echo "✅ list_pods tool working"
else
    echo "❌ list_pods tool failed"
    echo "Response: $TOOL_RESPONSE"
fi

# Cleanup
echo "🧹 Cleaning up..."
kill $PORT_FORWARD_PID 2>/dev/null || true

echo "✅ All tests completed!"
echo ""
echo "🌐 To access the web UI:"
echo "kubectl port-forward svc/$SERVICE_NAME 8080:8080 -n $NAMESPACE"
echo "Then open: http://localhost:8080"
