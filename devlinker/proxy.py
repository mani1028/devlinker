from __future__ import annotations

import threading
from typing import Dict, Optional

import requests
from flask import Flask, Response, request

app = Flask(__name__)

FRONTEND: Optional[int] = None
BACKEND: Optional[int] = None


def _filter_headers(incoming: Dict[str, str]) -> Dict[str, str]:
    excluded = {
        "host",
        "content-length",
        "connection",
        "accept-encoding",
        "upgrade",
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
    }
    return {k: v for k, v in incoming.items() if k.lower() not in excluded}


def _forward(target_url: str) -> Response:
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return Response(
            "WebSocket upgrade is not supported by the Dev Linker HTTP proxy. "
            "For shared links, run Vite with HMR disabled.",
            status=426,
        )

    payload = request.get_data() if request.method in {"POST", "PUT", "PATCH"} else None
    query_params = list(request.args.items(multi=True))

    try:
        upstream = requests.request(
            method=request.method,
            url=target_url,
            params=query_params,
            data=payload,
            headers=_filter_headers(dict(request.headers)),
            cookies=request.cookies,
            allow_redirects=False,
            timeout=15,
        )
    except requests.RequestException as exc:
        return Response(f"Upstream unavailable: {exc}", status=502)

    response = Response(upstream.content, status=upstream.status_code)
    for key, value in upstream.headers.items():
        if key.lower() in {"content-length", "transfer-encoding", "connection"}:
            continue
        response.headers[key] = value
    return response


@app.route("/api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def api_proxy(path: str) -> Response:
    if BACKEND is None:
        return Response("Backend is not configured.", status=503)
    return _forward(f"http://localhost:{BACKEND}/api/{path}")


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def frontend_proxy(path: str) -> Response:
    if FRONTEND is None:
        return Response("Frontend is not configured.", status=503)

    target = f"http://localhost:{FRONTEND}/{path}" if path else f"http://localhost:{FRONTEND}/"
    return _forward(target)


def start_proxy(frontend_port: int, backend_port: int, proxy_port: int = 8000) -> None:
    global FRONTEND, BACKEND
    FRONTEND = frontend_port
    BACKEND = backend_port

    thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=proxy_port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
