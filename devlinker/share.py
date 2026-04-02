import click
import requests

from devlinker.global_state import STATE
from devlinker.tunnel import start_tunnel


_COMMON_PROXY_PORTS = (8000, 8001, 8002, 8003, 8004, 8005, 8006, 8007, 8008, 8009, 8010, 18000)


def _is_devlinker_proxy(port: int) -> bool:
    try:
        response = requests.get(f"http://127.0.0.1:{port}/__devlinker/dashboard", timeout=0.5)
    except requests.RequestException:
        return False
    return response.status_code == 200 and "API Logs Dashboard" in response.text


def _resolve_proxy_port(requested_port: int | None) -> int:
    if requested_port is not None:
        if _is_devlinker_proxy(requested_port):
            return requested_port
        raise click.ClickException(
            f"No DevLinker proxy is running on port {requested_port}. Start devlinker first, or pass the correct --proxy-port."
        )

    for candidate in _COMMON_PROXY_PORTS:
        if _is_devlinker_proxy(candidate):
            return candidate

    raise click.ClickException(
        "No running DevLinker proxy was found. Start devlinker first, or pass --proxy-port <port>."
    )

@click.command()
@click.option("--proxy-port", type=int, default=None, help="Proxy port to tunnel. Auto-detect when omitted.")
def share(proxy_port: int | None):
    """Enable public tunnel at runtime (no restart)."""
    if STATE["tunnel"]:
        click.secho("⚠️ Already shared", fg="yellow")
        return
    try:
        resolved_proxy_port = _resolve_proxy_port(proxy_port)
        STATE["proxy_port"] = resolved_proxy_port
        provider, url = start_tunnel(resolved_proxy_port)
        STATE["tunnel"] = url
        click.secho("\n🌍 Public Sharing Enabled\n" + ("─" * 24), fg="green", bold=True)
        click.secho("✔ Tunnel connected", fg="green")
        click.secho(f"\nPublic URL:\n{url}\n", fg="cyan", bold=True)
        click.secho("📤 Share this link with your team", fg="magenta")
    except Exception as exc:
        click.secho(f"[WARN] Tunnel failed: {exc}", fg="red")
        click.secho("[INFO] Next step: install cloudflared or configure ngrok auth.", fg="yellow")

@click.command()
def unshare():
    """Disable public tunnel at runtime (no restart)."""
    if not STATE["tunnel"]:
        click.secho("⚠️ No active tunnel", fg="yellow")
        return
    from devlinker.tunnel import stop_tunnel
    stop_tunnel()
    STATE["tunnel"] = None
    click.secho("🛑 Sharing stopped", fg="red", bold=True)
