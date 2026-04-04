# API Monitor CLI command for health/status dashboard
import click
from devlinker.runtime_api import fetch_issues, proxy_base_url

@click.command()
def monitor():
    """Show API health/status dashboard."""
    try:
        payload = fetch_issues()
    except Exception as exc:
        click.secho(
            f"Could not reach running DevLinker proxy at {proxy_base_url()} ({exc})",
            fg="red",
        )
        click.secho("Start DevLinker first, then run this command from another terminal.", fg="yellow")
        return

    categories = payload.get("categories", {})
    issues = payload.get("items", [])
    click.secho("\n📡 API Monitor Dashboard\n" + ("═" * 36), fg="cyan", bold=True)
    if not categories:
        categories = {"general": "OK"}
    for category, level in categories.items():
        status = "✅" if level == "OK" else "⚠️"
        click.secho(f"{category.title():<10}: {status}", fg="white")
    click.secho("\nDetails:", fg="magenta")
    if not issues:
        click.secho("✅ No issues detected yet.", fg="green")
        return

    for issue in issues:
        issue_text = issue.get("issue", "Unknown issue")
        level = str(issue.get("level", "MEDIUM")).upper()
        count = int(issue.get("count", 1))
        if level == "HIGH":
            click.secho(f"❌ {issue_text} (x{count})", fg="red")
        elif level == "MEDIUM":
            click.secho(f"⚠️  {issue_text} (x{count})", fg="yellow")
        else:
            click.secho(f"💡 {issue_text} (x{count})", fg="cyan")
