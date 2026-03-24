from __future__ import annotations

import socket
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import click

from . import __version__
from .detector import check_port, detect_ports, is_vite_port
from .proxy import start_proxy
from .runner import start_servers
from .tunnel import start_tunnel


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _select_proxy_port(requested_port: int) -> int:
    if not _is_port_in_use(requested_port):
        return requested_port

    if requested_port != 8000:
        raise click.ClickException(
            f"Proxy port {requested_port} is already in use. Choose another with --proxy-port."
        )

    for candidate in (8001, 8002, 18000):
        if not _is_port_in_use(candidate):
            print(f"Port 8000 is busy. Falling back to proxy port {candidate}.")
            return candidate

    raise click.ClickException(
        "No free proxy port found in fallback list (8000, 8001, 8002, 18000)."
    )


def _with_ngrok_skip_warning(url: str) -> str:
    parts = urlsplit(url)
    if "ngrok" not in parts.netloc:
        return url

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["ngrok-skip-browser-warning"] = "true"
    new_query = urlencode(query)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


@click.command()
@click.version_option(version=__version__, prog_name="devlinker")
@click.option("--frontend", type=int, default=None, help="Override detected frontend port.")
@click.option("--backend", type=int, default=None, help="Override detected backend port.")
@click.option("--proxy-port", type=int, default=8000, show_default=True, help="Proxy listen port.")
def cli(frontend: int | None, backend: int | None, proxy_port: int) -> None:
    print(f"\n✨ Dev Linker v{__version__}\n")
    print("🚀 Starting services...")

    start_servers()

    print("🔍 Detecting services...")
    frontend_port, backend_port = detect_ports(frontend=frontend, backend=backend)

    if frontend_port is None:
        raise click.ClickException(
            "Frontend not detected on common ports. Try: devlinker --frontend 5173"
        )
    if backend_port is None:
        raise click.ClickException(
            "Backend not detected on common ports. Try: devlinker --backend 5000"
        )

    if not is_vite_port(frontend_port):
        raise click.ClickException(
            f"Frontend port {frontend_port} is reachable but does not look like a Vite dev server. "
            "Run frontend with Dev Linker or pass the correct --frontend port."
        )

    if not check_port(backend_port):
        raise click.ClickException(
            f"Backend port {backend_port} is not reachable. Try: devlinker --backend 5000"
        )

    proxy_port = _select_proxy_port(proxy_port)

    print(f"   • Frontend -> {frontend_port}")
    print(f"   • Backend  -> {backend_port}\n")

    print(f"🌐 Starting proxy on :{proxy_port}...")
    start_proxy(frontend_port, backend_port, proxy_port=proxy_port)

    # Allow Flask thread to bind before opening tunnel.
    time.sleep(1)

    print(f"\n🌐 Proxy ready at http://localhost:{proxy_port}\n")
    try:
        print("⚡ Opening public tunnel...")
        provider, public_url = start_tunnel(proxy_port)
        warning_free_url = _with_ngrok_skip_warning(public_url)
        provider_label = "Cloudflare" if provider == "cloudflare" else "ngrok"
        print(f"⚡ Tunnel provider: {provider_label}")
        print("🌍 Public URL:")
        print(f"   {warning_free_url}\n")
        print("👉 Share this link with anyone")
    except RuntimeError as exc:
        print(f"⚠ Tunnel unavailable: {exc}")
        print(f"🌐 Local proxy remains available at http://localhost:{proxy_port}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDev Linker stopped.")


if __name__ == "__main__":
    cli()
