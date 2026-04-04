from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from typing import Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

# --- RequestInspector: Real-time request analyzer ---

from devlinker.detection_state import state
import threading
_recent_requests = []
_recent_lock = threading.Lock()
_printed_fixes = set()
_printed_live_header = False
LIVE_REQUEST_LOGGING_ENABLED = False
UNIVERSAL_MODE = True
MAX_RECENT_REQUESTS = 200
PROXY_READY_EVENT = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global HTTP_CLIENT
    HTTP_CLIENT = httpx.AsyncClient(timeout=15.0, follow_redirects=False)
    PROXY_READY_EVENT.set()
    try:
        yield
    finally:
        if HTTP_CLIENT is not None:
            await HTTP_CLIENT.aclose()
            HTTP_CLIENT = None
        PROXY_READY_EVENT.clear()


app = FastAPI(lifespan=lifespan)


def _format_request_context(path: str, method: str | None, status: int, target: str) -> str:
    safe_method = method.upper() if method else "UNKNOWN"
    return f"{safe_method} {path} -> {target} ({status})"


def _print_fix_once(message: str) -> None:
    key = message.strip().lower()
    if key in _printed_fixes:
        return
    _printed_fixes.add(key)
    from devlinker.logger import print_fix
    print_fix(message)


def _should_log_live_request(path: str) -> bool:
    if not LIVE_REQUEST_LOGGING_ENABLED:
        return False
    return path == "/api" or path.startswith("/api/")


def _print_live_request_line(method: str, path: str, status: int, elapsed_ms: float) -> None:
    global _printed_live_header
    with _recent_lock:
        if not _printed_live_header:
            print("\n📡 Requests (Live)")
            _printed_live_header = True
    print(f"{method.upper():<6} {path:<24} {status:<3} {elapsed_ms:.0f}ms")


def _configured_link_token() -> str | None:
    token = os.getenv("DEVLINKER_LINK_TOKEN", "").strip()
    return token or None


def _extract_presented_token(headers: Dict[str, str], query_params) -> str | None:
    query_token = query_params.get("dl_token")
    if query_token:
        return query_token

    # WebSocket handshakes may not include dl_token in the WS URL, but the
    # browser Referer usually contains the original page URL query string.
    referer = headers.get("referer", "").strip()
    if referer:
        try:
            referer_query = dict(parse_qsl(urlsplit(referer).query, keep_blank_values=True))
            referer_token = (referer_query.get("dl_token") or "").strip()
            if referer_token:
                return referer_token
        except ValueError:
            pass

    direct_header = headers.get("x-devlinker-token", "").strip()
    if direct_header:
        return direct_header
    auth_header = headers.get("authorization", "").strip()
    bearer_prefix = "Bearer "
    if auth_header.startswith(bearer_prefix):
        return auth_header[len(bearer_prefix):].strip()
    return None


def _is_link_token_valid(expected_token: str | None, headers: Dict[str, str], query_params) -> bool:
    if not expected_token:
        return True
    presented = _extract_presented_token(headers, query_params)
    return bool(presented) and presented == expected_token

class RequestInspector:
    def analyze(self, path, status, target, method=None, response_text=None, elapsed_ms=None):
        warnings = []
        normalized_method = method.upper() if method else ""
        is_root_document_request = path == "/" and normalized_method in {"GET", "HEAD", "OPTIONS"}
        is_backend_request = target == "backend" or path == "/api" or path.startswith("/api/")
        # Ignore static files and paths
        static_exts = [".js", ".css", ".ico", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2", ".ttf", ".map"]
        IGNORE_PATHS = ["/@vite", "/assets", "/favicon.ico", "/src", "/node_modules"]
        if any(path.endswith(ext) for ext in static_exts):
            return warnings
        if any(path.startswith(p) for p in IGNORE_PATHS):
            return warnings
        # Only warn for missing /api prefix if status is 404, method is POST/PUT/DELETE, and not static/ignored
        if is_backend_request and status == 404 and normalized_method in ["POST", "PUT", "DELETE"]:
            if not path.startswith("/api"):
                # Optionally, check response_text for "Not Found"
                if response_text is None or "not found" in response_text.lower():
                    issue = f"Possible missing '/api' prefix on {path} [{method}]"
                    if state.add(issue, level="MEDIUM", category="routing"):
                        warnings.append(issue)
        # 2. 404 detection (general)
        if is_backend_request and status == 404 and not is_root_document_request:
            issue = f"Route not found → check backend route: {path}"
            if state.add(issue, level="HIGH", category="routing"):
                warnings.append(issue)
        # 3. Upstream failure
        if status == 502:
            issue = f"Backend unreachable: {path}"
            if state.add(issue, level="HIGH", category="network"):
                warnings.append(issue)
        # Log request for inspector
        with _recent_lock:
            _recent_requests.append(
                {
                    "ts": int(time.time() * 1000),
                    "method": normalized_method or "GET",
                    "path": path,
                    "status": status,
                    "target": target,
                    "latency_ms": round(float(elapsed_ms), 1) if elapsed_ms is not None else None,
                }
            )
            if len(_recent_requests) > MAX_RECENT_REQUESTS:
                _recent_requests.pop(0)
        return warnings

FRONTEND: Optional[int] = None
BACKEND: Optional[int] = None
PROXY_PORT: Optional[int] = None
HTTP_CLIENT: Optional[httpx.AsyncClient] = None
UPSTREAM_HOST_CANDIDATES: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
API_PREFIX = "/api"
STRIP_API_PREFIX = False
_UPSTREAM_HOST_CACHE: dict[int, str] = {}
_UPSTREAM_HOST_CACHE_LOCK = threading.Lock()

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _apply_security_headers(headers: Dict[str, str]) -> Dict[str, str]:
    # Explicitly allow camera/mic for the current origin.
    headers["Permissions-Policy"] = "camera=(self), microphone=(self)"
    return headers


def _apply_cors_headers(headers: Dict[str, str], request: Request) -> Dict[str, str]:
    origin = request.headers.get("origin", "").strip()
    # Credentialed CORS cannot use wildcard origin; reflect explicit Origin when present.
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    else:
        headers["Access-Control-Allow-Origin"] = "*"
    headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS,HEAD"
    requested_headers = request.headers.get("access-control-request-headers", "").strip()
    headers["Access-Control-Allow-Headers"] = requested_headers or "*"
    vary = headers.get("Vary", "")
    vary_values = [token.strip() for token in vary.split(",") if token.strip()]
    if origin and "Origin" not in vary_values:
        vary_values.append("Origin")
    headers["Vary"] = ", ".join(vary_values)
    return headers


def _connection_header_tokens(headers: Dict[str, str]) -> set[str]:
    connection = ""
    for key, value in headers.items():
        if key.lower() == "connection":
            connection = value
            break
    return {token.strip().lower() for token in connection.split(",") if token.strip()}


def _filter_request_headers(
    incoming: Dict[str, str],
    target_port: int | None = None,
    rewrite_origin_headers: bool = True,
) -> Dict[str, str]:
    connection_tokens = _connection_header_tokens(incoming)
    excluded = HOP_BY_HOP_HEADERS | connection_tokens | {"host", "content-length"}
    filtered = {k: v for k, v in incoming.items() if k.lower() not in excluded}

    if rewrite_origin_headers and target_port is not None:
        target_origin = f"http://127.0.0.1:{target_port}"
        if "origin" in filtered:
            filtered["origin"] = target_origin
        if "referer" in filtered:
            filtered["referer"] = f"{target_origin}/"

    # Preserve original request context for auth / CSRF / host-aware middleware.
    original_host = incoming.get("host")
    if original_host:
        filtered["x-forwarded-host"] = original_host

    original_proto = incoming.get("x-forwarded-proto")
    if original_proto:
        filtered["x-forwarded-proto"] = original_proto

    return filtered


def _filter_response_headers(incoming: Dict[str, str], current_origin: str | None = None) -> Dict[str, str]:
    connection_tokens = _connection_header_tokens(incoming)
    excluded = HOP_BY_HOP_HEADERS | connection_tokens | {"content-length"}
    filtered = {k: v for k, v in incoming.items() if k.lower() not in excluded}

    if current_origin:
        for key, value in list(filtered.items()):
            if key.lower() != "location" or not isinstance(value, str):
                continue
            filtered[key] = re.sub(
                r"^https?://(localhost|127\.0\.0\.1|(?:\d{1,3}\.){3}\d{1,3})(:\d+)?",
                current_origin,
                value,
                flags=re.IGNORECASE,
            )

    return filtered


def _filter_websocket_headers(incoming: Dict[str, str]) -> Dict[str, str]:
    connection_tokens = _connection_header_tokens(incoming)
    excluded = HOP_BY_HOP_HEADERS | connection_tokens | {
        "host",
        "origin",
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
    }
    return {k: v for k, v in incoming.items() if k.lower() not in excluded}


def _target_port(path: str) -> Optional[int]:
    if _is_api_path(path):
        return BACKEND
    if FRONTEND is None:
        return BACKEND
    return FRONTEND


def _is_api_path(path: str) -> bool:
    if API_PREFIX == "/":
        return True
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")


def _normalize_api_prefix(prefix: str | None) -> str:
    if not isinstance(prefix, str):
        return "/api"
    normalized = prefix.strip() or "/api"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized or "/api"


def _strip_api_prefix(path: str) -> str:
    if not _is_api_path(path):
        return path
    if path == API_PREFIX:
        return "/"
    stripped = path[len(API_PREFIX):]
    if not stripped.startswith("/"):
        stripped = f"/{stripped}"
    return stripped


def _upstream_path_for_request(path: str) -> str:
    if not STRIP_API_PREFIX:
        return path
    return _strip_api_prefix(path)


def _api_error_response(
    request: Request,
    status_code: int,
    error: str,
    suggestion: str,
    technical_detail: str | None = None,
) -> JSONResponse:
    payload: dict[str, str] = {
        "error": error,
        "suggestion": suggestion,
    }
    if technical_detail and LIVE_REQUEST_LOGGING_ENABLED:
        payload["detail"] = technical_detail
    return JSONResponse(
        payload,
        status_code=status_code,
        headers=_apply_cors_headers(_apply_security_headers({}), request),
    )


def _ordered_upstream_hosts(target_port: int) -> list[str]:
    with _UPSTREAM_HOST_CACHE_LOCK:
        cached = _UPSTREAM_HOST_CACHE.get(target_port)

    ordered: list[str] = []
    if cached:
        ordered.append(cached)
    for candidate in UPSTREAM_HOST_CANDIDATES:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _remember_upstream_host(target_port: int, host: str) -> None:
    with _UPSTREAM_HOST_CACHE_LOCK:
        _UPSTREAM_HOST_CACHE[target_port] = host


def _format_host_for_url(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _is_text_rewritable_response(content_type: str, path: str) -> bool:
    normalized_type = (content_type or "").lower()
    safe_types = (
        "text/html",
        "application/javascript",
        "text/javascript",
        "application/json",
        "text/css",
        "text/plain",
    )
    if any(token in normalized_type for token in safe_types):
        return True
    lowered_path = (path or "").lower()
    return lowered_path.endswith((".js", ".mjs", ".cjs", ".html", ".json", ".css"))


def _replace_loopback_urls(text: str, current_origin: str) -> str:
    ws_origin = current_origin.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    transformed = re.sub(
        r"https?://(?:localhost|127\.0\.0\.1|\[::1\]|::1)(?::\d{1,5})?",
        current_origin,
        text,
        flags=re.IGNORECASE,
    )
    transformed = re.sub(
        r"wss?://(?:localhost|127\.0\.0\.1|\[::1\]|::1)(?::\d{1,5})?",
        ws_origin,
        transformed,
        flags=re.IGNORECASE,
    )
    transformed = re.sub(
        r"(?<![A-Za-z0-9_.-])(?:localhost|127\.0\.0\.1|\[::1\]|::1):\d{1,5}(?![0-9])",
        current_origin,
        transformed,
        flags=re.IGNORECASE,
    )
    return transformed


def _transform_response_body(body: bytes, content_type: str, current_origin: str, path: str) -> bytes:
    if not body or not _is_text_rewritable_response(content_type, path):
        return body
    decoded = body.decode("utf-8", errors="ignore")
    rewritten = _replace_loopback_urls(decoded, current_origin)
    return rewritten.encode("utf-8")


def _devlinker_runtime_env_script() -> str:
    return (
        "<script>\n"
        "window._DEVLINKER_PROXY_URL = window.location.origin;\n"
        "window.import_meta_env = Object.assign({}, window.import_meta_env || {}, {\n"
        f"  VITE_API_URL: window.location.origin + '{API_PREFIX}'\n"
        "});\n"
        "</script>\n"
    )


def _generate_magic_patch(current_origin: str, backend_port: int, api_prefix: str) -> str:
    normalized_prefix = api_prefix if api_prefix.startswith("/") else f"/{api_prefix}"
    if len(normalized_prefix) > 1 and normalized_prefix.endswith("/"):
        normalized_prefix = normalized_prefix.rstrip("/")
    return rf"""<script>
(function() {{
    const origin = {current_origin!r};
    const backendPort = {str(backend_port)!r};
    const apiPrefix = {normalized_prefix!r};
    const localRegex = /^https?:\/\/(localhost|127\.0\.0\.1|(?:\d{{1,3}}\.){{3}}\d{{1,3}})(?::(\d+))?/i;

    function rewrite(url) {{
        const urlStr = url.toString();
        const match = urlStr.match(localRegex);
        if (!match) {{
            return urlStr;
        }}

        const port = match[2];
        let path = urlStr.replace(localRegex, "");
        if (port === backendPort && !path.startsWith(apiPrefix)) {{
            path = apiPrefix + (path.startsWith("/") ? "" : "/") + path;
        }}
        if (!path.startsWith("/")) {{
            path = "/" + path;
        }}
        return origin + path;
    }}

    const _fetch = window.fetch;
    window.fetch = (resource, init) => {{
        if (typeof resource === "string" || resource instanceof URL) {{
            resource = rewrite(resource);
        }} else if (typeof Request !== "undefined" && resource instanceof Request) {{
            const newUrl = rewrite(resource.url);
            resource = new Request(newUrl, resource);
        }}
        return _fetch(resource, init);
    }};

    if (window.XMLHttpRequest) {{
        const _open = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url, ...args) {{
            return _open.apply(this, [method, rewrite(url), ...args]);
        }};
    }}

    if (typeof window.WebSocket !== "undefined") {{
        const OriginalWebSocket = window.WebSocket;
        window.WebSocket = function(url, protocols) {{
            const newUrl = rewrite(url).replace(/^http(s?):/i, "ws$1:");
            return new OriginalWebSocket(newUrl, protocols);
        }};
        window.WebSocket.prototype = OriginalWebSocket.prototype;
    }}
}})();
</script>"""


def _inject_into_head_or_top(html: str, snippet: str) -> str:
    if re.search(r"<head[^>]*>", html, re.IGNORECASE):
        return re.sub(r"(<head[^>]*>)", r"\1\n" + snippet, html, count=1, flags=re.IGNORECASE)
    if html.lstrip().lower().startswith("<!doctype"):
        return re.sub(r"(?i)(<!doctype[^>]*>)", r"\1\n" + snippet, html, count=1)
    return snippet + html


def _build_target_http_url(
    port: int,
    path: str,
    query_params: list[tuple[str, str]],
    host: str = "127.0.0.1",
) -> str:
    query_string = urlencode(query_params, doseq=True)
    base_url = f"http://{_format_host_for_url(host)}:{port}{path}"
    if not query_string:
        return base_url
    return f"{base_url}?{query_string}"


def _build_target_ws_url(port: int, path: str, query: str, host: str = "127.0.0.1") -> str:
    base_url = f"ws://{_format_host_for_url(host)}:{port}{path}"
    if not query:
        return base_url
    return f"{base_url}?{query}"


async def _forward_http(request: Request) -> Response:
    if request.url.path == "/__devlinker/logs":
        return await logs_dashboard_data()
    if request.url.path == "/__devlinker/dashboard":
        return await logs_dashboard_page()
    if request.url.path == "/__devlinker/api/issues":
        return await issues_api_data()
    if request.url.path == "/__devlinker/api/status":
        return await status_api_data()

    if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
        return Response(
            status_code=204,
            headers=_apply_cors_headers(_apply_security_headers({}), request),
        )

    # Serve instant loader only for localhost HTML document navigations.
    client_ip = request.client.host if request.client else None
    host_header = request.headers.get("host", "").lower()

    def is_localhost_ip(ip):
        if not ip:
            return False
        return ip.startswith("127.") or ip == "localhost" or ip == "::1"

    def is_lan_ip(ip):
        if not ip:
            return False
        if ip.startswith("192.168.") or ip.startswith("10."):
            return True
        if ip.startswith("172."):
            try:
                second = int(ip.split(".")[1])
                return 16 <= second <= 31
            except Exception:
                return False
        return False

    def classify_mode(host: str, ip: str | None) -> str:
        host_only = host.split(":", 1)[0] if host else ""
        if host_only in ("localhost", "127.0.0.1", "::1"):
            return "localhost"
        if host_only.startswith("192.168.") or host_only.startswith("10."):
            return "lan"
        if host_only.startswith("172."):
            try:
                second = int(host_only.split(".")[1])
                if 16 <= second <= 31:
                    return "lan"
            except Exception:
                pass
        if host_only and host_only not in ("", "0.0.0.0"):
            return "public"
        if is_localhost_ip(ip):
            return "localhost"
        if is_lan_ip(ip):
            return "lan"
        return "public" if ip else "unknown"

    def _is_secure_request(req: Request, host: str) -> bool:
        if req.url.scheme == "https":
            return True
        forwarded_proto = req.headers.get("x-forwarded-proto", "").lower()
        if "https" in forwarded_proto:
            return True
        host_only = host.split(":", 1)[0] if host else ""
        return host_only in ("localhost", "127.0.0.1", "::1")

    mode = classify_mode(host_header, client_ip)
    is_localhost = mode == "localhost"
    is_lan = mode == "lan"
    is_public = mode == "public"
    is_secure = _is_secure_request(request, host_header)
    required_link_token = _configured_link_token()
    if (is_lan or is_public) and not _is_link_token_valid(required_link_token, dict(request.headers), request.query_params):
        return PlainTextResponse(
            "Unauthorized link: include dl_token query or X-DevLinker-Token header.",
            status_code=401,
            headers=_apply_cors_headers(_apply_security_headers({}), request),
        )
    is_instant = request.headers.get("x-devlinker-instant") == "1"
    accept_header = request.headers.get("accept", "")
    sec_fetch_dest = request.headers.get("sec-fetch-dest", "")
    is_html_request = "text/html" in accept_header.lower()
    is_document_navigation = sec_fetch_dest in ("", "document")
    is_api_path = _is_api_path(request.url.path)
    if (
        is_localhost
        and not is_instant
        and request.method == "GET"
        and is_html_request
        and is_document_navigation
        and not is_api_path
    ):
        import os
        loader_path = os.path.join(os.path.dirname(__file__), "devlinker_loader_instant.html")
        with open(loader_path, encoding="utf-8") as f:
            loader_html = f.read()
        return Response(
            content=loader_html,
            status_code=200,
            media_type="text/html",
            headers=_apply_cors_headers(_apply_security_headers({}), request),
        )
    from devlinker.logger import print_warning
    from devlinker.detector_ai import DevLinkerAI

    inspector = RequestInspector()
    ai = DevLinkerAI()

    target_port = _target_port(request.url.path)
    target_name = "backend" if target_port == BACKEND else "frontend"
    upstream_path = _upstream_path_for_request(request.url.path) if target_name == "backend" else request.url.path
    if target_port is None:
        if is_api_path:
            status = 503
            warnings = inspector.analyze(request.url.path, status, "backend", method=request.method)
            context = _format_request_context(request.url.path, request.method, status, "backend")
            for w in warnings:
                if "/api" in w:
                    print_warning(
                        f"API routing issue detected | {context}\n"
                        f"Fix: Try /api{request.url.path}"
                    )
                else:
                    print_warning(f"{w} | {context}")
            return _api_error_response(
                request,
                status,
                "Backend not configured",
                "Start the backend server and re-run DevLinker.",
            )
        status = 503
        warnings = inspector.analyze(request.url.path, status, "frontend", method=request.method)
        context = _format_request_context(request.url.path, request.method, status, "frontend")
        for w in warnings:
            print_warning(f"{w} | {context}")
        return PlainTextResponse(
            "Frontend is not configured.",
            status_code=status,
            headers=_apply_cors_headers(_apply_security_headers({}), request),
        )

    if HTTP_CLIENT is None:
        print_warning("Proxy HTTP client is not ready.")
        return PlainTextResponse(
            "Proxy HTTP client is not ready.",
            status_code=503,
            headers=_apply_cors_headers(_apply_security_headers({}), request),
        )

    stream_request_body = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    candidate_hosts = _ordered_upstream_hosts(target_port)
    if stream_request_body and candidate_hosts:
        # Request streams are single-pass; use the best-known host only.
        candidate_hosts = candidate_hosts[:1]
    query_params = list(request.query_params.multi_items())
    should_rewrite_origin = UNIVERSAL_MODE and target_name == "backend"
    filtered_request_headers = _filter_request_headers(
        dict(request.headers),
        target_port=target_port,
        rewrite_origin_headers=should_rewrite_origin,
    )
    started_at = time.perf_counter()

    try:
        upstream = None
        last_exc: httpx.RequestError | None = None
        for upstream_host in candidate_hosts:
            target_url = _build_target_http_url(
                target_port,
                upstream_path,
                query_params,
                host=upstream_host,
            )
            try:
                outbound_request = HTTP_CLIENT.build_request(
                    method=request.method,
                    url=target_url,
                    headers=filtered_request_headers,
                    content=request.stream() if stream_request_body else None,
                )
                upstream = await HTTP_CLIENT.send(outbound_request, stream=True)
                _remember_upstream_host(target_port, upstream_host)
                break
            except httpx.RequestError as exc:
                last_exc = exc

        if upstream is None and last_exc is not None:
            raise last_exc
        if upstream is None:
            raise RuntimeError("Unexpected empty upstream response")
    except httpx.RequestError as exc:
        status = 502
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        if _should_log_live_request(request.url.path):
            _print_live_request_line(request.method, request.url.path, status, elapsed_ms)
        warnings = inspector.analyze(request.url.path, status, target_name, method=request.method)
        context = _format_request_context(request.url.path, request.method, status, target_name)
        for w in warnings:
            print_warning(f"{w} | {context}")
        ai_suggestions = ai.analyze_failure(str(exc))
        for s in ai_suggestions:
            _print_fix_once(f"{s} | {context}")
        if is_api_path:
            return _api_error_response(
                request,
                status,
                "Backend not reachable",
                "Start backend server and verify it listens on localhost.",
                technical_detail=str(exc),
            )
        return PlainTextResponse(
            f"Upstream unavailable: {exc}",
            status_code=status,
            headers=_apply_cors_headers(_apply_security_headers({}), request),
        )

    # Analyze response for warnings and fixes
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    if _should_log_live_request(request.url.path):
        _print_live_request_line(request.method, request.url.path, upstream.status_code, elapsed_ms)

    context = _format_request_context(request.url.path, request.method, upstream.status_code, target_name)

    # Inject loader overlay only for LAN/WiFi/public HTML responses.
    current_origin = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
    headers = _apply_cors_headers(
        _apply_security_headers(_filter_response_headers(dict(upstream.headers), current_origin=current_origin)),
        request,
    )
    content_type = headers.get("content-type", "")
    is_html = "text/html" in content_type
    should_rewrite_assets = UNIVERSAL_MODE and _is_text_rewritable_response(content_type, request.url.path)
    should_buffer_response = upstream.status_code >= 400 or should_rewrite_assets or (
        is_html and (UNIVERSAL_MODE or is_lan or is_public)
    )

    response_text_for_analysis: str | None = None
    if should_buffer_response:
        buffered_content = await upstream.aread()
        response_text_for_analysis = buffered_content.decode(upstream.encoding or "utf-8", errors="replace")
    else:
        buffered_content = None

    warnings = inspector.analyze(
        request.url.path,
        upstream.status_code,
        target_name,
        method=request.method,
        response_text=response_text_for_analysis,
        elapsed_ms=elapsed_ms,
    )
    # Only print routing warnings for error responses or /api paths
    # Suppress false positives for frontend assets (node_modules, src, etc.) that return 200
    is_frontend_asset = any(p in request.url.path for p in ['/node_modules/', '/src/', '.jsx', '.js', '.css'])
    if upstream.status_code >= 400 or (not is_frontend_asset and not target_name == "backend"):
        for w in warnings:
            if "/api" in w:
                print_warning(
                    f"API routing issue detected | {context}\n"
                    f"Fix: Try /api{request.url.path}"
                )
            else:
                print_warning(f"{w} | {context}")
    if (target_name == "backend" or is_api_path) and upstream.status_code >= 400:
        preview = (response_text_for_analysis or "")[:500]
        ai_context = f"{request.method.upper()} {request.url.path} {upstream.status_code} {preview}"
        ai_suggestions = ai.analyze_failure(ai_context)
        for s in ai_suggestions:
            _print_fix_once(f"{s} | {context}")

    if (
        HTTP_CLIENT is not None
        and target_name == "backend"
        and _is_api_path(request.url.path)
        and not STRIP_API_PREFIX
        and upstream.status_code == 404
        and request.method.upper() in {"GET", "HEAD"}
    ):
        try:
            stripped_path = _strip_api_prefix(request.url.path)
            if stripped_path != request.url.path:
                probe_params = list(request.query_params.multi_items())
                probe_url = _build_target_http_url(target_port, stripped_path, probe_params)
                probe_response = await HTTP_CLIENT.request("GET", probe_url, headers=filtered_request_headers)
                mismatch_hints = ai.analyze_prefix_mismatch(
                    request.url.path,
                    upstream.status_code,
                    probe_response.status_code,
                    api_prefix=API_PREFIX,
                )
                for hint in mismatch_hints:
                    _print_fix_once(f"{hint} | {context}")
        except Exception:
            pass

    if buffered_content is not None:
        # Buffered responses are decoded by httpx, so encoded-body headers become invalid.
        headers.pop("content-encoding", None)
        headers.pop("Content-Encoding", None)
        if should_rewrite_assets:
            buffered_content = _transform_response_body(
                buffered_content,
                content_type,
                current_origin,
                request.url.path,
            )

        # Inject loader and runtime patching for all proxied HTML responses.
        if is_html and (UNIVERSAL_MODE or is_lan or is_public):
            try:
                html = buffered_content.decode(upstream.encoding or "utf-8", errors="replace")
                # Only inject if </body> exists.
                if "</body>" in html:
                    import os
                    loader_file = "devlinker_loader_snippet.html"
                    with open(os.path.join(os.path.dirname(__file__), loader_file), encoding="utf-8") as f:
                        loader = f.read()
                    context_script = (
                        "<script>window.__DEVLINKER_CAMERA_CONTEXT__="
                        + "{"
                        + f'"mode":"{mode}",'
                        + f'"secure":{str(is_secure).lower()}'
                        + "}"
                        + ";</script>\n"
                    )
                    env_script = _devlinker_runtime_env_script()
                    magic_patch_script = _generate_magic_patch(current_origin, BACKEND or 0, API_PREFIX)
                    import re
                    if re.search(r"<head[^>]*>", html, re.IGNORECASE):
                        html = _inject_into_head_or_top(html, env_script + context_script + magic_patch_script)
                        html = re.sub(r"(</body>)", loader + r"\1", html, flags=re.IGNORECASE)
                    else:
                        html = _inject_into_head_or_top(html, env_script + context_script + magic_patch_script)
                        html = html.replace("</body>", loader + "</body>")
                    buffered_content = html.encode(upstream.encoding or "utf-8")
            except Exception:
                pass

        if UNIVERSAL_MODE and target_name == "backend" and is_api_path and upstream.status_code >= 500:
            await upstream.aclose()
            return _api_error_response(
                request,
                upstream.status_code,
                "Backend error",
                "Check backend logs, restart backend, then retry.",
            )

        await upstream.aclose()
        return Response(
            content=buffered_content,
            status_code=upstream.status_code,
            headers=headers,
        )

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=headers,
        background=BackgroundTask(upstream.aclose),
    )


async def _proxy_websocket(websocket: WebSocket) -> None:
    required_link_token = _configured_link_token()
    if required_link_token and not _is_link_token_valid(required_link_token, dict(websocket.headers), websocket.query_params):
        await websocket.close(code=1008)
        return

    target_port = _target_port(websocket.url.path)
    upstream_path = _upstream_path_for_request(websocket.url.path)
    if target_port is None:
        await websocket.close(code=1013)
        return

    requested_subprotocols = [
        value.strip()
        for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    ]
    forward_headers = _filter_websocket_headers(dict(websocket.headers))
    try:
        connect_kwargs = {
            "subprotocols": requested_subprotocols or None,
            "open_timeout": 10,
            "ping_interval": 20,
            "ping_timeout": 20,
        }

        upstream = None
        last_error: Exception | None = None
        for upstream_host in _ordered_upstream_hosts(target_port):
            target_url = _build_target_ws_url(
                target_port,
                upstream_path,
                websocket.url.query,
                host=upstream_host,
            )
            try:
                try:
                    upstream = await websockets.connect(
                        target_url,
                        additional_headers=forward_headers,
                        **connect_kwargs,
                    )
                except TypeError:
                    upstream = await websockets.connect(
                        target_url,
                        extra_headers=forward_headers,
                        **connect_kwargs,
                    )
                _remember_upstream_host(target_port, upstream_host)
                break
            except Exception as exc:
                last_error = exc

        if upstream is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("Unexpected empty upstream websocket")

        async with upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)

            async def client_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    text = message.get("text")
                    if text is not None:
                        await upstream.send(text)
                        continue
                    binary = message.get("bytes")
                    if binary is not None:
                        await upstream.send(binary)

            async def upstream_to_client() -> None:
                while True:
                    data = await upstream.recv()
                    if isinstance(data, str):
                        await websocket.send_text(data)
                    else:
                        await websocket.send_bytes(data)

            done, pending = await asyncio.wait(
                {
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                },
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exception = task.exception()
                if exception and not isinstance(exception, (WebSocketDisconnect, ConnectionClosed)):
                    raise exception
    except Exception:
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close(code=1011)


@app.api_route(
    "/",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def http_proxy(path: str, request: Request) -> Response:  # noqa: ARG001
    return await _forward_http(request)


@app.websocket("/")
@app.websocket("/{path:path}")
async def websocket_proxy(websocket: WebSocket, path: str) -> None:  # noqa: ARG001
    await _proxy_websocket(websocket)


@app.get("/__devlinker/logs")
async def logs_dashboard_data() -> JSONResponse:
        with _recent_lock:
                records = list(_recent_requests[-100:])
        return JSONResponse({"count": len(records), "items": records})


@app.get("/__devlinker/api/issues")
async def issues_api_data() -> JSONResponse:
    return JSONResponse(state.snapshot())


@app.get("/__devlinker/api/status")
async def status_api_data() -> JSONResponse:
    return JSONResponse(
        {
            "frontend_port": FRONTEND,
            "backend_port": BACKEND,
            "proxy_port": PROXY_PORT,
            "live_request_logging": LIVE_REQUEST_LOGGING_ENABLED,
            "universal_mode": UNIVERSAL_MODE,
        }
    )


@app.get("/__devlinker/dashboard")
async def logs_dashboard_page() -> HTMLResponse:
        html = """<!doctype html>
<html>
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
    <title>DevLinker API Logs</title>
    <style>
        :root { --bg:#f4f7fb; --card:#ffffff; --ink:#0f172a; --muted:#64748b; --ok:#065f46; --warn:#92400e; --err:#991b1b; --line:#dbe3ee; }
        body { margin:0; font-family:\"Segoe UI\",\"Trebuchet MS\",sans-serif; background: radial-gradient(circle at top left,#e7f1ff,transparent 45%), var(--bg); color:var(--ink); }
        .wrap { max-width: 1100px; margin: 28px auto; padding: 0 16px; }
        .card { background: var(--card); border:1px solid var(--line); border-radius:14px; box-shadow: 0 10px 25px rgba(15,23,42,.06); overflow:hidden; }
        h1 { margin:0; font-size: 1.4rem; }
        .head { padding: 14px 16px; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--line); }
        .meta { color:var(--muted); font-size:.9rem; }
        table { width:100%; border-collapse: collapse; }
        th,td { padding:10px 12px; border-bottom:1px solid var(--line); text-align:left; font-size:.9rem; }
        th { color:var(--muted); font-weight:600; }
        .s2 { color: var(--ok); font-weight: 700; }
        .s4 { color: var(--warn); font-weight: 700; }
        .s5 { color: var(--err); font-weight: 700; }
        .path { font-family:Consolas, monospace; }
        .empty { padding: 20px; color: var(--muted); }
    </style>
</head>
<body>
    <div class=\"wrap\">
        <div class=\"card\">
            <div class=\"head\">
                <h1>API Logs Dashboard</h1>
                <div class=\"meta\" id=\"meta\">Waiting for traffic...</div>
            </div>
            <div id=\"content\" class=\"empty\">No requests yet.</div>
        </div>
    </div>
    <script>
        function statusClass(code){
            if(code >= 500) return 's5';
            if(code >= 400) return 's4';
            return 's2';
        }
        function ago(ms){
            const d = Date.now() - ms;
            if (d < 1000) return 'now';
            if (d < 60000) return Math.floor(d/1000) + 's ago';
            return Math.floor(d/60000) + 'm ago';
        }
        function render(items){
            const content = document.getElementById('content');
            const meta = document.getElementById('meta');
            if(!items.length){
                content.className = 'empty';
                content.textContent = 'No requests yet.';
                meta.textContent = 'Waiting for traffic...';
                return;
            }
            const rows = items.slice().reverse().map(item => {
                const status = Number(item.status || 0);
                const lat = item.latency_ms == null ? '-' : item.latency_ms + 'ms';
                return '<tr>' +
                    '<td>' + (item.method || '-') + '</td>' +
                    '<td class="path">' + (item.path || '-') + '</td>' +
                    '<td><span class="' + statusClass(status) + '">' + status + '</span></td>' +
                    '<td>' + (item.target || '-') + '</td>' +
                    '<td>' + lat + '</td>' +
                    '<td>' + (item.ts ? ago(item.ts) : '-') + '</td>' +
                    '</tr>';
            }).join('');
            content.className = '';
            content.innerHTML = '<table><thead><tr><th>Method</th><th>Path</th><th>Status</th><th>Target</th><th>Latency</th><th>When</th></tr></thead><tbody>' + rows + '</tbody></table>';
            meta.textContent = items.length + ' requests captured';
        }
        async function tick(){
            try{
                const resp = await fetch('/__devlinker/logs', {cache:'no-store'});
                const data = await resp.json();
                render(Array.isArray(data.items) ? data.items : []);
            }catch(_){
            }
        }
        tick();
        setInterval(tick, 1500);
    </script>
</body>
</html>"""
        return HTMLResponse(html)


def start_proxy(
    frontend_port: Optional[int],
    backend_port: int,
    proxy_port: int = 8000,
    enable_debug_logs: bool = False,
    universal_mode: bool = True,
    api_prefix: str = "/api",
    strip_prefix: bool = False,
    preferred_upstream_hosts: tuple[str, ...] | list[str] | None = None,
) -> None:
    global FRONTEND, BACKEND, PROXY_PORT, LIVE_REQUEST_LOGGING_ENABLED, UNIVERSAL_MODE, _printed_live_header, API_PREFIX, STRIP_API_PREFIX, UPSTREAM_HOST_CANDIDATES
    FRONTEND = frontend_port
    BACKEND = backend_port
    PROXY_PORT = proxy_port
    LIVE_REQUEST_LOGGING_ENABLED = enable_debug_logs
    UNIVERSAL_MODE = universal_mode
    API_PREFIX = _normalize_api_prefix(api_prefix)
    STRIP_API_PREFIX = bool(strip_prefix)
    if preferred_upstream_hosts:
        merged_hosts = list(preferred_upstream_hosts) + ["127.0.0.1", "localhost", "::1"]
        deduped_hosts: list[str] = []
        for host in merged_hosts:
            if host and host not in deduped_hosts:
                deduped_hosts.append(host)
        UPSTREAM_HOST_CANDIDATES = tuple(deduped_hosts)
    else:
        UPSTREAM_HOST_CANDIDATES = ("127.0.0.1", "localhost", "::1")
    _printed_live_header = False
    with _UPSTREAM_HOST_CACHE_LOCK:
        _UPSTREAM_HOST_CACHE.clear()
    PROXY_READY_EVENT.clear()

    def _run_server() -> None:
        if sys.platform.startswith("win") and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        config = uvicorn.Config(app, host="0.0.0.0", port=proxy_port, log_level="warning")
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # type: ignore[assignment]
        server.run()

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()


def wait_for_proxy_startup(timeout: float = 5.0) -> bool:
    return PROXY_READY_EVENT.wait(timeout)
