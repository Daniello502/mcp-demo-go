# Example Queries for MCP Kubernetes Demo

This document provides example queries you can try with the MCP Kubernetes Demo to showcase different capabilities.

## Basic Kubernetes Operations

### Pod Management
- "Show me all pods in the default namespace"
- "What pods are failing in the default namespace?"
- "List all pods with label app=productpage"
- "Show me the logs for the productpage pod"
- "What events are happening in the default namespace?"

### Service Discovery
- "List all services in the default namespace"
- "Show me the endpoints for the productpage service"
- "What services are running in the istio-system namespace?"

## Istio Service Mesh Operations

### Virtual Services
- "List all VirtualServices and explain their routing rules"
- "Show me the VirtualService for the productpage service"
- "What routing rules are configured for the reviews service?"

### Destination Rules
- "List all DestinationRules and their traffic policies"
- "Show me the load balancing configuration for the reviews service"
- "What traffic policies are applied to the ratings service?"

### Gateways
- "List all Istio Gateways"
- "Show me the ingress configuration"
- "What external traffic is allowed into the mesh?"

## Service Mesh Observability (Kiali)

### Service Graph
- "Show me the service mesh topology from Kiali"
- "What's the traffic flow between services?"
- "Which services are communicating with each other?"
- "Show me the service graph for the default namespace"

### Health Analysis
- "Which services are unhealthy according to Kiali?"
- "What's the health status of the productpage service?"
- "Show me the error rates for all services"
- "Which services have the highest error rates?"

### Traffic Metrics
- "What's the request rate for the reviews service?"
- "Show me the latency distribution for all services"
- "Which service has the highest latency?"
- "What's the throughput for the productpage service?"

## Distributed Tracing (Jaeger)

### Trace Analysis
- "Find slow traces in the last hour from Jaeger"
- "Show me traces for the productpage service"
- "What are the slowest operations in the mesh?"
- "Find traces with errors in the last 30 minutes"

### Service Dependencies
- "What are the service dependencies from Jaeger?"
- "Show me the call graph for the productpage service"
- "Which services does the reviews service call?"
- "What's the dependency chain for the ratings service?"

### Latency Analysis
- "Analyze latency bottlenecks in the mesh"
- "What's causing the high latency in the reviews service?"
- "Show me the P95 latency for all services"
- "Which operations are taking the longest time?"

## Metrics & Monitoring (Prometheus)

### Request Metrics
- "What's the error rate for the reviews service?"
- "Show me P95 latency across all services"
- "What's the request rate trend for the productpage service?"
- "Which service has the highest error rate?"

### Istio Metrics
- "Show me the Istio request metrics"
- "What's the request duration histogram for all services?"
- "Show me the 4xx and 5xx error rates"
- "What's the throughput for the mesh?"

### Alerting
- "Are there any active Prometheus alerts?"
- "What alerts are firing for the service mesh?"
- "Show me the alert status for all services"

## Dashboard Data (Grafana)

### Istio Dashboards
- "Get data from the Istio Service Dashboard"
- "Show me the Istio Mesh Dashboard overview"
- "What does the Istio Workload Dashboard show?"
- "Get metrics from the Istio Control Plane Dashboard"

### Service Overview
- "Show me the complete service metrics overview"
- "What's the health status from Grafana?"
- "Get the traffic metrics from the Istio dashboards"
- "Show me the error rate trends from Grafana"

## Combined Analysis

### Service Health Analysis
- "Analyze the health of the productpage service" (Kiali + Prometheus)
- "Why is the ratings service slow?" (Jaeger traces + Prometheus metrics)
- "What's causing errors in the reviews service?" (Events + Logs + Metrics)

### Performance Analysis
- "Show me everything about the reviews service" (comprehensive analysis)
- "Analyze the performance of the entire mesh"
- "What's the bottleneck in the productpage service?"
- "Why are requests failing in the ratings service?"

### Troubleshooting
- "Debug the productpage service issues"
- "What's wrong with the mesh configuration?"
- "Why are services not communicating properly?"
- "Analyze the traffic flow and identify issues"

## Advanced Queries

### Configuration Analysis
- "Compare the VirtualService configurations"
- "What's the difference between the old and new DestinationRule?"
- "Show me the Gateway configuration changes"
- "Analyze the security policies in the mesh"

### Traffic Management
- "What traffic splitting rules are configured?"
- "Show me the canary deployment configuration"
- "What's the circuit breaker configuration?"
- "How is traffic being routed to different versions?"

### Security Analysis
- "What security policies are applied to the mesh?"
- "Show me the mTLS configuration"
- "What authentication rules are configured?"
- "Analyze the authorization policies"

## Tips for Effective Queries

1. **Be Specific**: Instead of "show me services", try "show me unhealthy services in the default namespace"

2. **Use Time Ranges**: "Find slow traces in the last hour" is better than "find slow traces"

3. **Combine Context**: "Why is the productpage service slow?" will trigger multiple tools (Jaeger + Prometheus + Kiali)

4. **Ask for Analysis**: "Analyze the health of the reviews service" will get comprehensive insights

5. **Use Service Names**: Reference actual service names from your cluster for better results

## Expected Tool Usage

When you ask these questions, Claude should automatically call the appropriate MCP tools:

- **Pod questions** → `list_pods`, `get_pod_logs`, `get_events`
- **Service mesh questions** → `list_virtualservices`, `list_destinationrules`, `list_gateways`
- **Kiali questions** → `get_kiali_graph`, `get_kiali_service_health`
- **Jaeger questions** → `query_jaeger_traces`
- **Prometheus questions** → `query_prometheus`, `get_istio_metrics`
- **Grafana questions** → `get_grafana_dashboard_data`

The beauty of this MCP implementation is that Claude can intelligently combine multiple tools to provide comprehensive answers to complex questions about your service mesh!
