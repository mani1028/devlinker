
import click
from devlinker.global_state import STATE
from devlinker.tunnel import start_tunnel

@click.command()
def share():
    """Enable public tunnel at runtime (no restart)."""
    if STATE["tunnel"]:
        click.secho("⚠️ Already shared", fg="yellow")
        return
    try:
        provider, url = start_tunnel(STATE["proxy_port"])
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
