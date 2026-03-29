from __future__ import annotations

import socket
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import click

from . import __version__
from .detector import check_port, detect_ports, is_vite_port
from .proxy import start_proxy
from .runner import detect_backend_port, start_servers
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
            print("[WARN] Port 8000 in use")
            print(f"[INFO] Using proxy port: {candidate}")
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


def _print_summary(
    frontend_port: int,
    backend_port: int,
    proxy_port: int,
    public_url: str | None,
    wlan_url: str | None,
    startup_seconds: float,
) -> None:
    print(f"\nDevLinker Ready (in {startup_seconds:.1f}s)")
    print(f"Frontend: http://localhost:{frontend_port}")
    print(f"Backend:  http://localhost:{backend_port}")
    print("Access Links:")
    print(f"Local:  http://localhost:{proxy_port}")
    if wlan_url:
        print(f"WLAN:   {wlan_url}")
    else:
        print("WLAN:   unavailable")
    if public_url:
        print(f"Public: {public_url}")
        print("Tip: Press Ctrl+Click to open link")
    else:
        print("Public: unavailable (local proxy still active)")


def _get_local_ip() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip_address = sock.getsockname()[0]
        if ip_address and not ip_address.startswith("127."):
            return ip_address
        return None
    except OSError:
        return None
    finally:
        sock.close()


def _wait_for_readiness(
    label: str,
    port: int,
    checker,
    retries: int = 15,
    delay_seconds: float = 1.0,
) -> bool:
    print(f"[INFO] Waiting for {label} on :{port}...")
    for attempt in range(1, retries + 1):
        if checker(port):
            print(f"[OK] {label} ready on :{port}")
            return True
        if attempt < retries:
            time.sleep(delay_seconds)
    print(f"[WARN] {label} not ready on :{port} after {retries} checks")
    return False


@click.command()
@click.version_option(version=__version__, prog_name="devlinker")
@click.option("--frontend", type=int, default=None, help="Override detected frontend port.")
@click.option(
    "--backend",
    "--backend-port",
    "backend_port_override",
    type=int,
    default=None,
    help="Override detected backend port.",
)
@click.option("--proxy-port", type=int, default=8000, show_default=True, help="Proxy listen port.")
@click.option(
    "--docker",
    "auto_start_docker",
    is_flag=True,
    help="Auto-start Docker backends (manual Docker is the default).",
)
@click.option("--no-tunnel", is_flag=True, help="Skip public tunnel and run local proxy only.")
@click.option(
    "--interactive-backend/--no-interactive-backend",
    default=True,
    show_default=True,
    help="Prompt to choose backend when local and Docker candidates are both available.",
)
@click.option(
    "--lan/--no-lan",
    "lan_enabled",
    default=True,
    show_default=True,
    help="Show WLAN sharing URL for devices on the same network.",
)
@click.option("--debug", is_flag=True, hidden=True, help="Enable debug logging.")
def cli(
    frontend: int | None,
    backend_port_override: int | None,
    proxy_port: int,
    auto_start_docker: bool,
    no_tunnel: bool,
    interactive_backend: bool,
    lan_enabled: bool,
    debug: bool,
) -> None:
    started = time.perf_counter()
    print(f"\nDev Linker v{__version__}")
    print("[INFO] Mode: Auto (FastAPI async proxy + Docker detection)")
    print("[INFO] Booting local services...")

    start_servers(auto_start_docker=auto_start_docker)

    backend_port = detect_backend_port(
        default_port=5000,
        override_port=backend_port_override,
        interactive=interactive_backend,
        debug=debug,
    )
    if backend_port is None:
        raise SystemExit(1)

    print("[INFO] Detecting frontend/backend ports...")
    frontend_port, backend_port = detect_ports(frontend=frontend, backend=backend_port)

    if frontend_port is None:
        raise click.ClickException(
            "Frontend not detected on common ports. Start frontend first or set --frontend (example: 5173)."
        )
    if backend_port is None:
        raise click.ClickException(
            "Backend not detected on common ports. Start backend first or set --backend (example: 5000)."
        )

    if not _wait_for_readiness("Frontend", frontend_port, is_vite_port):
        raise click.ClickException(
            f"Frontend port {frontend_port} is reachable but does not look like a Vite dev server. "
            "Run frontend with Dev Linker or pass the correct --frontend port."
        )

    if not _wait_for_readiness("Backend", backend_port, check_port):
        raise click.ClickException(
            f"Backend port {backend_port} is not reachable. Verify backend is running and listening on localhost."
        )

    proxy_port = _select_proxy_port(proxy_port)

    print(f"[OK] Frontend -> {frontend_port}")
    print(f"[OK] Backend  -> {backend_port}\n")

    print(f"[INFO] Starting proxy on :{proxy_port}...")
    start_proxy(frontend_port, backend_port, proxy_port=proxy_port)

    # Allow proxy thread to bind before opening tunnel.
    time.sleep(1)

    wlan_url: str | None = None
    if lan_enabled:
        local_ip = _get_local_ip()
        if local_ip:
            wlan_url = f"http://{local_ip}:{proxy_port}"
            print(f"[OK] WLAN URL: {wlan_url}")
            print("[INFO] Share WLAN link with teammates on same WiFi/LAN.")
        else:
            print("[WARN] WLAN URL unavailable (no active LAN interface detected).")
            print("[INFO] If LAN sharing fails, allow proxy port in firewall and use same network.")

    print(f"\n[OK] Proxy ready at http://localhost:{proxy_port}\n")
    warning_free_url: str | None = None
    if no_tunnel:
        print("[INFO] Tunnel disabled by --no-tunnel; local proxy only.")
    else:
        try:
            print("[INFO] Opening public tunnel...")
            provider, public_url = start_tunnel(proxy_port)
            warning_free_url = _with_ngrok_skip_warning(public_url)
            provider_label = "Cloudflare" if provider == "cloudflare" else "ngrok"
            print(f"[OK] Tunnel provider: {provider_label}")
            print("[OK] Public URL:")
            print(f"     {warning_free_url}\n")
            print("Tip: Press Ctrl+Click to open link")
            print("[INFO] Share this link with collaborators.")
        except RuntimeError as exc:
            print(f"[WARN] Tunnel failed: {exc}")
            print("[INFO] Next step: install cloudflared or configure ngrok auth.")
            print("[INFO] Tip: run 'ngrok config add-authtoken <token>' for ngrok fallback.")
            print(f"[OK] Continuing with local proxy at http://localhost:{proxy_port}")

    _print_summary(
        frontend_port,
        backend_port,
        proxy_port,
        warning_free_url,
        wlan_url,
        startup_seconds=time.perf_counter() - started,
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Dev Linker stopped.")


if __name__ == "__main__":
    cli()
