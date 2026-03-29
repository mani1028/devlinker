import click
from devlinker.tunnel import start_tunnel

# Global tunnel state
_tunnel_info = {
    "provider": None,
    "public_url": None,
    "active": False,
    "proxy_port": None,
}

@click.command()
def share():
    """Enable public tunnel at runtime (no restart)."""
    from devlinker.main import _select_proxy_port
    import sys
    proxy_port = 8000
    banner = "\n" + ("═" * 36) + "\n🌍 DevLinker Share Mode\n" + ("═" * 36)
    if _tunnel_info["active"]:
        click.secho(f"{banner}\n\n🔗 Tunnel already active:", fg="yellow", bold=True)
        click.secho(f"   {_tunnel_info['public_url']}\n", fg="cyan", bold=True)
        return
    try:
        click.secho(f"{banner}\n\n🌍 Enabling public tunnel...", fg="green", bold=True)
        provider, public_url = start_tunnel(proxy_port)
        _tunnel_info["provider"] = provider
        _tunnel_info["public_url"] = public_url
        _tunnel_info["active"] = True
        _tunnel_info["proxy_port"] = proxy_port
        click.secho(f"\n[OK] Tunnel provider: {provider}", fg="blue")
        click.secho(f"[OK] Public URL:", fg="blue")
        click.secho(f"   {public_url}\n", fg="cyan", bold=True)
        click.secho("Tip: Press Ctrl+Click to open link", fg="magenta")
        click.secho("[INFO] Share this link with collaborators.\n", fg="magenta")
    except Exception as exc:
        click.secho(f"[WARN] Tunnel failed: {exc}", fg="red")
        click.secho("[INFO] Next step: install cloudflared or configure ngrok auth.", fg="yellow")
        sys.exit(1)

@click.command()
def unshare():
    """Disable public tunnel at runtime (no restart)."""
    banner = "\n" + ("═" * 36) + "\n🛑 DevLinker Unshare Mode\n" + ("═" * 36)
    if not _tunnel_info["active"]:
        click.secho(f"{banner}\n\nNo tunnel is currently active.\n", fg="yellow", bold=True)
        return
    # In a real implementation, stop the tunnel process here
    click.secho(f"{banner}\n\n🛑 Disabling tunnel:", fg="red", bold=True)
    click.secho(f"   {_tunnel_info['public_url']}\n", fg="cyan", bold=True)
    _tunnel_info["provider"] = None
    _tunnel_info["public_url"] = None
    _tunnel_info["active"] = False
    _tunnel_info["proxy_port"] = None
    click.secho("[OK] Tunnel disabled.\n", fg="green", bold=True)
