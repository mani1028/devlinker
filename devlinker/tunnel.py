from __future__ import annotations

def stop_tunnel():
    """Stop all active tunnels (Cloudflare/ngrok)."""
    # Stop ngrok tunnels
    try:
        for tunnel in ngrok.get_tunnels():
            public_url = getattr(tunnel, "public_url", None)
            if public_url is not None:
                ngrok.disconnect(public_url)
    except Exception:
        pass
    # Stop cloudflared processes
    global _CLOUDFLARED_PROCESSES
    for proc in _CLOUDFLARED_PROCESSES:
        try:
            proc.terminate()
        except Exception:
            pass
    _CLOUDFLARED_PROCESSES.clear()

import re
import shutil
import subprocess
from subprocess import TimeoutExpired

from pyngrok import ngrok
from pyngrok.exception import PyngrokError

_TRYCLOUDFLARE_URL = re.compile(r"https://[a-z0-9.-]+\.trycloudflare\.com", re.IGNORECASE)
_CLOUDFLARED_PROCESSES: list[subprocess.Popen[str]] = []


def _extract_trycloudflare_url(output: str) -> str | None:
    match = _TRYCLOUDFLARE_URL.search(output)
    return match.group(0) if match else None


def _try_cloudflare(proxy_port: int, startup_timeout: float = 12.0) -> str | None:
    cloudflared = shutil.which("cloudflared")
    if cloudflared is None:
        return None

    command = [
        cloudflared,
        "tunnel",
        "--url",
        f"http://127.0.0.1:{proxy_port}",
        "--no-autoupdate",
    ]

    process = subprocess.Popen(  # noqa: S603
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    output = ""
    try:
        stdout, _ = process.communicate(timeout=startup_timeout)
        output = stdout or ""
    except TimeoutExpired as exc:
        # exc.stdout and exc.stderr may be str, bytes, bytearray, or memoryview; convert to str
        def to_str(val):
            if isinstance(val, str):
                return val
            if isinstance(val, (bytes, bytearray)):
                return val.decode(errors="replace")
            if isinstance(val, memoryview):
                return val.tobytes().decode(errors="replace")
            return str(val) if val is not None else ""
        exc_stdout = to_str(exc.stdout)
        exc_stderr = to_str(exc.stderr)
        output = exc_stdout + exc_stderr

    if not isinstance(output, str):
        output = str(output)

    url = _extract_trycloudflare_url(output)
    if url:
        _CLOUDFLARED_PROCESSES.append(process)
        return url

    process.terminate()
    return None


def _disconnect_existing_tunnels() -> None:
    for tunnel in ngrok.get_tunnels():
        public_url = getattr(tunnel, "public_url", None)
        if public_url is not None:
            ngrok.disconnect(public_url)


def _start_ngrok_tunnel(proxy_port: int) -> str | None:
    try:
        tunnel = ngrok.connect(str(proxy_port))
        public_url = getattr(tunnel, "public_url", None)
        if public_url is not None:
            return public_url
        return None
    except PyngrokError as exc:
        message = str(exc).lower()

        # If a prior endpoint is still active, disconnect and retry once.
        if "already online" in message or ("endpoint" in message and "online" in message):
            try:
                _disconnect_existing_tunnels()
                tunnel = ngrok.connect(str(proxy_port))
                public_url = getattr(tunnel, "public_url", None)
                if public_url is not None:
                    return public_url
                return None
            except PyngrokError as retry_exc:
                raise RuntimeError(
                    f"Failed to start ngrok tunnel after disconnect retry: {retry_exc}"
                ) from retry_exc

        if "err_ngrok_108" in message or "simultaneous ngrok agent sessions" in message:
            raise RuntimeError(
                "Failed to start ngrok tunnel: account session limit reached (ERR_NGROK_108). Close other ngrok agents in https://dashboard.ngrok.com/agents or run a single agent with multiple endpoints."
            ) from exc

        if "authtoken" in message or "err_ngrok_4018" in message or "authentication failed" in message:
            raise RuntimeError(
                "Failed to start ngrok tunnel: missing or invalid auth token. Run 'ngrok config add-authtoken <token>' and try again."
            ) from exc

        raise RuntimeError(f"Failed to start ngrok tunnel: {exc}") from exc


def start_tunnel(proxy_port: int = 8000) -> tuple[str, str]:
    """Open a public tunnel and return (provider, url)."""
    cloudflare_url = _try_cloudflare(proxy_port)
    if cloudflare_url is not None:
        return "cloudflare", cloudflare_url

    try:
        ngrok_url = _start_ngrok_tunnel(proxy_port)
        if ngrok_url is not None:
            return "ngrok", ngrok_url
        raise RuntimeError("Ngrok tunnel did not return a public URL.")
    except RuntimeError as ngrok_error:
        raise RuntimeError(
            "No tunnel available.\n"
            "Option 1 (recommended): install cloudflared and ensure it is on PATH.\n"
            "Option 2: configure ngrok auth token with 'ngrok config add-authtoken <token>'.\n"
            f"Ngrok details: {ngrok_error}"
        ) from ngrok_error
