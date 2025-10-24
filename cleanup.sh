#!/bin/bash

# MCP Kubernetes Demo Cleanup Script

echo "🧹 MCP Kubernetes Demo Cleanup Script"
echo "====================================="

echo "🗑️ Removing Kubernetes resources..."
kubectl delete -k k8s/

echo "✅ Cleanup complete!"
echo ""
echo "📋 To completely remove the namespace:"
echo "kubectl delete namespace mcp-demo"
