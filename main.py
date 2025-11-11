#!/usr/bin/env python3
"""
Enhanced MCP Kubernetes Demo Server with Context Awareness and Memory
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import hashlib

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
app = FastAPI(title="MCP Kubernetes Demo", version="2.0.0")

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

# ========================
# Context Cache & Memory
# ========================

class ContextCache:
    """Manages discovered context about the cluster"""
    def __init__(self):
        self.namespaces: List[str] = []
        self.services_by_ns: Dict[str, List[str]] = {}
        self.pods_by_ns: Dict[str, List[Dict]] = {}
        self.istio_resources: Dict[str, List[str]] = {}
        self.last_refresh: Optional[datetime] = None
        self.refresh_interval = timedelta(minutes=5)
        
    def needs_refresh(self) -> bool:
        if not self.last_refresh:
            return True
        return datetime.now() - self.last_refresh > self.refresh_interval
    
    def update(self, data: Dict[str, Any]):
        self.namespaces = data.get("namespaces", [])
        self.services_by_ns = data.get("services", {})
        self.pods_by_ns = data.get("pods", {})
        self.istio_resources = data.get("istio", {})
        self.last_refresh = datetime.now()
    
    def get_summary(self) -> str:
        """Generate a context summary for Claude"""
        summary_parts = []
        
        if self.namespaces:
            summary_parts.append(f"Available namespaces: {', '.join(self.namespaces[:10])}")
        
        for ns, services in list(self.services_by_ns.items())[:5]:
            if services:
                summary_parts.append(f"Namespace '{ns}' has services: {', '.join(services[:5])}")
        
        if self.istio_resources:
            for resource_type, names in self.istio_resources.items():
                if names:
                    summary_parts.append(f"{resource_type}: {len(names)} configured")
        
        return "\n".join(summary_parts) if summary_parts else "No cluster context available yet."

class ConversationMemory:
    """Manages conversation history per session"""
    def __init__(self, max_history: int = 10):
        self.sessions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.max_history = max_history
        
    def add_message(self, session_id: str, role: str, content: Any):
        self.sessions[session_id].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        # Keep only recent history
        if len(self.sessions[session_id]) > self.max_history * 2:
            self.sessions[session_id] = self.sessions[session_id][-self.max_history * 2:]
    
    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        return self.sessions[session_id]
    
    def clear_session(self, session_id: str):
        if session_id in self.sessions:
            del self.sessions[session_id]

# Global instances
context_cache = ContextCache()
conversation_memory = ConversationMemory()

# ========================
# Utility helpers
# ========================

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
    """Produce a compact summary per tool to save tokens"""
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

def generate_session_id(user_message: str) -> str:
    """Generate a simple session ID based on first message"""
    return hashlib.md5(user_message.encode()).hexdigest()[:8]

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    tools_used: List[str]
    session_id: str
    context_summary: Optional[str] = None

def initialize_clients():
    """Initialize Kubernetes and other clients"""
    global k8s_client, prometheus_client, anthropic_client
    
    try:
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
    
    try:
        prometheus_client = PrometheusConnect(url=PROMETHEUS_ENDPOINT, disable_ssl=True)
        logger.info("Prometheus client initialized successfully")
    except Exception as e:
        logger.warning(f"Failed to initialize Prometheus client: {e}")
        prometheus_client = None
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        anthropic_client = anthropic.Anthropic(api_key=api_key)
        logger.info("Anthropic client initialized successfully")
    else:
        logger.warning("ANTHROPIC_API_KEY not found")

# ========================
# Context Discovery Tools
# ========================

def discover_cluster_context() -> Dict[str, Any]:
    """Proactively discover cluster context"""
    context = {
        "namespaces": [],
        "services": {},
        "pods": {},
        "istio": {}
    }
    
    try:
        v1 = client.CoreV1Api(k8s_client)
        
        # Get all namespaces
        ns_list = v1.list_namespace()
        context["namespaces"] = [ns.metadata.name for ns in ns_list.items]
        
        # Get services and pods for each namespace (limit to avoid overload)
        for ns in context["namespaces"][:10]:  # Limit to 10 namespaces
            try:
                # Get services
                svc_list = v1.list_namespaced_service(ns)
                context["services"][ns] = [svc.metadata.name for svc in svc_list.items]
                
                # Get pods summary
                pod_list = v1.list_namespaced_pod(ns)
                context["pods"][ns] = [{
                    "name": p.metadata.name,
                    "status": p.status.phase
                } for p in pod_list.items[:10]]  # Limit pods per namespace
            except Exception as e:
                logger.warning(f"Failed to get resources for namespace {ns}: {e}")
        
        # Get Istio resources
        try:
            custom_api = client.CustomObjectsApi(k8s_client)
            
            vs_list = custom_api.list_cluster_custom_object(
                group="networking.istio.io",
                version="v1beta1",
                plural="virtualservices"
            )
            context["istio"]["virtualservices"] = [
                vs["metadata"]["name"] for vs in vs_list.get("items", [])[:20]
            ]
            
            dr_list = custom_api.list_cluster_custom_object(
                group="networking.istio.io",
                version="v1beta1",
                plural="destinationrules"
            )
            context["istio"]["destinationrules"] = [
                dr["metadata"]["name"] for dr in dr_list.get("items", [])[:20]
            ]
        except Exception as e:
            logger.warning(f"Failed to get Istio resources: {e}")
        
    except Exception as e:
        logger.error(f"Failed to discover cluster context: {e}")
    
    return context

# ========================
# MCP Tools Implementation
# ========================

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

def get_pod_logs(pod_name: str, namespace: str = "default", container: str = None, tail_lines: int = 100) -> Dict[str, Any]:
    """Retrieve logs from a specific pod/container"""
    try:
        v1 = client.CoreV1Api(k8s_client)
        logs = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            tail_lines=tail_lines
        )
        return {"logs": logs, "pod": pod_name, "namespace": namespace}
    except ApiException as e:
        return {"error": f"Failed to get logs: {e}"}

def get_events(namespace: str = "default", limit: int = 50) -> Dict[str, Any]:
    """Get cluster events filtered by namespace"""
    try:
        v1 = client.CoreV1Api(k8s_client)
        events = v1.list_namespaced_event(namespace=namespace)
        
        result = []
        for event in events.items[:limit]:
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

def list_namespaces() -> Dict[str, Any]:
    """List all namespaces in the cluster"""
    try:
        v1 = client.CoreV1Api(k8s_client)
        namespaces = v1.list_namespace()
        
        result = [{
            "name": ns.metadata.name,
            "status": ns.status.phase,
            "age": str(datetime.now() - ns.metadata.creation_timestamp.replace(tzinfo=None)) if ns.metadata.creation_timestamp else "unknown"
        } for ns in namespaces.items]
        
        return {"namespaces": result, "count": len(result)}
    except ApiException as e:
        return {"error": f"Failed to list namespaces: {e}"}

def list_services(namespace: str = "default") -> Dict[str, Any]:
    """List all services in a namespace"""
    try:
        v1 = client.CoreV1Api(k8s_client)
        services = v1.list_namespaced_service(namespace=namespace)
        
        result = [{
            "name": svc.metadata.name,
            "namespace": svc.metadata.namespace,
            "type": svc.spec.type,
            "cluster_ip": svc.spec.cluster_ip,
            "ports": [{"port": p.port, "protocol": p.protocol} for p in svc.spec.ports] if svc.spec.ports else []
        } for svc in services.items]
        
        return {"services": result, "count": len(result)}
    except ApiException as e:
        return {"error": f"Failed to list services: {e}"}

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

def query_jaeger_traces(service: str = None, operation: str = None, start_time: str = None, end_time: str = None, limit: int = 20) -> Dict[str, Any]:
    """Query traces by service name, operation, time range from Jaeger"""
    try:
        import requests
        
        url = f"{JAEGER_ENDPOINT}/api/traces"
        params = {"limit": limit}
        
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

def list_jaeger_services() -> Dict[str, Any]:
    """List all services available in Jaeger"""
    try:
        import requests
        
        url = f"{JAEGER_ENDPOINT}/api/services"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        return {"services": response.json().get("data", [])}
    except Exception as e:
        return {"error": f"Failed to list Jaeger services: {e}"}

# ========================
# NEW: Enhanced Prometheus & Grafana Tools
# ========================

def get_prometheus_targets() -> Dict[str, Any]:
    """Get all Prometheus scrape targets and their health status"""
    try:
        import requests
        
        url = f"{PROMETHEUS_ENDPOINT}/api/v1/targets"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json().get("data", {})
        active_targets = data.get("activeTargets", [])
        
        # Summarize targets by job
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
        
        return {
            "targets_summary": summary,
            "total_targets": len(active_targets),
            "sample_targets": active_targets[:10]
        }
    except Exception as e:
        return {"error": f"Failed to get Prometheus targets: {e}"}

def query_prometheus_range(query: str, duration: str = "1h", step: str = "1m") -> Dict[str, Any]:
    """Execute PromQL range queries for time series data"""
    try:
        if not prometheus_client:
            return {"error": "Prometheus client not initialized"}
        
        # Parse duration
        duration_map = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "3h": 180, "6h": 360, "12h": 720, "24h": 1440}
        minutes = duration_map.get(duration, 60)
        
        end_time = datetime.now()
        start_time = end_time - timedelta(minutes=minutes)
        
        result = prometheus_client.custom_query_range(
            query=query,
            start_time=start_time,
            end_time=end_time,
            step=step
        )
        
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

def get_resource_usage(namespace: str = None) -> Dict[str, Any]:
    """Get CPU and memory usage metrics for pods"""
    try:
        if not prometheus_client:
            return {"error": "Prometheus client not initialized"}
        
        namespace_filter = f'namespace="{namespace}"' if namespace else ''
        
        queries = {
            "cpu_usage": f'sum(rate(container_cpu_usage_seconds_total{{{namespace_filter}}}[5m])) by (pod, namespace)',
            "memory_usage": f'sum(container_memory_working_set_bytes{{{namespace_filter}}}) by (pod, namespace)',
            "network_receive": f'sum(rate(container_network_receive_bytes_total{{{namespace_filter}}}[5m])) by (pod, namespace)',
            "network_transmit": f'sum(rate(container_network_transmit_bytes_total{{{namespace_filter}}}[5m])) by (pod, namespace)',
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
        return {"error": f"Failed to get resource usage: {e}"}

def get_grafana_dashboards() -> Dict[str, Any]:
    """List all available Grafana dashboards"""
    try:
        import requests
        
        url = f"{GRAFANA_ENDPOINT}/api/search"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        dashboards = response.json()
        
        result = [{
            "title": d.get("title"),
            "uid": d.get("uid"),
            "url": f"{GRAFANA_ENDPOINT}/d/{d.get('uid')}",
            "type": d.get("type"),
            "tags": d.get("tags", [])
        } for d in dashboards if d.get("type") == "dash-db"]
        
        return {"dashboards": result, "count": len(result)}
    except Exception as e:
        return {"error": f"Failed to get Grafana dashboards: {e}"}

def get_alertmanager_alerts() -> Dict[str, Any]:
    """Get active alerts from Prometheus Alertmanager"""
    try:
        import requests
        
        # Try to get alerts from Prometheus API
        url = f"{PROMETHEUS_ENDPOINT}/api/v1/alerts"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json().get("data", {})
        alerts = data.get("alerts", [])
        
        # Categorize by state
        active_alerts = [a for a in alerts if a.get("state") == "firing"]
        pending_alerts = [a for a in alerts if a.get("state") == "pending"]
        
        return {
            "active_count": len(active_alerts),
            "pending_count": len(pending_alerts),
            "active_alerts": active_alerts[:20],
            "pending_alerts": pending_alerts[:20]
        }
    except Exception as e:
        return {"error": f"Failed to get alerts: {e}"}

def get_top_services_by_traffic(namespace: str = None, limit: int = 10) -> Dict[str, Any]:
    """Get top services by request volume"""
    try:
        if not prometheus_client:
            return {"error": "Prometheus client not initialized"}
        
        namespace_filter = f'destination_workload_namespace="{namespace}",' if namespace else ''
        query = f'topk({limit}, sum(rate(istio_requests_total{{{namespace_filter}}}[5m])) by (destination_service_name))'
        
        result = prometheus_client.custom_query(query=query)
        
        return {"query": query, "top_services": result, "limit": limit}
    except Exception as e:
        return {"error": f"Failed to get top services: {e}"}

def get_error_rate_by_service(namespace: str = None, threshold: float = 0.01) -> Dict[str, Any]:
    """Get services with error rates above threshold"""
    try:
        if not prometheus_client:
            return {"error": "Prometheus client not initialized"}
        
        namespace_filter = f'destination_workload_namespace="{namespace}",' if namespace else ''
        
        # Calculate error rate percentage
        query = f'''
        (sum(rate(istio_requests_total{{{namespace_filter}response_code!~"2.."}}[5m])) by (destination_service_name)
        / 
        sum(rate(istio_requests_total{{{namespace_filter}}}[5m])) by (destination_service_name)) > {threshold}
        '''
        
        result = prometheus_client.custom_query(query=query.strip())
        
        return {
            "threshold": threshold,
            "services_with_errors": result
        }
    except Exception as e:
        return {"error": f"Failed to get error rates: {e}"}

# ========================
# MCP Tools Registry
# ========================

MCP_TOOLS = {
    "list_namespaces": {
        "description": "List all namespaces in the cluster. Use this first to discover what namespaces exist.",
        "parameters": {}
    },
    "list_services": {
        "description": "List all services in a specific namespace",
        "parameters": {
            "namespace": {"type": "string", "default": "default"}
        }
    },
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
            "container": {"type": "string", "optional": True},
            "tail_lines": {"type": "integer", "default": 100}
        }
    },
    "get_events": {
        "description": "Get cluster events filtered by namespace",
        "parameters": {
            "namespace": {"type": "string", "default": "default"},
            "limit": {"type": "integer", "default": 50}
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
        "description": "Get service mesh topology/graph from Kiali. Shows services and traffic flow.",
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
    "list_jaeger_services": {
        "description": "List all services that have traces in Jaeger. Use this to discover what services can be queried.",
        "parameters": {}
    },
    "query_jaeger_traces": {
        "description": "Query traces by service name, operation, time range from Jaeger",
        "parameters": {
            "service": {"type": "string", "optional": True},
            "operation": {"type": "string", "optional": True},
            "start_time": {"type": "string", "optional": True},
            "end_time": {"type": "string", "optional": True},
            "limit": {"type": "integer", "default": 20}
        }
    },
    "get_prometheus_targets": {
        "description": "Get all Prometheus scrape targets and their health status",
        "parameters": {}
    },
    "query_prometheus_range": {
        "description": "Execute PromQL range queries for time series data over a duration",
        "parameters": {
            "query": {"type": "string", "required": True},
            "duration": {"type": "string", "default": "1h"},
            "step": {"type": "string", "default": "1m"}
        }
    },
    "get_service_metrics_summary": {
        "description": "Get comprehensive metrics summary (request rate, errors, latency) for all services in a namespace",
        "parameters": {
            "namespace": {"type": "string", "default": "default"}
        }
    },
    "get_resource_usage": {
        "description": "Get CPU, memory, and network usage for pods",
        "parameters": {
            "namespace": {"type": "string", "optional": True}
        }
    },
    "get_grafana_dashboards": {
        "description": "List all available Grafana dashboards",
        "parameters": {}
    },
    "get_alertmanager_alerts": {
        "description": "Get active and pending alerts from Prometheus Alertmanager",
        "parameters": {}
    },
    "get_top_services_by_traffic": {
        "description": "Get top N services by request volume/traffic",
        "parameters": {
            "namespace": {"type": "string", "optional": True},
            "limit": {"type": "integer", "default": 10}
        }
    },
    "get_error_rate_by_service": {
        "description": "Get services with error rates above a threshold",
        "parameters": {
            "namespace": {"type": "string", "optional": True},
            "threshold": {"type": "number", "default": 0.01}
        }
    },
}

def execute_mcp_tool(tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an MCP tool with given parameters"""
    if tool_name not in MCP_TOOLS:
        return {"error": f"Unknown tool: {tool_name}"}
    
    try:
        if tool_name == "list_namespaces":
            return list_namespaces(**parameters)
        elif tool_name == "list_services":
            return list_services(**parameters)
        elif tool_name == "list_pods":
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
        elif tool_name == "list_jaeger_services":
            return list_jaeger_services(**parameters)
        elif tool_name == "query_jaeger_traces":
            return query_jaeger_traces(**parameters)
        elif tool_name == "get_prometheus_targets":
            return get_prometheus_targets(**parameters)
        elif tool_name == "query_prometheus_range":
            return query_prometheus_range(**parameters)
        elif tool_name == "get_service_metrics_summary":
            return get_service_metrics_summary(**parameters)
        elif tool_name == "get_resource_usage":
            return get_resource_usage(**parameters)
        elif tool_name == "get_grafana_dashboards":
            return get_grafana_dashboards(**parameters)
        elif tool_name == "get_alertmanager_alerts":
            return get_alertmanager_alerts(**parameters)
        elif tool_name == "get_top_services_by_traffic":
            return get_top_services_by_traffic(**parameters)
        elif tool_name == "get_error_rate_by_service":
            return get_error_rate_by_service(**parameters)
        else:
            return {"error": f"Tool {tool_name} not implemented"}
    except Exception as e:
        logger.error(f"Tool execution error: {e}")
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

def select_relevant_tools(user_message: str, all_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Intelligently select relevant tools based on user intent"""
    intent = user_message.lower()
    selected = []
    
    # Always include discovery tools
    discovery_tools = ["list_namespaces", "list_services", "list_jaeger_services"]
    
    # Kiali/Service Mesh tools
    if any(keyword in intent for keyword in ["kiali", "graph", "topology", "mesh", "service mesh", "traffic"]):
        discovery_tools.extend(["get_kiali_graph", "get_kiali_service_health"])
    
    # Pod/Event tools
    if any(keyword in intent for keyword in ["pod", "pods", "event", "events", "logs", "container", "restart"]):
        discovery_tools.extend(["list_pods", "get_events", "get_pod_logs"])
    
    # Jaeger/Tracing tools
    if any(keyword in intent for keyword in ["jaeger", "trace", "traces", "tracing", "slow", "latency", "span"]):
        discovery_tools.extend(["list_jaeger_services", "query_jaeger_traces"])
    
    # Prometheus/Metrics/Grafana tools
    if any(keyword in intent for keyword in ["prometheus", "metric", "metrics", "error rate", "request rate", 
                                              "p99", "p95", "latency", "performance", "throughput", "cpu", "memory",
                                              "resource", "usage", "grafana", "dashboard", "alert", "top services"]):
        discovery_tools.extend([
            "query_prometheus", "get_istio_metrics", "get_service_metrics_summary",
            "get_prometheus_targets", "get_resource_usage", "get_top_services_by_traffic",
            "get_error_rate_by_service", "get_grafana_dashboards", "get_alertmanager_alerts"
        ])
    
    # Istio config tools
    if any(keyword in intent for keyword in ["virtualservice", "destinationrule", "gateway", "routing", 
                                              "traffic policy", "istio config"]):
        discovery_tools.extend(["list_virtualservices", "list_destinationrules", "list_gateways"])
    
    # Namespace-specific queries
    if any(keyword in intent for keyword in ["namespace", "ns"]):
        discovery_tools.extend(["list_namespaces", "list_services", "list_pods"])
    
    # If no specific intent detected, provide comprehensive toolset
    if len(discovery_tools) <= 3:
        discovery_tools = list(MCP_TOOLS.keys())[:10]  # Limit to prevent overload
    
    # Remove duplicates while preserving order
    seen = set()
    unique_tools = []
    for tool in discovery_tools:
        if tool not in seen:
            seen.add(tool)
            unique_tools.append(tool)
    
    # Get tool definitions
    for tool in all_tools:
        if tool["name"] in unique_tools:
            selected.append(tool)
    
    logger.info(f"Selected {len(selected)} tools for query: {unique_tools}")
    return selected

def build_system_prompt(context_summary: str) -> str:
    """Build enhanced system prompt with context"""
    base_prompt = """You are an expert SRE assistant for Kubernetes and Istio service mesh environments.

Your capabilities:
- Analyze Kubernetes clusters (pods, services, events, logs)
- Monitor Istio service mesh (VirtualServices, DestinationRules, Gateways)
- Query observability tools (Kiali for topology, Jaeger for traces, Prometheus for metrics, Grafana dashboards)
- Provide actionable insights and troubleshooting recommendations

**IMPORTANT Guidelines:**
1. **Always start with discovery**: If you don't know what namespaces/services exist, use list_namespaces or list_services first
2. **Be proactive**: For Jaeger queries, always check list_jaeger_services first to see what services have traces
3. **For Kiali**: Check multiple namespaces if the default namespace is empty
4. **Provide context**: When you find issues, explain what they mean and suggest fixes
5. **Be concise but thorough**: Give actionable insights with concrete numbers
6. **Remember conversation context**: Use previous information to avoid redundant queries
7. **CRITICAL**: Make at most 3-4 tool calls per query. Prioritize the most important tools first.
8. **After gathering data, STOP and respond**. Don't keep calling tools unless absolutely necessary.

"""
    
    if context_summary:
        base_prompt += f"\n**Current Cluster Context:**\n{context_summary}\n"
    
    return base_prompt

def process_with_claude(user_message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """Process user message with Claude using enhanced context and memory"""
    logger.info("=== Starting enhanced process_with_claude ===")
    logger.info(f"User message: {user_message}")
    
    if not anthropic_client:
        logger.warning("Anthropic client not initialized!")
        return {
            "response": "Anthropic API key not configured. Please set ANTHROPIC_API_KEY environment variable.",
            "tools_used": [],
            "session_id": session_id or "no-session"
        }
    
    # Generate or use existing session ID
    if not session_id:
        session_id = generate_session_id(user_message)
    
    # Refresh cluster context if needed
    if context_cache.needs_refresh():
        logger.info("Refreshing cluster context...")
        context_data = discover_cluster_context()
        context_cache.update(context_data)
    
    try:
        all_tools = convert_mcp_tools_to_anthropic_format()
        selected_tools = select_relevant_tools(user_message, all_tools)
        
        # Build system prompt with context
        system_message = build_system_prompt(context_cache.get_summary())
        
        # Get conversation history
        history = conversation_memory.get_history(session_id)
        
        # Build messages array with history
        messages: List[Dict[str, Any]] = []
        
        # Add recent history (last 4 exchanges to save tokens)
        for msg in history[-8:]:  # Last 4 user + 4 assistant messages
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        # Add current message
        messages.append({"role": "user", "content": user_message})
        
        tools_used: List[str] = []
        max_iterations = 15  # Increased for complex workflows
        
        for i in range(max_iterations):
            logger.info(f"Claude iteration {i+1}/{max_iterations}")
            
            resp = anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,  # Increased token limit
                system=system_message,
                tools=selected_tools,
                messages=messages,
            )
            
            stop_reason = getattr(resp, "stop_reason", None)
            logger.info(f"Stop reason: {stop_reason}")
            
            # Process tool calls
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
                    
                    logger.info(f"Executing tool: {tool_name} with input: {tool_input}")
                    
                    try:
                        result = execute_mcp_tool(tool_name, tool_input)
                        result = _summarize_tool_result(tool_name, result)
                        logger.info(f"Tool result summary: {len(str(result))} chars")
                    except Exception as tool_exc:
                        logger.error(f"Tool execution error: {tool_exc}")
                        result = {"error": f"Tool execution failed: {tool_exc}"}
                    
                    tool_results_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(result),
                    })
            
            if made_tool_call:
                # Continue conversation with tool results
                messages.append({"role": "assistant", "content": assistant_content_blocks})
                messages.append({"role": "user", "content": tool_results_blocks})
                continue
            
            # No tool call - finalize response
            final_text = ""
            for block in assistant_content_blocks:
                text_val = getattr(block, "text", None)
                if text_val:
                    final_text += text_val
            
            # Store in conversation memory
            conversation_memory.add_message(session_id, "user", user_message)
            conversation_memory.add_message(session_id, "assistant", final_text)
            
            return {
                "response": final_text or "No response generated.",
                "tools_used": list(dict.fromkeys(tools_used)),
                "session_id": session_id,
                "context_summary": context_cache.get_summary()
            }
        
        # Max iterations reached
        logger.warning(f"Reached max iterations ({max_iterations})")
        return {
            "response": "I've gathered a lot of information but need to stop here. Please ask a more specific question or break down your request.",
            "tools_used": list(dict.fromkeys(tools_used)),
            "session_id": session_id
        }
        
    except Exception as e:
        logger.error(f"Exception in process_with_claude: {type(e).__name__}: {e}", exc_info=True)
        return {
            "response": f"Error processing request: {type(e).__name__}: {e}",
            "tools_used": [],
            "session_id": session_id or "error-session"
        }

# ========================
# API Endpoints
# ========================

@app.get("/")
async def serve_ui():
"""Serve the enhanced web UI"""
html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
   <meta charset="UTF-8">
   <meta name="viewport" content="width=device-width, initial-scale=1.0">
   <title>Enhanced MCP Kubernetes Demo</title>
   <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen">
   <div class="container mx-auto px-4 py-8">
       <div class="max-w-4xl mx-auto">
           <div class="text-center mb-8">
               <h1 class="text-4xl font-bold text-gray-800 mb-2">
                   🚀 Enhanced MCP Kubernetes Demo
               </h1>
               <p class="text-gray-600">Intelligent AI-powered cluster insights with memory and context awareness</p>
           </div>
           
           <div class="bg-white rounded-lg shadow-lg p-6 mb-4">
               <div class="flex justify-between items-center mb-4">
                   <h2 class="text-lg font-semibold text-gray-700">Conversation</h2>
                   <button id="clear-button" class="text-sm text-red-500 hover:text-red-700">
                       Clear History
                   </button>
               </div>
               
               <div id="chat-container" class="h-96 overflow-y-auto border border-gray-200 rounded-lg p-4 mb-4 bg-gray-50">
                   <div class="text-gray-500 text-center py-8">
                       <p class="text-lg mb-2">👋 Welcome!</p>
                       <p>I'm your intelligent SRE assistant. I can help you explore and troubleshoot your Kubernetes cluster and Istio service mesh.</p>
                       <p class="mt-2 text-sm">I'll remember our conversation and proactively gather context.</p>
                   </div>
               </div>
               
               <div class="flex space-x-2">
                   <input 
                       type="text" 
                       id="message-input" 
                       placeholder="Ask me anything about your cluster..."
                       class="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                   >
                   <button 
                       id="send-button" 
                       class="bg-blue-500 text-white px-6 py-2 rounded-lg hover:bg-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-500 transition"
                   >
                       Send
                   </button>
               </div>
           </div>
           
           <div class="bg-white rounded-lg shadow-lg p-6">
               <h3 class="text-lg font-semibold text-gray-700 mb-3">💡 Example Questions</h3>
               <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
                   <button class="example-btn text-left p-3 border border-gray-200 rounded hover:bg-blue-50 hover:border-blue-300 transition text-sm">
                       "What namespaces exist in my cluster?"
                   </button>
                   <button class="example-btn text-left p-3 border border-gray-200 rounded hover:bg-blue-50 hover:border-blue-300 transition text-sm">
                       "Show me the service mesh topology"
                   </button>
                   <button class="example-btn text-left p-3 border border-gray-200 rounded hover:bg-blue-50 hover:border-blue-300 transition text-sm">
                       "Find slow traces in Jaeger"
                   </button>
                   <button class="example-btn text-left p-3 border border-gray-200 rounded hover:bg-blue-50 hover:border-blue-300 transition text-sm">
                       "Check error rates across services"
                   </button>
                   <button class="example-btn text-left p-3 border border-gray-200 rounded hover:bg-blue-50 hover:border-blue-300 transition text-sm">
                       "Analyze my exam-preparatory namespace"
                   </button>
                   <button class="example-btn text-left p-3 border border-gray-200 rounded hover:bg-blue-50 hover:border-blue-300 transition text-sm">
                       "What services have the most traffic?"
                   </button>
               </div>
           </div>
       </div>
   </div>

   <script>
       const chatContainer = document.getElementById('chat-container');
       const messageInput = document.getElementById('message-input');
       const sendButton = document.getElementById('send-button');
       const clearButton = document.getElementById('clear-button');
       let sessionId = null;
       
       function addMessage(content, isUser = false, tools = null) {
           const messageDiv = document.createElement('div');
           messageDiv.className = `mb-4 ${isUser ? 'text-right' : 'text-left'}`;
           
           const bubbleDiv = document.createElement('div');
           bubbleDiv.className = `inline-block max-w-2xl px-4 py-3 rounded-lg ${
               isUser 
                   ? 'bg-blue-500 text-white' 
                   : 'bg-white text-gray-800 shadow border border-gray-200'
           }`;
           
           // Format content with line breaks
            const formattedContent = content.replace(/\n/g, '<br>');
            bubbleDiv.innerHTML = formattedContent;
            bubbleDiv.textContent = content;
           
           messageDiv.appendChild(bubbleDiv);
           
           // Add tools badge if present
           if (tools && tools.length > 0) {
               const toolsBadge = document.createElement('div');
               toolsBadge.className = 'inline-block mt-2 px-3 py-1 bg-green-100 text-green-700 rounded-full text-xs';
               toolsBadge.textContent = `🔧 Tools: ${tools.join(', ')}`;
               messageDiv.appendChild(toolsBadge);
           }
           
           chatContainer.appendChild(messageDiv);
           chatContainer.scrollTop = chatContainer.scrollHeight;
       }
       
       function addLoadingMessage() {
           const loadingDiv = document.createElement('div');
           loadingDiv.className = 'mb-4 text-left';
           loadingDiv.id = 'loading-message';
           loadingDiv.innerHTML = `
               <div class="inline-block px-4 py-3 rounded-lg bg-white shadow border border-gray-200">
                   <div class="flex items-center space-x-3">
                       <div class="animate-spin rounded-full h-5 w-5 border-b-2 border-blue-500"></div>
                       <span class="text-gray-600">Analyzing your cluster...</span>
                   </div>
               </div>
           `;
           chatContainer.appendChild(loadingDiv);
           chatContainer.scrollTop = chatContainer.scrollHeight;
           return loadingDiv;
       }
       
       async function sendMessage(message) {
           if (!message.trim()) return;
           
           // Add user message
           addMessage(message, true);
           messageInput.value = '';
           messageInput.disabled = true;
           sendButton.disabled = true;
           
           // Add loading message
           const loadingDiv = addLoadingMessage();
           
           try {
               const response = await fetch('/chat', {
                   method: 'POST',
                   headers: {
                       'Content-Type': 'application/json',
                   },
                   body: JSON.stringify({ 
                       message: message,
                       session_id: sessionId
                   })
               });
               
               const data = await response.json();
               
               // Store session ID
               if (data.session_id) {
                   sessionId = data.session_id;
               }
               
               // Remove loading message
               loadingDiv.remove();
               
               // Add Claude's response
               addMessage(data.response, false, data.tools_used);
               
           } catch (error) {
               loadingDiv.remove();
               addMessage('❌ Error: ' + error.message, false);
           } finally {
               messageInput.disabled = false;
               sendButton.disabled = false;
               messageInput.focus();
           }
       }
       
       sendButton.addEventListener('click', () => {
           sendMessage(messageInput.value);
       });
       
       messageInput.addEventListener('keypress', (e) => {
           if (e.key === 'Enter' && !e.shiftKey) {
               e.preventDefault();
               sendMessage(messageInput.value);
           }
       });
       
       clearButton.addEventListener('click', () => {
           if (confirm('Clear conversation history?')) {
               chatContainer.innerHTML = `
                   <div class="text-gray-500 text-center py-8">
                       <p class="text-lg mb-2">👋 Fresh start!</p>
                       <p>Conversation cleared. How can I help you?</p>
                   </div>
               `;
               sessionId = null;
           }
       });
       
       // Example button handlers
       document.querySelectorAll('.example-btn').forEach(btn => {
           btn.addEventListener('click', () => {
               const question = btn.textContent.trim().replace(/^"|"$/g, '');
               messageInput.value = question;
               sendMessage(question);
           });
       });
       
       // Focus input on load
       messageInput.focus();
   </script>
</body>
</html>
   """
return HTMLResponse(content=html_content)

@app.post("/chat")
async def chat(request: ChatRequest):
"""Handle chat requests with session support"""
try:
result = process_with_claude(request.message, request.session_id)
return {
"response": result.get("response", "No response"),
"tools_used": result.get("tools_used", []),
"session_id": result.get("session_id", "default"),
"context_summary": result.get("context_summary", None)
}
except Exception as e:
logger.error(f"Error in chat endpoint: {e}", exc_info=True)
return {
"response": f"Error: {str(e)}",
"tools_used": [],
"session_id": "error",
"context_summary": None
}

@app.get("/health")
async def health_check():
"""Health check endpoint"""
cluster_connected = k8s_client is not None
return {
"status": "healthy",
"cluster_connected": cluster_connected,
"prometheus_connected": prometheus_client is not None,
"anthropic_configured": anthropic_client is not None,
"context_cached": not context_cache.needs_refresh(),
"active_sessions": len(conversation_memory.sessions)
}

@app.get("/tools")
async def list_tools():
"""List available MCP tools"""
return {"tools": MCP_TOOLS, "count": len(MCP_TOOLS)}

@app.post("/tools/{tool_name}")
async def execute_tool(tool_name: str, parameters: Dict[str, Any] = None):
"""Execute a specific MCP tool directly"""
if parameters is None:
parameters = {}

result = execute_mcp_tool(tool_name, parameters)
return result

@app.get("/context")
async def get_context():
"""Get current cluster context"""
if context_cache.needs_refresh():
context_data = discover_cluster_context()
context_cache.update(context_data)

return {
"summary": context_cache.get_summary(),
"namespaces": context_cache.namespaces,
"services": context_cache.services_by_ns,
"last_refresh": context_cache.last_refresh.isoformat() if context_cache.last_refresh else None
}

@app.post("/context/refresh")
async def refresh_context():
"""Manually refresh cluster context"""
context_data = discover_cluster_context()
context_cache.update(context_data)
return {"status": "refreshed", "summary": context_cache.get_summary()}

@app.on_event("startup")
async def startup_event():
"""Actions to perform on server startup"""
logger.info("Starting Enhanced MCP Kubernetes Demo Server...")
initialize_clients()

# Initial context discovery
logger.info("Discovering initial cluster context...")
context_data = discover_cluster_context()
context_cache.update(context_data)
logger.info(f"Context discovered: {len(context_cache.namespaces)} namespaces")

logger.info("Server started successfully.")

if __name__ == "__main__":
initialize_clients()
uvicorn.run(app, host="0.0.0.0", port=8080)
