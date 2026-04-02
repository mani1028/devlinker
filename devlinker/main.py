from __future__ import annotations

import os
import socket
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import click

try:
    from rich import box
    from rich.align import Align
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text

    _RICH_AVAILABLE = True
    _CONSOLE = Console()
except ImportError:  # pragma: no cover - fallback when rich is unavailable
    box = None
    Align = None
    Console = None
    Live = None
    Panel = None
    Text = None
    _RICH_AVAILABLE = False
    _CONSOLE = None

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

def _ui_print(message: str, style: str | None = None) -> None:
    if _CONSOLE:
        _CONSOLE.print(message, style=style)
    else:
        click.secho(message)


def _ui_status(icon: str, message: str, style: str | None = None) -> None:
    _ui_print(f"{icon} {message}", style=style)


class _LiveStatus:
    def __init__(self, console: Any) -> None:
        self._console = console
        self._order = ["Frontend", "Backend", "Proxy"]
        if Text is not None:
            self._rows: dict[str, Any] = {
                "Frontend": Text("⏳ Starting...", style="cyan"),
                "Backend": Text("⏳ Starting...", style="cyan"),
                "Proxy": Text("⏳ Starting...", style="cyan"),
            }
        else:
            self._rows = {
                "Frontend": "Starting...",
                "Backend": "Starting...",
                "Proxy": "Starting...",
            }
        self._live: Any = None

    def start(self) -> None:
        if Live is None:
            return
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=8,
            transient=True,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.stop()
            self._live = None

    def update(self, label: str, text: str, style: str | None = None) -> None:
        if Text is None:
            return
        self._rows[label] = Text(text, style=style) if style else Text(text)
        if self._live:
            self._live.update(self._render())

    def _render(self) -> Any:
        if Text is None:
            return ""
        width = max(len(label) for label in self._order)
        lines = []
        for label in self._order:
            prefix = Text(f"{label:<{width}}  ")
            value = self._rows.get(label, Text(""))
            lines.append(prefix + value)
        output = Text()
        for index, line in enumerate(lines):
            if index:
                output.append("\n")
            output.append_text(line)
        return output


def _can_use_live() -> bool:
    return False


def _print_banner() -> None:
    if _CONSOLE and Panel and Text and Align and box:
        title = Text(f"♾️  DevLinker v{__version__}", style="bold white")
        subtitle = Text("Smart Local Dev Environment", style="dim")
        body = Align.center(Text.assemble(title, "\n", subtitle))
        panel = Panel.fit(body, box=box.ROUNDED, border_style="cyan")
        _CONSOLE.print(panel)
        return

    banner = "\n" + ("═" * 36) + f"\n♾️  DevLinker v{__version__} ⚡\n" + ("═" * 36)
    click.secho(banner, fg="green", bold=True)


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

    candidates = list(range(requested_port + 1, requested_port + 11)) + [18000]
    for candidate in candidates:
        if not _is_port_in_use(candidate):
            _ui_status("⚠", f"Port {requested_port} in use", style="yellow")
            _ui_status("ℹ", f"Using proxy port: {candidate}", style="blue")
            return candidate

    raise click.ClickException(
        f"No free proxy port found near {requested_port}. Try --proxy-port with another value."
    )


def _with_ngrok_skip_warning(url: str) -> str:
    parts = urlsplit(url)
    if "ngrok" not in parts.netloc:
        return url

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["ngrok-skip-browser-warning"] = "true"
    new_query = urlencode(query)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def _with_link_token(url: str) -> str:
    token = os.getenv("DEVLINKER_LINK_TOKEN", "").strip()
    if not token:
        return url

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["dl_token"] = token
    new_query = urlencode(query)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))



def _print_summary(
    frontend_port: int | None,
    backend_port: int,
    proxy_port: int,
    public_url: str | None,
    wlan_url: str | None,
    startup_seconds: float,
) -> None:
    if _CONSOLE and Panel and box:
        lines = [
            f"Proxy     http://localhost:{proxy_port}",
        ]
        lines.append(f"WLAN      {wlan_url}" if wlan_url else "WLAN      unavailable")
        lines.append(f"Public    {public_url}" if public_url else "Public    disabled (use --url)")
        body = "\n".join(lines)
        panel = Panel.fit(body, title="DevLinker Ready", border_style="green", box=box.ROUNDED)
        _CONSOLE.print(panel)
        _ui_print(f"✨ Ready in {startup_seconds:.1f}s", style="bold green")
        _ui_print("Powered by DevLinker 🚀", style="magenta")
        _ui_print("", style=None)
        _ui_print("💖 Support DevLinker", style="bold magenta")
        _ui_print("Help keep this tool free & growing!", style="white")
        _ui_print("👉 Run: devlinker support", style="magenta")
        _ui_print(f"💳 UPI: {SUPPORT_UPI_ID}", style="yellow")
        return

    banner = "\n" + ("═" * 36) + "\n🚀 DevLinker Ready\n" + ("═" * 36)
    click.secho(banner, fg="green", bold=True)
    click.secho(f"✨ Ready in {startup_seconds:.1f}s", fg="green")
    click.secho("Powered by DevLinker 🚀", fg="magenta")
    click.secho(f"Proxy:    http://localhost:{proxy_port}", fg="white")
    if wlan_url:
        click.secho(f"WLAN:     {wlan_url}", fg="white")
    else:
        click.secho("WLAN:     unavailable", fg="white")
    if public_url:
        click.secho(f"Public:   {public_url}", fg="cyan", bold=True)
        click.secho("Tip: Press Ctrl+Click to open link", fg="magenta")
    else:
        click.secho("Public:   disabled (use --url)", fg="yellow")
    click.secho("\n💖 Support DevLinker 🚀", fg="magenta", bold=True)
    click.secho("Help keep this tool free & growing!", fg="white")
    click.secho("👉 Run: devlinker support", fg="magenta")
    click.secho(f"💳 UPI: {SUPPORT_UPI_ID}", fg="yellow")


def _print_support_qr(open_link: bool) -> None:
    if _CONSOLE and Panel and box:
        panel = Panel.fit(
            "Scan the QR to support the project",
            title="💖 Support DevLinker",
            border_style="magenta",
            box=box.ROUNDED,
        )
        _CONSOLE.print(panel)
    else:
        banner = "\n" + ("═" * 36) + "\n💖 Support DevLinker 🚀\n" + ("═" * 36)
        click.secho(banner, fg="magenta", bold=True)
        click.secho("Powered by DevLinker 🚀", fg="magenta")
        click.secho("\nScan the QR to support the project:", fg="white")

    try:
        import qrcode
    except ImportError:
        click.secho("\nASCII QR (fallback):", fg="yellow")
        for row in SUPPORT_QR_FALLBACK:
            click.echo(row.replace("#", "##").replace(".", "  "))
        click.secho("\nInstall full QR support: pip install qrcode[pil]", fg="yellow")
        click.secho(f"\nUPI ID: {SUPPORT_UPI_ID}", fg="yellow")
        click.secho(f"Link: {SUPPORT_UPI_LINK}\n", fg="cyan")
        click.secho("Your support helps:", fg="white")
        click.secho("✔ Keep this tool free", fg="green")
        click.secho("✔ Add new features", fg="green")
        click.secho("✔ Improve performance", fg="green")
        click.secho("\nThank you 🙌", fg="magenta")
        if open_link:
            webbrowser.open(SUPPORT_UPI_LINK)
        return

    qr = qrcode.QRCode(border=1)
    qr.add_data(SUPPORT_UPI_LINK)
    qr.make(fit=True)
    qr.print_ascii(invert=True)

    click.secho(f"\nUPI ID: {SUPPORT_UPI_ID}", fg="yellow")
    click.secho(f"Link: {SUPPORT_UPI_LINK}\n", fg="cyan")
    click.secho("Your support helps:", fg="white")
    click.secho("✔ Keep this tool free", fg="green")
    click.secho("✔ Add new features", fg="green")
    click.secho("✔ Improve performance", fg="green")
    click.secho("\nThank you 🙌", fg="magenta")

    if open_link:
        webbrowser.open(SUPPORT_UPI_LINK)


def _write_frontend_api_env(proxy_port: int) -> None:
    """Keep frontend calls routed through the proxy in local development."""
    frontend_dir = Path("frontend")
    if not frontend_dir.is_dir():
        return

    env_path = frontend_dir / ".env.local"
    start_marker = "# devlinker-managed:start"
    end_marker = "# devlinker-managed:end"
    managed_block = (
        f"{start_marker}\n"
        f"VITE_API_URL=http://localhost:{proxy_port}\n"
        f"{end_marker}"
    )

    try:
        existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        if start_marker in existing and end_marker in existing:
            before, _, tail = existing.partition(start_marker)
            _, _, after = tail.partition(end_marker)
            updated = f"{before}{managed_block}{after}"
        elif existing.strip():
            updated = f"{existing.rstrip()}\n\n{managed_block}\n"
        else:
            updated = f"{managed_block}\n"

        env_path.write_text(updated, encoding="utf-8")
        _ui_status("✔", f"Updated frontend/.env.local with VITE_API_URL=http://localhost:{proxy_port}", style="green")
    except OSError:
        _ui_status("⚠", "Could not update frontend/.env.local; set VITE_API_URL manually.", style="yellow")


def _get_local_ips() -> list[str]:
    def _is_usable_ipv4(ip_address: str | None) -> bool:
        if not ip_address:
            return False
        if ip_address.startswith("127.") or ip_address.startswith("169.254."):
            return False
        return True

    candidates: list[str] = []

    def _add_candidate(ip_address: str | None) -> None:
        if not _is_usable_ipv4(ip_address):
            return
        if ip_address is None:
            return
        if ip_address in candidates:
            return
        candidates.append(ip_address)

    def _is_private_lan_ipv4(ip_address: str) -> bool:
        if ip_address.startswith("10.") or ip_address.startswith("192.168."):
            return True
        if ip_address.startswith("172."):
            try:
                second_octet = int(ip_address.split(".")[1])
                return 16 <= second_octet <= 31
            except (IndexError, ValueError):
                return False
        return False

    # Prefer outbound-route detection first.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        _add_candidate(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()

    # Fallback to hostname resolution when default-route probing is unavailable.
    try:
        _hostname, _aliases, addresses = socket.gethostbyname_ex(socket.gethostname())
        for candidate in addresses:
            _add_candidate(candidate)
    except OSError:
        pass

    # Final fallback using addrinfo to handle edge resolver setups.
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM)
        for info in infos:
            candidate = info[4][0]
            if not isinstance(candidate, str):
                continue
            _add_candidate(candidate)
    except OSError:
        pass

    private_candidates = [ip for ip in candidates if _is_private_lan_ipv4(ip)]
    other_candidates = [ip for ip in candidates if ip not in private_candidates]
    return private_candidates + other_candidates


def _get_local_ip() -> str | None:
    candidates = _get_local_ips()
    return candidates[0] if candidates else None


def _wait_for_readiness(
    label: str,
    port: int,
    checker,
    retries: int = 15,
    delay_seconds: float = 1.0,
) -> bool:
    _ui_status("⏳", f"Waiting for {label} ({port})...", style="cyan")
    for attempt in range(1, retries + 1):
        if checker(port):
            _ui_status("✔", f"{label} is live ({port})", style="green")
            return True
        if attempt < retries:
            time.sleep(delay_seconds)
    _ui_status("⚠", f"{label} not ready on {port} after {retries} checks", style="yellow")
    return False


def _wait_for_readiness_live(
    label: str,
    port: int,
    checker,
    live_status: _LiveStatus,
    retries: int = 15,
    delay_seconds: float = 1.0,
) -> bool:
    live_status.update(label, f"⏳ Waiting ({port})...", style="cyan")
    for attempt in range(1, retries + 1):
        if checker(port):
            live_status.update(label, f"✔ Running ({port})", style="green")
            return True
        if attempt < retries:
            time.sleep(delay_seconds)
    live_status.update(label, f"⚠ Not ready ({port})", style="yellow")
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
    help="Prompt to choose backend when multiple backends are detected.",
)
@click.option(
    "--lan/--no-lan",
    "lan_enabled",
    default=True,
    show_default=True,
    help="Show WLAN sharing URL for devices on the same network.",
)
@click.option("--debug", is_flag=True, help="Enable debug mode and live API request logging.")
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
    _print_banner()
    _ui_status("✔", "Detecting project...", style="green")
    _ui_status("⏳", "Booting local services...", style="cyan")

    live_status = None
    if _can_use_live():
        live_status = _LiveStatus(_CONSOLE)
        live_status.start()

    start_servers(auto_start_docker=auto_start_docker)

    backend_port = detect_backend_port(
        default_port=5000,
        override_port=backend_port_override,
        interactive=interactive_backend,
        debug=debug,
    )
    if backend_port is None:
        raise SystemExit(1)

    if not live_status:
        _ui_status("⏳", "Detecting ports...", style="cyan")
    frontend_port, backend_port = detect_ports(frontend=frontend, backend=backend_port)

    if backend_port is None:
        raise click.ClickException(
            "Backend not detected.\n"
            "Possible reasons:\n"
            "- Backend server is not started\n"
            "- Wrong backend port\n"
            "- App crashed during startup\n"
            "Fix: start backend first, or pass --backend (example: 5000)."
        )

    if frontend_port is not None:
        if live_status:
            frontend_ready = _wait_for_readiness_live(
                "Frontend",
                frontend_port,
                is_vite_port,
                live_status,
            )
        else:
            frontend_ready = _wait_for_readiness("Frontend", frontend_port, is_vite_port)

        if not frontend_ready:
            raise click.ClickException(
                f"Frontend port {frontend_port} is reachable but does not look like a Vite dev server. "
                "Run frontend with Vite, or pass the correct --frontend port."
            )
    elif live_status:
        live_status.update("Frontend", "⚠ Not detected (backend-only mode)", style="yellow")
    else:
        _ui_status("⚠", "Frontend not detected; running backend-only mode.", style="yellow")

    if live_status:
        backend_ready = _wait_for_readiness_live(
            "Backend",
            backend_port,
            check_port,
            live_status,
        )
    else:
        backend_ready = _wait_for_readiness("Backend", backend_port, check_port)

    if not backend_ready:
        raise click.ClickException(
            f"Backend port {backend_port} is not reachable. Verify backend is running and listening on localhost."
        )

    proxy_port = _select_proxy_port(proxy_port)
    _write_frontend_api_env(proxy_port)

    if not live_status:
        frontend_status = str(frontend_port) if frontend_port is not None else "none"
        _ui_status("✔", f"Frontend: {frontend_status} | Backend: {backend_port}", style="green")

    if live_status:
        live_status.update("Proxy", f"⏳ Starting ({proxy_port})...", style="cyan")
    else:
        _ui_status("⏳", f"Starting proxy ({proxy_port})...", style="cyan")
    start_proxy(
        frontend_port,
        backend_port,
        proxy_port=proxy_port,
        enable_debug_logs=debug,
    )

    # Allow proxy thread to bind before opening tunnel.
    time.sleep(1)

    if live_status:
        live_status.update("Proxy", f"✔ Active ({proxy_port})", style="green")

    wlan_url: str | None = None
    if lan_enabled:
        local_ips = _get_local_ips()
        if local_ips:
            wlan_url = _with_link_token(f"http://{local_ips[0]}:{proxy_port}")
            _ui_status("✔", f"LAN share: {wlan_url}", style="green")
            if len(local_ips) > 1:
                alternative_urls = ", ".join(_with_link_token(f"http://{ip}:{proxy_port}") for ip in local_ips[1:])
                _ui_status("ℹ", f"Alternate LAN URLs: {alternative_urls}", style="blue")
            _ui_status("ℹ", "Share with teammates on the same WiFi/LAN.", style="blue")
            if os.getenv("DEVLINKER_LINK_TOKEN", "").strip():
                _ui_status("🔒", "Token protection is ON for LAN/public traffic.", style="green")
            _ui_status(
                "⚠",
                "Camera/mic may be blocked on HTTP. Use localhost or --url for HTTPS.",
                style="yellow",
            )
            _ui_status(
                "ℹ",
                "If link does not open on other devices, allow Python/DevLinker in firewall and ensure both devices are on same subnet.",
                style="blue",
            )
        else:
            _ui_status("⚠", "LAN URL unavailable (no active interface detected).", style="yellow")
            _ui_status("ℹ", "Tip: connect to WiFi, disable VPN/proxy adapters, then restart DevLinker.", style="blue")

    if not live_status:
        _ui_status("✔", f"Proxy ready: http://localhost:{proxy_port}", style="green")
        _ui_status("ℹ", f"Use http://localhost:{proxy_port} as the single app entry point.", style="blue")
        if debug:
            _ui_status("🛠", "Debug mode enabled: live API request logger is ON", style="magenta")

    warning_free_url: str | None = None
    enable_tunnel = False
    if url:
        enable_tunnel = True
    if no_tunnel:
        enable_tunnel = False

    if enable_tunnel:
        try:
            _ui_status("🌍", "Enabling public tunnel...", style="green")
            provider, public_url = start_tunnel(proxy_port)
            warning_free_url = _with_link_token(_with_ngrok_skip_warning(public_url))
            provider_label = "Cloudflare" if provider == "cloudflare" else "ngrok"
            _ui_status("✔", f"Tunnel provider: {provider_label}", style="blue")
            _ui_status("✔", f"Public URL: {warning_free_url}", style="cyan")
            _ui_status("ℹ", "Tip: Ctrl+Click to open link", style="magenta")
            _ui_status("ℹ", "Share this link with collaborators.", style="magenta")
        except RuntimeError as exc:
            _ui_status("✖", f"Tunnel failed: {exc}", style="red")
            _ui_status("ℹ", "Install cloudflared or configure ngrok auth.", style="yellow")
            _ui_status("ℹ", "Tip: run 'ngrok config add-authtoken <token>'", style="yellow")
            _ui_status("✔", f"Continuing with local proxy at http://localhost:{proxy_port}", style="green")
    else:
        _ui_status("⚡", "Public tunnel disabled (use --url)", style="yellow")

    if live_status:
        live_status.stop()

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
            time.sleep(0.5)
    except KeyboardInterrupt:
        _ui_status("ℹ", "DevLinker stopped.", style="yellow")


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
