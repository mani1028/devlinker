from __future__ import annotations

import asyncio
import threading
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx
import uvicorn
import websockets
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import PlainTextResponse
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

app = FastAPI()

# --- RequestInspector: Real-time request analyzer ---

from devlinker.detection_state import state
import threading
_recent_requests = []
_recent_lock = threading.Lock()

class RequestInspector:
    def analyze(self, path, status, target, method=None, response_text=None):
        warnings = []
        # Ignore static files and paths
        static_exts = [".js", ".css", ".ico", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2", ".ttf", ".map"]
        IGNORE_PATHS = ["/@vite", "/assets", "/favicon.ico", "/src", "/node_modules"]
        if any(path.endswith(ext) for ext in static_exts):
            return warnings
        if any(path.startswith(p) for p in IGNORE_PATHS):
            return warnings
        # Only warn for missing /api prefix if status is 404, method is POST/PUT/DELETE, and not static/ignored
        if status == 404 and method and method.upper() in ["POST", "PUT", "DELETE"]:
            if not path.startswith("/api"):
                # Optionally, check response_text for "Not Found"
                if response_text is None or "not found" in response_text.lower():
                    issue = f"Possible missing '/api' prefix on {path} [{method}]"
                    if state.add(issue, level="MEDIUM", category="routing"):
                        warnings.append(issue)
        # 2. 404 detection (general)
        if status == 404:
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
            _recent_requests.append({"path": path, "status": status, "target": target})
            if len(_recent_requests) > 50:
                _recent_requests.pop(0)
        return warnings

FRONTEND: Optional[int] = None
BACKEND: Optional[int] = None
HTTP_CLIENT: Optional[httpx.AsyncClient] = None

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


@app.on_event("startup")
async def _on_startup() -> None:
    global HTTP_CLIENT
    HTTP_CLIENT = httpx.AsyncClient(timeout=15.0, follow_redirects=False)


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    global HTTP_CLIENT
    if HTTP_CLIENT is not None:
        await HTTP_CLIENT.aclose()
        HTTP_CLIENT = None


def _connection_header_tokens(headers: Dict[str, str]) -> set[str]:
    connection = ""
    for key, value in headers.items():
        if key.lower() == "connection":
            connection = value
            break
    return {token.strip().lower() for token in connection.split(",") if token.strip()}


def _filter_request_headers(incoming: Dict[str, str]) -> Dict[str, str]:
    connection_tokens = _connection_header_tokens(incoming)
    excluded = HOP_BY_HOP_HEADERS | connection_tokens | {"host", "content-length"}
    return {k: v for k, v in incoming.items() if k.lower() not in excluded}


def _filter_response_headers(incoming: Dict[str, str]) -> Dict[str, str]:
    connection_tokens = _connection_header_tokens(incoming)
    excluded = HOP_BY_HOP_HEADERS | connection_tokens | {"content-length"}
    return {k: v for k, v in incoming.items() if k.lower() not in excluded}


def _filter_websocket_headers(incoming: Dict[str, str]) -> Dict[str, str]:
    connection_tokens = _connection_header_tokens(incoming)
    excluded = HOP_BY_HOP_HEADERS | connection_tokens | {
        "host",
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
    }
    return {k: v for k, v in incoming.items() if k.lower() not in excluded}


def _target_port(path: str) -> Optional[int]:
    if path == "/api" or path.startswith("/api/"):
        return BACKEND
    return FRONTEND


def _build_target_http_url(port: int, path: str, query_params: list[tuple[str, str]]) -> str:
    query_string = urlencode(query_params, doseq=True)
    base_url = f"http://127.0.0.1:{port}{path}"
    if not query_string:
        return base_url
    return f"{base_url}?{query_string}"


def _build_target_ws_url(port: int, path: str, query: str) -> str:
    base_url = f"ws://127.0.0.1:{port}{path}"
    if not query:
        return base_url
    return f"{base_url}?{query}"


async def _forward_http(request: Request) -> Response:
    from devlinker.logger import print_warning, print_fix
    from devlinker.detector_ai import DevLinkerAI

    inspector = RequestInspector()
    ai = DevLinkerAI()

    target_port = _target_port(request.url.path)
    if target_port is None:
        if request.url.path.startswith("/api"):
            status = 503
            warnings = inspector.analyze(request.url.path, status, "backend", method=request.method)
            for w in warnings:
                if "/api" in w:
                    print_warning(f"API routing issue detected\n👉 {request.url.path} returned 404\n👉 Try: /api{request.url.path}")
                else:
                    print_warning(w)
            return PlainTextResponse("Backend is not configured.", status_code=status)
        status = 503
        warnings = inspector.analyze(request.url.path, status, "frontend", method=request.method)
        for w in warnings:
            print_warning(w)
        return PlainTextResponse("Frontend is not configured.", status_code=status)

    if HTTP_CLIENT is None:
        print_warning("Proxy HTTP client is not ready.")
        return PlainTextResponse("Proxy HTTP client is not ready.", status_code=503)

    payload = await request.body()
    query_params = list(request.query_params.multi_items())
    target_url = _build_target_http_url(target_port, request.url.path, query_params)

    try:
        upstream = await HTTP_CLIENT.request(
            method=request.method,
            url=target_url,
            content=payload,
            headers=_filter_request_headers(dict(request.headers)),
        )
    except httpx.RequestError as exc:
        status = 502
        warnings = inspector.analyze(request.url.path, status, "backend", method=request.method)
        for w in warnings:
            print_warning(w)
        ai_suggestions = ai.analyze_failure(str(exc))
        for s in ai_suggestions:
            print_fix(s)
        return PlainTextResponse(f"Upstream unavailable: {exc}", status_code=status)

    # Analyze response for warnings and fixes
    warnings = inspector.analyze(
        request.url.path,
        upstream.status_code,
        "backend",
        method=request.method,
        response_text=upstream.text
    )
    for w in warnings:
        if "/api" in w:
            print_warning(f"API routing issue detected\n👉 {request.url.path} returned 404\n👉 Try: /api{request.url.path}")
        else:
            print_warning(w)
    ai_suggestions = ai.analyze_failure(str(upstream.text))
    for s in ai_suggestions:
        print_fix(s)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_response_headers(dict(upstream.headers)),
    )


async def _proxy_websocket(websocket: WebSocket) -> None:
    target_port = _target_port(websocket.url.path)
    if target_port is None:
        await websocket.close(code=1013)
        return

    requested_subprotocols = [
        value.strip()
        for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    ]
    forward_headers = _filter_websocket_headers(dict(websocket.headers))
    target_url = _build_target_ws_url(target_port, websocket.url.path, websocket.url.query)

    try:
        connect_kwargs = {
            "subprotocols": requested_subprotocols or None,
            "open_timeout": 10,
            "ping_interval": 20,
            "ping_timeout": 20,
        }
        try:
            upstream_connect = websockets.connect(
                target_url,
                additional_headers=forward_headers,
                **connect_kwargs,
            )
        except TypeError:
            upstream_connect = websockets.connect(
                target_url,
                extra_headers=forward_headers,
                **connect_kwargs,
            )

        async with upstream_connect as upstream:
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


def start_proxy(frontend_port: int, backend_port: int, proxy_port: int = 8000) -> None:
    global FRONTEND, BACKEND
    FRONTEND = frontend_port
    BACKEND = backend_port

    thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="0.0.0.0", port=proxy_port, log_level="warning"),
        daemon=True,
    )
    thread.start()
