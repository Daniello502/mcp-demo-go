#!/bin/bash

# Service Mesh Port-Forward Script
# Run this on your remote server to expose service mesh tools locally

echo "🔗 Starting Service Mesh Port-Forwards"
echo "====================================="
echo ""

# Function to start port-forward
start_port_forward() {
    local service=$1
    local namespace=$2
    local local_port=$3
    local remote_port=$4
    local name=$5
    
    echo "Starting $name port-forward..."
    kubectl port-forward svc/$service -n $namespace $local_port:$remote_port &
    echo "✅ $name available at: http://localhost:$local_port"
}

# Start all port-forwards
start_port_forward "kiali" "istio-system" "20001" "20001" "Kiali"
start_port_forward "tracing" "istio-system" "16685" "80" "Jaeger"
start_port_forward "prometheus" "istio-system" "9090" "9090" "Prometheus"
start_port_forward "grafana" "istio-system" "3000" "3000" "Grafana"

echo ""
echo "🎉 All service mesh tools are now accessible locally!"
echo ""
echo "📋 Access URLs:"
echo "   Kiali:      http://localhost:20001"
echo "   Jaeger:     http://localhost:16685"
echo "   Prometheus: http://localhost:9090"
echo "   Grafana:    http://localhost:3000"
echo ""
echo "🛑 To stop all port-forwards:"
echo "   pkill -f 'kubectl port-forward'"
echo ""
echo "📊 To check running port-forwards:"
echo "   jobs"
echo "   ps aux | grep 'kubectl port-forward'"
