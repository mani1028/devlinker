import click
from devlinker.runtime_api import fetch_logs, proxy_base_url

@click.command()
def inspect():
    """Show recent API calls and statuses."""
    click.secho("\n🔍 Recent API Calls (last 50):\n" + ("═" * 36), fg="cyan", bold=True)
    try:
        payload = fetch_logs(limit=50)
    except Exception as exc:
        click.secho(
            f"Could not reach running DevLinker proxy at {proxy_base_url()} ({exc})",
            fg="red",
        )
        click.secho("Start DevLinker first, then run this command from another terminal.", fg="yellow")
        return

    recent_requests = payload.get("items", [])
    if not recent_requests:
        click.secho("No API calls recorded yet.", fg="yellow")
        return
    for req in recent_requests[-50:]:
        status = req["status"]
        emoji = "✅" if status < 400 else ("⚠️" if status < 500 else "❌")
        click.secho(f"{emoji} {req['target']:<8} {req['path']:<30} → {status}", fg="white")
