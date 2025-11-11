#!/usr/bin/env python3
"""
MCP Kubernetes Demo Server

A Model Context Protocol server that enables Claude AI to interact with
Kubernetes clusters and Istio service mesh through natural language queries.
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from prometheus_api_client import PrometheusConnect

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="MCP Kubernetes Demo", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables for clients
k8s_client = None
prometheus_client = None
anthropic_client = None

# Configuration
KIALI_ENDPOINT = os.getenv("KIALI_ENDPOINT", "http://kiali.istio-system:20001")
JAEGER_ENDPOINT = os.getenv("JAEGER_ENDPOINT", "http://jaeger-query.istio-system:16686")
PROMETHEUS_ENDPOINT = os.getenv("PROMETHEUS_ENDPOINT", "http://prometheus.istio-system:9090")
GRAFANA_ENDPOINT = os.getenv("GRAFANA_ENDPOINT", "http://grafana.istio-system:3000")

# ------------------------
# Utility helpers (token-safe)
# ------------------------

def _truncate_string(value: str, max_len: int = 300) -> str:
    if not isinstance(value, str):
        return value
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."

def _prune_list(items: List[Any], max_items: int = 20) -> List[Any]:
    if not isinstance(items, list):
        return items
    if len(items) <= max_items:
        return items
    return items[:max_items]

def _compact_json(data: Any, max_items: int = 20, max_str: int = 300) -> Any:
    # Recursively prune big lists/strings and drop verbose fields
    if isinstance(data, dict):
        compacted = {}
        for k, v in data.items():
            if k in {"logs", "raw", "rawJSON", "raw_json", "fullText"}:
                continue
            compacted[k] = _compact_json(v, max_items=max_items, max_str=max_str)
        return compacted
    if isinstance(data, list):
        return [_compact_json(v, max_items=max_items, max_str=max_str) for v in _prune_list(data, max_items=max_items)]
    if isinstance(data, str):
        return _truncate_string(data, max_len=max_str)
    return data

def _summarize_tool_result(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    # Produce a compact summary per tool to save tokens
    if not isinstance(result, dict):
        return {"result": _compact_json(result)}

    try:
        if tool_name == "list_pods":
            pods = result.get("pods", [])
            summary = [{
                "name": p.get("name"),
                "status": p.get("status"),
                "ready": p.get("ready"),
                "restarts": p.get("restarts"),
            } for p in _prune_list(pods, 20)]
            return {"count": result.get("count", len(pods)), "pods": summary}

        if tool_name == "get_events":
            events = result.get("events", [])
            summary = [{
                "type": e.get("type"),
                "reason": e.get("reason"),
                "message": _truncate_string(e.get("message", ""), 160),
                "last_timestamp": e.get("last_timestamp"),
            } for e in _prune_list(events, 20)]
            return {"count": result.get("count", len(events)), "events": summary}

        if tool_name == "get_kiali_graph":
            g = result.get("graph_data", {})
            nodes = g.get("elements", {}).get("nodes", []) if isinstance(g.get("elements"), dict) else []
            edges = g.get("elements", {}).get("edges", []) if isinstance(g.get("elements"), dict) else []
            return {
                "nodes_count": len(nodes),
                "edges_count": len(edges),
                "sample_nodes": _prune_list(nodes, 10),
            }

        if tool_name == "query_jaeger_traces":
            traces = result.get("traces", {}).get("data", []) if isinstance(result.get("traces"), dict) else []
            simple = []
            for t in _prune_list(traces, 10):
                tid = t.get("traceID") or t.get("traceId") or t.get("id")
                duration = t.get("duration") or t.get("durationMs")
                simple.append({"traceID": tid, "duration": duration})
            return {"traces": simple, "count": len(traces)}

        if tool_name in {"get_istio_metrics", "query_prometheus"}:
            return _compact_json(result, max_items=10, max_str=120)

        if tool_name in {"list_virtualservices", "list_destinationrules", "list_gateways"}:
            key = "virtualservices" if tool_name == "list_virtualservices" else (
                "destinationrules" if tool_name == "list_destinationrules" else "gateways"
            )
            items = result.get(key, [])
            return {
                "count": len(items),
                key: _prune_list(items, 15)
            }
    except Exception:
        pass
    return _compact_json(result, max_items=15, max_str=160)

def _extract_query_params(message: str) -> Dict[str, Any]:
    # naive extraction of namespace/service/duration
    import re
    params: Dict[str, Any] = {}
    m = re.search(r"namespace\s+([a-z0-9-]+)", message, flags=re.I)
    if m:
        params["namespace"] = m.group(1)
    m = re.search(r"service\s+([a-z0-9-]+)", message, flags=re.I)
    if m:
        params["service"] = m.group(1)
    m = re.search(r"last\s+(\d+)(m|h|d)", message, flags=re.I)
    if m:
        params["duration"] = f"{m.group(1)}{m.group(2)}"
    return params

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str
    tools_used: List[str]

def initialize_clients():
    """Initialize Kubernetes and other clients"""
    global k8s_client, prometheus_client, anthropic_client
    
    try:
        # Initialize Kubernetes client (in-cluster config)
        config.load_incluster_config()
        k8s_client = client.ApiClient()
        logger.info("Kubernetes client initialized successfully")
    except Exception as e:
        logger.warning(f"Failed to load in-cluster config: {e}")
        try:
            config.load_kube_config()
            k8s_client = client.ApiClient()
            logger.info("Kubernetes client initialized with kubeconfig")
        except Exception as e2:
            logger.error(f"Failed to initialize Kubernetes client: {e2}")
            k8s_client = None
    
    # Initialize Prometheus client
    try:
        prometheus_client = PrometheusConnect(url=PROMETHEUS_ENDPOINT, disable_ssl=True)
        logger.info("Prometheus client initialized successfully")
    except Exception as e:
        logger.warning(f"Failed to initialize Prometheus client: {e}")
        prometheus_client = None
    
    # Initialize Anthropic client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        anthropic_client = anthropic.Anthropic(api_key=api_key)
        logger.info("Anthropic client initialized successfully")
    else:
        logger.warning("ANTHROPIC_API_KEY not found")

# MCP Tools Implementation

def list_pods(namespace: str = "default", label_selector: str = None) -> Dict[str, Any]:
    """List pods in a namespace with optional label selector"""
    try:
        v1 = client.CoreV1Api(k8s_client)
        pods = v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=label_selector
        )
        
        result = []
        for pod in pods.items:
            result.append({
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "status": pod.status.phase,
                "ready": f"{pod.status.container_statuses[0].ready if pod.status.container_statuses else False}",
                "restarts": pod.status.container_statuses[0].restart_count if pod.status.container_statuses else 0,
                "age": str(datetime.now() - pod.metadata.creation_timestamp.replace(tzinfo=None)) if pod.metadata.creation_timestamp else "unknown"
            })
        
        return {"pods": result, "count": len(result)}
    except ApiException as e:
        return {"error": f"Failed to list pods: {e}"}

def get_pod_logs(pod_name: str, namespace: str = "default", container: str = None) -> Dict[str, Any]:
    """Retrieve logs from a specific pod/container"""
    try:
        v1 = client.CoreV1Api(k8s_client)
        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            tail_lines=100
        )
        return {"logs": logs, "pod": pod_name, "namespace": namespace}
    except ApiException as e:
        return {"error": f"Failed to get logs: {e}"}

def get_events(namespace: str = "default") -> Dict[str, Any]:
    """Get cluster events filtered by namespace"""
    try:
        v1 = client.CoreV1Api(k8s_client)
        events = v1.list_namespaced_event(namespace=namespace)
        
        result = []
        for event in events.items:
            result.append({
                "name": event.metadata.name,
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "first_timestamp": str(event.first_timestamp) if event.first_timestamp else None,
                "last_timestamp": str(event.last_timestamp) if event.last_timestamp else None,
                "count": event.count,
                "involved_object": {
                    "kind": event.involved_object.kind,
                    "name": event.involved_object.name
                } if event.involved_object else None
            })
        
        return {"events": result, "count": len(result)}
    except ApiException as e:
        return {"error": f"Failed to get events: {e}"}

def list_virtualservices(namespace: str = None) -> Dict[str, Any]:
    """List all VirtualServices with routing rules"""
    try:
        custom_api = client.CustomObjectsApi(k8s_client)
        
        if namespace:
            vs_list = custom_api.list_namespaced_custom_object(
                group="networking.istio.io",
                version="v1beta1",
                namespace=namespace,
                plural="virtualservices"
            )
        else:
            vs_list = custom_api.list_cluster_custom_object(
                group="networking.istio.io",
                version="v1beta1",
                plural="virtualservices"
            )
        
        result = []
        for vs in vs_list.get("items", []):
            result.append({
                "name": vs["metadata"]["name"],
                "namespace": vs["metadata"]["namespace"],
                "hosts": vs["spec"].get("hosts", []),
                "http": vs["spec"].get("http", []),
                "tcp": vs["spec"].get("tcp", []),
                "tls": vs["spec"].get("tls", [])
            })
        
        return {"virtualservices": result, "count": len(result)}
    except ApiException as e:
        return {"error": f"Failed to list VirtualServices: {e}"}

def list_destinationrules(namespace: str = None) -> Dict[str, Any]:
    """List all DestinationRules with traffic policies"""
    try:
        custom_api = client.CustomObjectsApi(k8s_client)
        
        if namespace:
            dr_list = custom_api.list_namespaced_custom_object(
                group="networking.istio.io",
                version="v1beta1",
                namespace=namespace,
                plural="destinationrules"
            )
        else:
            dr_list = custom_api.list_cluster_custom_object(
                group="networking.istio.io",
                version="v1beta1",
                plural="destinationrules"
            )
        
        result = []
        for dr in dr_list.get("items", []):
            result.append({
                "name": dr["metadata"]["name"],
                "namespace": dr["metadata"]["namespace"],
                "host": dr["spec"].get("host", ""),
                "traffic_policy": dr["spec"].get("trafficPolicy", {}),
                "subsets": dr["spec"].get("subsets", [])
            })
        
        return {"destinationrules": result, "count": len(result)}
    except ApiException as e:
        return {"error": f"Failed to list DestinationRules: {e}"}

def list_gateways(namespace: str = None) -> Dict[str, Any]:
    """List all Istio Gateways"""
    try:
        custom_api = client.CustomObjectsApi(k8s_client)
        
        if namespace:
            gw_list = custom_api.list_namespaced_custom_object(
                group="networking.istio.io",
                version="v1beta1",
                namespace=namespace,
                plural="gateways"
            )
        else:
            gw_list = custom_api.list_cluster_custom_object(
                group="networking.istio.io",
                version="v1beta1",
                plural="gateways"
            )
        
        result = []
        for gw in gw_list.get("items", []):
            result.append({
                "name": gw["metadata"]["name"],
                "namespace": gw["metadata"]["namespace"],
                "selector": gw["spec"].get("selector", {}),
                "servers": gw["spec"].get("servers", [])
            })
        
        return {"gateways": result, "count": len(result)}
    except ApiException as e:
        return {"error": f"Failed to list Gateways: {e}"}

def get_kiali_graph(namespace: str = "default", graph_type: str = "workload", duration: str = "1h") -> Dict[str, Any]:
    """Get service graph data from Kiali API"""
    try:
        import requests
        
        url = f"{KIALI_ENDPOINT}/api/namespaces/{namespace}/graph"
        params = {
            "graphType": graph_type,
            "duration": duration,
            "includeHealth": "true"
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        return {"graph_data": response.json()}
    except Exception as e:
        return {"error": f"Failed to get Kiali graph: {e}"}

def get_kiali_service_health(namespace: str = "default", service: str = None) -> Dict[str, Any]:
    """Get health scores for services from Kiali"""
    try:
        import requests
        
        if service:
            url = f"{KIALI_ENDPOINT}/api/namespaces/{namespace}/services/{service}/health"
        else:
            url = f"{KIALI_ENDPOINT}/api/namespaces/{namespace}/health"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        return {"health_data": response.json()}
    except Exception as e:
        return {"error": f"Failed to get Kiali service health: {e}"}

def query_prometheus(query: str, start_time: str = None, end_time: str = None) -> Dict[str, Any]:
    """Execute PromQL queries against Prometheus"""
    try:
        if not prometheus_client:
            return {"error": "Prometheus client not initialized"}
        
        if start_time and end_time:
            result = prometheus_client.custom_query_range(
                query=query,
                start_time=start_time,
                end_time=end_time
            )
        else:
            result = prometheus_client.custom_query(query=query)
        
        return {"query": query, "result": result}
    except Exception as e:
        return {"error": f"Failed to query Prometheus: {e}"}

def get_istio_metrics(service: str = None, namespace: str = "default") -> Dict[str, Any]:
    """Get Istio-specific metrics"""
    try:
        if not prometheus_client:
            return {"error": "Prometheus client not initialized"}
        
        queries = {
            "request_total": f'sum(rate(istio_requests_total{{destination_service_name="{service}"}}[5m])) by (destination_service_name)' if service else 'sum(rate(istio_requests_total[5m])) by (destination_service_name)',
            "request_duration_p50": f'histogram_quantile(0.5, sum(rate(istio_request_duration_milliseconds_bucket{{destination_service_name="{service}"}}[5m])) by (le))' if service else 'histogram_quantile(0.5, sum(rate(istio_request_duration_milliseconds_bucket[5m])) by (le))',
            "request_duration_p95": f'histogram_quantile(0.95, sum(rate(istio_request_duration_milliseconds_bucket{{destination_service_name="{service}"}}[5m])) by (le))' if service else 'histogram_quantile(0.95, sum(rate(istio_request_duration_milliseconds_bucket[5m])) by (le))',
            "request_duration_p99": f'histogram_quantile(0.99, sum(rate(istio_request_duration_milliseconds_bucket{{destination_service_name="{service}"}}[5m])) by (le))' if service else 'histogram_quantile(0.99, sum(rate(istio_request_duration_milliseconds_bucket[5m])) by (le))',
            "error_rate": f'sum(rate(istio_requests_total{{destination_service_name="{service}",response_code!~"2.."}}[5m])) / sum(rate(istio_requests_total{{destination_service_name="{service}"}}[5m]))' if service else 'sum(rate(istio_requests_total{response_code!~"2.."}[5m])) / sum(rate(istio_requests_total[5m]))'
        }
        
        results = {}
        for metric_name, query in queries.items():
            try:
                result = prometheus_client.custom_query(query=query)
                results[metric_name] = result
            except Exception as e:
                results[metric_name] = {"error": str(e)}
        
        return {"metrics": results}
    except Exception as e:
        return {"error": f"Failed to get Istio metrics: {e}"}

def query_jaeger_traces(service: str = None, operation: str = None, start_time: str = None, end_time: str = None) -> Dict[str, Any]:
    """Query traces by service name, operation, time range from Jaeger"""
    try:
        import requests
        
        url = f"{JAEGER_ENDPOINT}/api/traces"
        params = {}
        
        if service:
            params["service"] = service
        if operation:
            params["operation"] = operation
        if start_time:
            params["start"] = start_time
        if end_time:
            params["end"] = end_time
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        return {"traces": response.json()}
    except Exception as e:
        return {"error": f"Failed to query Jaeger traces: {e}"}

# MCP Tools Registry
MCP_TOOLS = {
    "list_pods": {
        "description": "List pods in a namespace with optional label selector",
        "parameters": {
            "namespace": {"type": "string", "default": "default"},
            "label_selector": {"type": "string", "optional": True}
        }
    },
    "get_pod_logs": {
        "description": "Retrieve logs from a specific pod/container",
        "parameters": {
            "pod_name": {"type": "string", "required": True},
            "namespace": {"type": "string", "default": "default"},
            "container": {"type": "string", "optional": True}
        }
    },
    "get_events": {
        "description": "Get cluster events filtered by namespace",
        "parameters": {
            "namespace": {"type": "string", "default": "default"}
        }
    },
    "list_virtualservices": {
        "description": "List all VirtualServices with routing rules",
        "parameters": {
            "namespace": {"type": "string", "optional": True}
        }
    },
    "list_destinationrules": {
        "description": "List all DestinationRules with traffic policies",
        "parameters": {
            "namespace": {"type": "string", "optional": True}
        }
    },
    "list_gateways": {
        "description": "List all Istio Gateways",
        "parameters": {
            "namespace": {"type": "string", "optional": True}
        }
    },
    "get_kiali_graph": {
        "description": "Get service graph data from Kiali API",
        "parameters": {
            "namespace": {"type": "string", "default": "default"},
            "graph_type": {"type": "string", "default": "workload"},
            "duration": {"type": "string", "default": "1h"}
        }
    },
    "get_kiali_service_health": {
        "description": "Get health scores for services from Kiali",
        "parameters": {
            "namespace": {"type": "string", "default": "default"},
            "service": {"type": "string", "optional": True}
        }
    },
    "query_prometheus": {
        "description": "Execute PromQL queries against Prometheus",
        "parameters": {
            "query": {"type": "string", "required": True},
            "start_time": {"type": "string", "optional": True},
            "end_time": {"type": "string", "optional": True}
        }
    },
    "get_istio_metrics": {
        "description": "Get Istio-specific metrics (request rate, latency, error rate)",
        "parameters": {
            "service": {"type": "string", "optional": True},
            "namespace": {"type": "string", "default": "default"}
        }
    },
    "query_jaeger_traces": {
        "description": "Query traces by service name, operation, time range from Jaeger",
        "parameters": {
            "service": {"type": "string", "optional": True},
            "operation": {"type": "string", "optional": True},
            "start_time": {"type": "string", "optional": True},
            "end_time": {"type": "string", "optional": True}
        }
    }
}

def execute_mcp_tool(tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an MCP tool with given parameters"""
    if tool_name not in MCP_TOOLS:
        return {"error": f"Unknown tool: {tool_name}"}
    
    try:
        if tool_name == "list_pods":
            return list_pods(**parameters)
        elif tool_name == "get_pod_logs":
            return get_pod_logs(**parameters)
        elif tool_name == "get_events":
            return get_events(**parameters)
        elif tool_name == "list_virtualservices":
            return list_virtualservices(**parameters)
        elif tool_name == "list_destinationrules":
            return list_destinationrules(**parameters)
        elif tool_name == "list_gateways":
            return list_gateways(**parameters)
        elif tool_name == "get_kiali_graph":
            return get_kiali_graph(**parameters)
        elif tool_name == "get_kiali_service_health":
            return get_kiali_service_health(**parameters)
        elif tool_name == "query_prometheus":
            return query_prometheus(**parameters)
        elif tool_name == "get_istio_metrics":
            return get_istio_metrics(**parameters)
        elif tool_name == "query_jaeger_traces":
            return query_jaeger_traces(**parameters)
        else:
            return {"error": f"Tool {tool_name} not implemented"}
    except Exception as e:
        return {"error": f"Failed to execute {tool_name}: {e}"}

def convert_mcp_tools_to_anthropic_format() -> List[Dict[str, Any]]:
    """Convert MCP tools registry to Anthropic tool format."""
    anthropic_tools: List[Dict[str, Any]] = []
    for tool_name, tool_info in MCP_TOOLS.items():
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for param_name, param_info in tool_info["parameters"].items():
            prop: Dict[str, Any] = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", f"The {param_name} parameter")
            }
            if "default" in param_info:
                prop["default"] = param_info["default"]
            properties[param_name] = prop
            if param_info.get("required", False):
                required.append(param_name)
        anthropic_tools.append({
            "name": tool_name,
            "description": tool_info.get("description", tool_name),
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        })
    return anthropic_tools

def process_with_claude(user_message: str) -> Dict[str, Any]:
    """Process user message with Claude using Anthropic tool use to call MCP tools."""
    logger.info("=== Starting process_with_claude ===")
    logger.info(f"User message: {user_message}")

    if not anthropic_client:
        logger.warning("Anthropic client not initialized!")
        return {
            "response": "Anthropic API key not configured. Please set ANTHROPIC_API_KEY environment variable.",
            "tools_used": []
        }

    try:
        tools_spec = convert_mcp_tools_to_anthropic_format()
        # Filter tools based on intent to reduce available toolset (token + accuracy)
        intent = user_message.lower()
        narrowed_tools = []
        def _need(names: List[str]) -> bool:
            return any(n in intent for n in names)
        if _need(["kiali", "graph", "topology", "mesh"]):
            narrowed_tools += [t for t in tools_spec if t["name"] in {"get_kiali_graph", "get_kiali_service_health"}]
        if _need(["pod", "pods", "event", "logs"]):
            narrowed_tools += [t for t in tools_spec if t["name"] in {"list_pods", "get_events", "get_pod_logs"}]
        if _need(["jaeger", "trace", "latency", "slow"]):
            narrowed_tools += [t for t in tools_spec if t["name"] in {"query_jaeger_traces"}]
        if _need(["prometheus", "metric", "error rate", "request rate", "p99", "latency"]):
            narrowed_tools += [t for t in tools_spec if t["name"] in {"query_prometheus", "get_istio_metrics"}]
        if _need(["virtualservice", "destinationrule", "gateway", "routing"]):
            narrowed_tools += [t for t in tools_spec if t["name"] in {"list_virtualservices", "list_destinationrules", "list_gateways"}]
        if not narrowed_tools:
            # default minimal set
            narrowed_tools = [t for t in tools_spec if t["name"] in {"list_pods", "get_events"}]

        system_message = (
            "You are an AI SRE assistant for Kubernetes and Istio. "
            "Use the provided tools to fetch real data before answering. "
            "Be concise, cite concrete numbers, and give actionable insights."
        )

        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": user_message}
        ]
        tools_used: List[str] = []

        max_iterations = 5
        for i in range(max_iterations):
            logger.info(f"Claude tool loop iteration {i+1}")
            resp = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1200,
                system=system_message,
                tools=narrowed_tools,
                messages=messages,
            )

            stop_reason = getattr(resp, "stop_reason", None)
            logger.info(f"Stop reason: {stop_reason}")

            # If Claude produced tool calls, execute them and continue loop
            made_tool_call = False
            tool_results_blocks: List[Dict[str, Any]] = []
            assistant_content_blocks = resp.content
            for block in assistant_content_blocks:
                if getattr(block, "type", "") == "tool_use":
                    made_tool_call = True
                    tool_name = block.name
                    tool_input = block.input or {}
                    tool_id = block.id
                    tools_used.append(tool_name)
                    logger.info(f"Executing tool: {tool_name} input={tool_input}")
                    try:
                        # merge simple extracted params if missing
                        inferred = _extract_query_params(user_message)
                        merged = {**inferred, **(tool_input or {})}
                        result = execute_mcp_tool(tool_name, merged)
                        result = _summarize_tool_result(tool_name, result)
                    except Exception as tool_exc:
                        result = {"error": f"Tool execution failed: {tool_exc}"}
                    # Push result back to Claude
                    tool_results_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(result),
                    })

            if made_tool_call:
                # Append assistant's tool_use content and our tool results, then continue
                messages.append({"role": "assistant", "content": assistant_content_blocks})
                messages.append({"role": "user", "content": tool_results_blocks})
                continue

            # No tool call -> finalize answer
            final_text = ""
            for block in assistant_content_blocks:
                text_val = getattr(block, "text", None)
                if text_val:
                    final_text += text_val
            # Keep response short by default for demo; Claude should be concise
            if len(final_text) > 2000:
                final_text = final_text[:1970] + "..."
            return {"response": final_text or "", "tools_used": list(dict.fromkeys(tools_used))}

        # If loop exhausted
        return {
            "response": "Reached tool call limit. Please refine your question.",
            "tools_used": list(dict.fromkeys(tools_used)),
        }

    except Exception as e:
        logger.error(f"Exception in process_with_claude: {type(e).__name__}: {e}", exc_info=True)
        return {"response": f"Error processing request: {type(e).__name__}: {e}", "tools_used": []}

# API Endpoints

@app.get("/")
async def serve_ui():
    """Serve the web UI"""
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MCP Kubernetes Demo</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <div class="max-w-4xl mx-auto">
            <h1 class="text-3xl font-bold text-gray-800 mb-8 text-center">
                MCP Kubernetes Demo
            </h1>
            
            <div class="bg-white rounded-lg shadow-lg p-6">
                <div id="chat-container" class="h-96 overflow-y-auto border border-gray-200 rounded-lg p-4 mb-4">
                    <div class="text-gray-500 text-center">
                        Start a conversation about your Kubernetes cluster and Istio service mesh!
                    </div>
                </div>
                
                <div class="flex space-x-2">
                    <input 
                        type="text" 
                        id="message-input" 
                        placeholder="Ask about pods, services, traces, metrics..."
                        class="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                    <button 
                        id="send-button" 
                        class="bg-blue-500 text-white px-6 py-2 rounded-lg hover:bg-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                        Send
                    </button>
                </div>
            </div>
            
            <div class="mt-6 text-center text-gray-600">
                <p>Try asking:</p>
                <ul class="mt-2 space-y-1">
                    <li>"Show me the service mesh topology from Kiali"</li>
                    <li>"What's the error rate for the productpage service?"</li>
                    <li>"Find slow traces in the last hour from Jaeger"</li>
                    <li>"What's the P99 latency for the reviews service?"</li>
                </ul>
            </div>
        </div>
    </div>

    <script>
        const chatContainer = document.getElementById('chat-container');
        const messageInput = document.getElementById('message-input');
        const sendButton = document.getElementById('send-button');
        
        function addMessage(content, isUser = false) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `mb-4 ${isUser ? 'text-right' : 'text-left'}`;
            
            const bubbleDiv = document.createElement('div');
            bubbleDiv.className = `inline-block max-w-xs lg:max-w-md px-4 py-2 rounded-lg ${
                isUser 
                    ? 'bg-blue-500 text-white' 
                    : 'bg-gray-200 text-gray-800'
            }`;
            bubbleDiv.textContent = content;
            
            messageDiv.appendChild(bubbleDiv);
            chatContainer.appendChild(messageDiv);
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }
        
        function addLoadingMessage() {
            const loadingDiv = document.createElement('div');
            loadingDiv.className = 'mb-4 text-left';
            loadingDiv.innerHTML = `
                <div class="inline-block max-w-xs lg:max-w-md px-4 py-2 rounded-lg bg-gray-200 text-gray-800">
                    <div class="flex items-center space-x-2">
                        <div class="animate-spin rounded-full h-4 w-4 border-b-2 border-gray-600"></div>
                        <span>Claude is thinking...</span>
                    </div>
                </div>
            `;
            chatContainer.appendChild(loadingDiv);
            chatContainer.scrollTop = chatContainer.scrollHeight;
            return loadingDiv;
        }
        
        async function sendMessage() {
            const message = messageInput.value.trim();
            if (!message) return;
            
            // Add user message
            addMessage(message, true);
            messageInput.value = '';
            
            // Add loading message
            const loadingDiv = addLoadingMessage();
            
            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ message: message })
                });
                
                const data = await response.json();
                
                // Remove loading message
                loadingDiv.remove();
                
                // Add Claude's response
                addMessage(data.response);
                
                // Show tools used if any
                if (data.tools_used && data.tools_used.length > 0) {
                    const toolsDiv = document.createElement('div');
                    toolsDiv.className = 'mb-4 text-left';
                    toolsDiv.innerHTML = `
                        <div class="inline-block max-w-xs lg:max-w-md px-4 py-2 rounded-lg bg-green-100 text-green-800 text-sm">
                            <strong>Tools used:</strong> ${data.tools_used.join(', ')}
                        </div>
                    `;
                    chatContainer.appendChild(toolsDiv);
                }
                
            } catch (error) {
                loadingDiv.remove();
                addMessage('Error: ' + error.message);
            }
        }
        
        sendButton.addEventListener('click', sendMessage);
        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                sendMessage();
            }
        });
    </script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Handle chat requests"""
    try:
        result = process_with_claude(request.message)
        return ChatResponse(
            response=result["response"],
            tools_used=result["tools_used"]
        )
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    cluster_connected = k8s_client is not None
    return {
        "status": "healthy",
        "cluster_connected": cluster_connected,
        "prometheus_connected": prometheus_client is not None,
        "anthropic_configured": anthropic_client is not None
    }

@app.get("/tools")
async def list_tools():
    """List available MCP tools"""
    return {"tools": MCP_TOOLS}

@app.post("/tools/{tool_name}")
async def execute_tool(tool_name: str, parameters: Dict[str, Any] = None):
    """Execute a specific MCP tool"""
    if parameters is None:
        parameters = {}
    
    result = execute_mcp_tool(tool_name, parameters)
    return result

@app.on_event("startup")
async def startup_event():
    """Actions to perform on server startup"""
    logger.info("Starting MCP Kubernetes Demo Server...")
    initialize_clients()
    logger.info("Server started successfully.")

if __name__ == "__main__":
    # Initialize clients on startup
    initialize_clients()
    
    # Run the server
    uvicorn.run(app, host="0.0.0.0", port=8080)



def get_prometheus_targets() -> Dict[str, Any]:
    """Get all Prometheus scrape targets and their health status"""
    try:
        import requests
        url = f"{PROMETHEUS_ENDPOINT}/api/v1/targets"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", {})
        active_targets = data.get("activeTargets", [])
        summary = {}
        for target in active_targets:
            job = target.get("labels", {}).get("job", "unknown")
            if job not in summary:
                summary[job] = {"up": 0, "down": 0, "total": 0}
            summary[job]["total"] += 1
            if target.get("health") == "up":
                summary[job]["up"] += 1
            else:
                summary[job]["down"] += 1
        return {"targets_summary": summary, "total_targets": len(active_targets), "sample_targets": active_targets[:10]}
    except Exception as e:
        return {"error": f"Failed to get Prometheus targets: {e}"}

def query_prometheus_range(query: str, duration: str = "1h", step: str = "1m") -> Dict[str, Any]:
    """Execute PromQL range queries for time series data"""
    try:
        if not prometheus_client:
            return {"error": "Prometheus client not initialized"}
        from datetime import datetime, timedelta
        duration_map = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "3h": 180, "6h": 360, "12h": 720, "24h": 1440}
        minutes = duration_map.get(duration, 60)
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=minutes)
        result = prometheus_client.custom_query_range(query=query, start_time=start_time, end_time=end_time, step=step)
        return {"query": query, "duration": duration, "result": result}
    except Exception as e:
        return {"error": f"Failed to query Prometheus range: {e}"}

def get_service_metrics_summary(namespace: str = "default") -> Dict[str, Any]:
    """Get a comprehensive metrics summary for all services in a namespace"""
    try:
        if not prometheus_client:
            return {"error": "Prometheus client not initialized"}
        queries = {
            "request_rate": f'sum(rate(istio_requests_total{{destination_workload_namespace="{namespace}"}}[5m])) by (destination_service_name)',
            "error_rate": f'sum(rate(istio_requests_total{{destination_workload_namespace="{namespace}",response_code!~"2.."}}[5m])) by (destination_service_name)',
            "p50_latency": f'histogram_quantile(0.50, sum(rate(istio_request_duration_milliseconds_bucket{{destination_workload_namespace="{namespace}"}}[5m])) by (destination_service_name, le))',
            "p95_latency": f'histogram_quantile(0.95, sum(rate(istio_request_duration_milliseconds_bucket{{destination_workload_namespace="{namespace}"}}[5m])) by (destination_service_name, le))',
            "p99_latency": f'histogram_quantile(0.99, sum(rate(istio_request_duration_milliseconds_bucket{{destination_workload_namespace="{namespace}"}}[5m])) by (destination_service_name, le))',
        }
        results = {}
        for metric_name, query in queries.items():
            try:
                result = prometheus_client.custom_query(query=query)
                results[metric_name] = result
            except Exception as e:
                results[metric_name] = {"error": str(e)}
        return {"namespace": namespace, "metrics": results}
    except Exception as e:
        return {"error": f"Failed to get service metrics summary: {e}"}
