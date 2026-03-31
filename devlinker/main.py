from __future__ import annotations

import socket
import time
import webbrowser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import click

from . import __version__
from .detector import check_port, detect_ports, is_vite_port
from .proxy import start_proxy
from .runner import detect_backend_port, start_servers
from .tunnel import start_tunnel
from .doctor import doctor
from .fix import fix

from .share import share, unshare
from .config import load_config
from .inspect import inspect
from .monitor import monitor

SUPPORT_UPI_ID = "devlinker@upi"
SUPPORT_UPI_LINK = "upi://pay?pa=devlinker@upi&pn=DevLinker&cu=INR&tn=Support%20DevLinker%20Project%20🚀"
SUPPORT_QR_FALLBACK = [
    "#######.......#######",
    "#.....#.......#.....#",
    "#.###.#.......#.###.#",
    "#.###.#.......#.###.#",
    "#.###.#.......#.###.#",
    "#.....#.......#.....#",
    "#######.......#######",
    "##..##.##..##.##..##.",
    ".##..##..##..##..##..",
    "###...###...###...###",
    "..###....###....###..",
    "###..#.###..#.###..#.",
    "#..###.#..###.#..###.",
    "....#......#......#..",
    "#######..............",
    "#.....#..............",
    "#.###.#..............",
    "#.###.#..............",
    "#.###.#..............",
    "#.....#..............",
    "#######..............",
]


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
    import click
    banner = "\n" + ("═" * 36) + "\n🚀 DevLinker Ready\n" + ("═" * 36)
    click.secho(f"{banner}", fg="green", bold=True)
    click.secho(f"⏱️  Startup time: {startup_seconds:.1f}s\n", fg="yellow")
    click.secho(f"Frontend: ", nl=False, fg="blue"); click.secho(f"http://localhost:{frontend_port}", fg="cyan", bold=True)
    click.secho(f"Backend:  ", nl=False, fg="blue"); click.secho(f"http://localhost:{backend_port}", fg="cyan", bold=True)
    click.secho("\nAccess Links:", fg="magenta", bold=True)
    click.secho(f"  Local  → http://localhost:{proxy_port}", fg="white")
    if wlan_url:
        click.secho(f"  WLAN   → {wlan_url}", fg="white")
    else:
        click.secho("  WLAN   → unavailable", fg="white")
    if public_url:
        click.secho(f"  Public → {public_url}", fg="cyan", bold=True)
        click.secho("Tip: Press Ctrl+Click to open link", fg="magenta")
    else:
        click.secho("  Public → Disabled (use --url)", fg="yellow")
    click.secho("\n💡 Enjoying DevLinker? Support the project ❤️", fg="magenta")
    click.secho(f"UPI: {SUPPORT_UPI_ID}", fg="yellow")
    click.secho("Run: devlinker support (shows QR)", fg="yellow")


def _print_support_qr(open_link: bool) -> None:
    click.secho("\n💖 Support DevLinker 🚀", fg="magenta", bold=True)
    click.secho("Help keep the tool free and improving!", fg="white")
    click.secho(f"\nUPI: {SUPPORT_UPI_ID}", fg="yellow")
    click.secho(f"Link: {SUPPORT_UPI_LINK}\n", fg="cyan")

    try:
        import qrcode
    except ImportError:
        click.secho("ASCII QR (fallback):", fg="yellow")
        for row in SUPPORT_QR_FALLBACK:
            click.echo(row.replace("#", "##").replace(".", "  "))
        click.secho("\nInstall full QR support: pip install qrcode[pil]", fg="yellow")
        if open_link:
            webbrowser.open(SUPPORT_UPI_LINK)
        return

    qr = qrcode.QRCode(border=1)
    qr.add_data(SUPPORT_UPI_LINK)
    qr.make(fit=True)
    qr.print_ascii(invert=True)

    if open_link:
        webbrowser.open(SUPPORT_UPI_LINK)


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


@click.group(invoke_without_command=True)
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
@click.option("--url", is_flag=True, help="Enable public tunnel URL.")
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
@click.pass_context
def main(
    ctx: click.Context,
    frontend: int | None,
    backend_port_override: int | None,
    proxy_port: int,
    auto_start_docker: bool,
    url: bool,
    no_tunnel: bool,
    interactive_backend: bool,
    lan_enabled: bool,
    debug: bool,
) -> None:
    if ctx.invoked_subcommand is not None:
        return

    _run_proxy(
        frontend,
        backend_port_override,
        proxy_port,
        auto_start_docker,
        url,
        no_tunnel,
        interactive_backend,
        lan_enabled,
        debug,
    )


def _run_proxy(
    frontend: int | None,
    backend_port_override: int | None,
    proxy_port: int,
    auto_start_docker: bool,
    url: bool,
    no_tunnel: bool,
    interactive_backend: bool,
    lan_enabled: bool,
    debug: bool,
) -> None:
    # Load config file if present
    config = load_config()
    # Use config values as defaults if CLI args are not set
    if frontend is None:
        frontend = config.get("frontend")
    if backend_port_override is None:
        backend_port_override = config.get("backend")
    if proxy_port == 8000 and config.get("proxy_port"):
        proxy_port = config["proxy_port"]
    if not url and config.get("tunnel") is True:
        url = True
    if config.get("api_prefix"):
        # Optionally pass api_prefix to proxy if needed in future
        pass

    started = time.perf_counter()
    banner = "\n" + ("═" * 36) + f"\n⚡ Dev Linker v{__version__} ⚡\n" + ("═" * 36)
    click.secho(banner, fg="green", bold=True)
    click.secho("[INFO] Mode: Auto (FastAPI async proxy + Docker detection)", fg="blue")
    click.secho("[INFO] Booting local services...", fg="blue")

    start_servers(auto_start_docker=auto_start_docker)

    backend_port = detect_backend_port(
        default_port=5000,
        override_port=backend_port_override,
        interactive=interactive_backend,
        debug=debug,
    )
    if backend_port is None:
        raise SystemExit(1)

    click.secho("[INFO] Detecting frontend/backend ports...", fg="blue")
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

    click.secho(f"[OK] Frontend  → {frontend_port}", fg="green")
    click.secho(f"[OK] Backend   → {backend_port}\n", fg="green")

    click.secho(f"[INFO] Starting proxy on :{proxy_port}...", fg="blue")
    start_proxy(frontend_port, backend_port, proxy_port=proxy_port)

    # Allow proxy thread to bind before opening tunnel.
    time.sleep(1)

    wlan_url: str | None = None
    if lan_enabled:
        local_ip = _get_local_ip()
        if local_ip:
            wlan_url = f"http://{local_ip}:{proxy_port}"
            click.secho(f"[OK] WLAN URL: {wlan_url}", fg="green")
            click.secho("[INFO] Share WLAN link with teammates on same WiFi/LAN.", fg="blue")
            click.secho(
                "[WARN] Camera/mic may be blocked on WLAN HTTP links by browser security."
                " Use localhost or --url for HTTPS.",
                fg="yellow",
            )
        else:
            click.secho("[WARN] WLAN URL unavailable (no active LAN interface detected).", fg="yellow")
            click.secho("[INFO] If LAN sharing fails, allow proxy port in firewall and use same network.", fg="yellow")

    click.secho(f"\n[OK] Proxy ready at http://localhost:{proxy_port}\n", fg="green", bold=True)

    warning_free_url: str | None = None
    enable_tunnel = False
    if url:
        enable_tunnel = True
    if no_tunnel:
        enable_tunnel = False

    if enable_tunnel:
        try:
            click.secho("\n🌍 Enabling public tunnel...", fg="green", bold=True)
            provider, public_url = start_tunnel(proxy_port)
            warning_free_url = _with_ngrok_skip_warning(public_url)
            provider_label = "Cloudflare" if provider == "cloudflare" else "ngrok"
            click.secho(f"[OK] Tunnel provider: {provider_label}", fg="blue")
            click.secho(f"[OK] Public URL:", fg="blue")
            click.secho(f"     {warning_free_url}\n", fg="cyan", bold=True)
            click.secho("Tip: Press Ctrl+Click to open link", fg="magenta")
            click.secho("[INFO] Share this link with collaborators.", fg="magenta")
        except RuntimeError as exc:
            click.secho(f"[WARN] Tunnel failed: {exc}", fg="red")
            click.secho("[INFO] Next step: install cloudflared or configure ngrok auth.", fg="yellow")
            click.secho("[INFO] Tip: run 'ngrok config add-authtoken <token>' for ngrok fallback.", fg="yellow")
            click.secho(f"[OK] Continuing with local proxy at http://localhost:{proxy_port}", fg="green")
    else:
        click.secho("\n⚡ Skipping public tunnel (use --url to enable)", fg="yellow", bold=True)
        click.secho("\n💡 Need to share outside network?", fg="magenta")
        click.secho("👉 Run: devlinker --url", fg="magenta", bold=True)

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
        click.secho("\n[INFO] Dev Linker stopped.", fg="yellow")


@click.command()
@click.option(
    "--open",
    "open_link",
    is_flag=True,
    help="Open the UPI link in your browser after rendering the QR.",
)
def support(open_link: bool) -> None:
    _print_support_qr(open_link)



main.add_command(doctor)
main.add_command(fix)
main.add_command(share)
main.add_command(unshare)
main.add_command(inspect)
main.add_command(monitor)
main.add_command(support)

if __name__ == "__main__":
    main()
