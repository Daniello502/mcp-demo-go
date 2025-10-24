# MCP Kubernetes Demo

A Model Context Protocol (MCP) server that enables Claude AI to interact with a Kubernetes cluster and Istio service mesh through natural language queries. This demonstrates MCP as a bridge between LLMs and live infrastructure.

## Architecture

```
User → Web Browser → MCP Server (Pod) → Kubernetes API
                                      → Istio CRDs
                                      → Claude API (for LLM responses)
```

## Features

### MCP Tools Available

**Basic Kubernetes Operations:**
- `list_pods` - List pods in a namespace with optional label selector
- `get_pod_logs` - Retrieve logs from a specific pod/container
- `get_events` - Get cluster events (filtered by namespace)

**Istio Service Mesh Operations:**
- `list_virtualservices` - List all VirtualServices with routing rules
- `get_virtualservice` - Get specific VirtualService details
- `list_destinationrules` - List all DestinationRules with traffic policies
- `list_gateways` - List all Istio Gateways
- `list_service_entries` - List ServiceEntries for external services

**Kiali Operations (Service Mesh Visualization):**
- `get_kiali_graph` - Get service graph data from Kiali API
- `get_kiali_service_health` - Get health scores for services
- `get_kiali_traffic_metrics` - Get request rates, error rates, latency percentiles

**Jaeger Operations (Distributed Tracing):**
- `query_jaeger_traces` - Query traces by service name, operation, time range
- `get_jaeger_trace_detail` - Get detailed span information for a trace
- `analyze_jaeger_latency` - Find slow traces and bottlenecks

**Prometheus Operations (Metrics & Monitoring):**
- `query_prometheus` - Execute PromQL queries
- `get_istio_metrics` - Get Istio-specific metrics (request_total, request_duration)
- `get_service_metrics` - Get metrics for a specific service
- `analyze_prometheus_alerts` - Check active Prometheus alerts

## Prerequisites

- Minikube with Istio installed
- Podman (for building and pushing images)
- Docker Hub account with Personal Access Token (PAT)
- Anthropic API key

## Quick Start

### 1. Build and Push Docker Image

```bash
# Login to Docker Hub with your PAT
podman login docker.io
# Username: your-dockerhub-username
# Password: your-personal-access-token

# Build the image
podman build -t docker.io/yourusername/mcp-k8s-server:v1 .

# Push to Docker Hub
podman push docker.io/yourusername/mcp-k8s-server:v1
```

**Important:** 
- Replace `yourusername` with your actual Docker Hub username
- Use your Docker Hub Personal Access Token (PAT) as the password when logging in
- Create a PAT at: https://hub.docker.com/settings/security

### 2. Configure API Key

Edit `k8s/secret.yaml` and replace `<BASE64_ENCODED_API_KEY>` with your base64-encoded Anthropic API key:

```bash
# Encode your API key
echo -n "your-anthropic-api-key-here" | base64

# Update the secret.yaml file with the encoded value
```

### 3. Update Image Reference

Edit `k8s/deployment.yaml` and update the image reference:

```yaml
image: docker.io/yourusername/mcp-k8s-server:v1
```

### 4. Deploy to Minikube

```bash
# Apply all manifests
kubectl apply -k k8s/

# Verify deployment
kubectl get pods -n mcp-demo
kubectl logs -f deployment/mcp-server -n mcp-demo
```

### 5. Access the Application

```bash
# Get the service URL
minikube service mcp-server -n mcp-demo --url

# Or use port-forward
kubectl port-forward svc/mcp-server 8080:8080 -n mcp-demo
```

Then open your browser to `http://localhost:8080`

## Usage Examples

### Service Mesh Topology (Kiali)
- "Show me the service graph for the default namespace"
- "Which services are unhealthy?"
- "What's the traffic flow in my mesh?"

### Distributed Tracing (Jaeger)
- "Find slow traces in the last 30 minutes"
- "Show me traces for the productpage service"
- "What are the service dependencies?"
- "Analyze latency bottlenecks"

### Metrics & Monitoring (Prometheus)
- "What's the error rate for the reviews service?"
- "Show me P95 latency across all services"
- "Are there any active alerts?"
- "What's the request rate trend?"

### Istio Configuration
- "List all VirtualServices and explain their routing"
- "Show me DestinationRules and traffic policies"
- "What gateways are configured?"

### Combined Analysis
- "Why is the ratings service slow?" (checks Jaeger traces + Prometheus metrics)
- "Analyze the health of the productpage service" (Kiali + Prometheus)
- "Show me everything about the reviews service" (comprehensive analysis)

## Security

### RBAC Permissions

The MCP server runs with minimal read-only permissions:

- **Kubernetes**: pods, services, events, namespaces, nodes (read-only)
- **Istio CRDs**: virtualservices, destinationrules, gateways, serviceentries (read-only)
- **Logs**: pod logs (read-only)
- **No write/delete permissions**

### Secrets Management

- Anthropic API key stored as Kubernetes Secret
- Mounted as environment variable in the pod
- No sensitive data in container images

## Troubleshooting

### Check Pod Status
```bash
kubectl get pods -n mcp-demo
kubectl describe pod -l app=mcp-server -n mcp-demo
```

### View Logs
```bash
kubectl logs -f deployment/mcp-server -n mcp-demo
```

### Check Service Connectivity
```bash
kubectl get svc -n mcp-demo
kubectl port-forward svc/mcp-server 8080:8080 -n mcp-demo
```

### Verify RBAC
```bash
kubectl auth can-i list pods --as=system:serviceaccount:mcp-demo:mcp-server
kubectl auth can-i list virtualservices --as=system:serviceaccount:mcp-demo:mcp-server
```

### Health Check
```bash
curl http://localhost:8080/health
```

## API Endpoints

- `GET /` - Web UI
- `POST /chat` - Chat with Claude
- `GET /health` - Health check
- `GET /tools` - List available MCP tools
- `POST /tools/{tool_name}` - Execute specific tool

## Development

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export ANTHROPIC_API_KEY="your-api-key"
export KIALI_ENDPOINT="http://localhost:20001"
export JAEGER_ENDPOINT="http://localhost:16686"
export PROMETHEUS_ENDPOINT="http://localhost:9090"
export GRAFANA_ENDPOINT="http://localhost:3000"

# Run locally
python main.py
```

### Testing Tools

```bash
# Test specific tool
curl -X POST http://localhost:8080/tools/list_pods \
  -H "Content-Type: application/json" \
  -d '{"namespace": "default"}'

# Test chat endpoint
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Show me all pods in default namespace"}'
```

## Project Structure

```
mcp-demo-go/
├── main.py                 # MCP server implementation
├── requirements.txt        # Python dependencies
├── Dockerfile             # Container image
├── README.md              # This file
└── k8s/                   # Kubernetes manifests
    ├── namespace.yaml
    ├── serviceaccount.yaml
    ├── clusterrole.yaml
    ├── clusterrolebinding.yaml
    ├── secret.yaml
    ├── deployment.yaml
    ├── service.yaml
    └── kustomization.yaml
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

MIT License - see LICENSE file for details.

## Support

For issues and questions:
1. Check the troubleshooting section
2. Review the logs
3. Open an issue on GitHub

---

**Note:** This is a demo project showcasing MCP capabilities with Kubernetes and Istio. It's not intended for production use without additional security hardening and error handling.