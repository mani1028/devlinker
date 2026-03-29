import click
from devlinker.proxy import _recent_requests

@click.command()
def inspect():
    """Show recent API calls and statuses."""
    click.secho("\n🔍 Recent API Calls (last 50):\n" + ("═" * 36), fg="cyan", bold=True)
    if not _recent_requests:
        click.secho("No API calls recorded yet.", fg="yellow")
        return
    for req in _recent_requests[-50:]:
        status = req["status"]
        emoji = "✅" if status < 400 else ("⚠️" if status < 500 else "❌")
        click.secho(f"{emoji} {req['target']:<8} {req['path']:<30} → {status}", fg="white")
