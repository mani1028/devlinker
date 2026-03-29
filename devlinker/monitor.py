# API Monitor CLI command for health/status dashboard
import click
from devlinker.detection_state import state

@click.command()
def monitor():
    """Show API health/status dashboard."""
    click.secho("\n📡 API Monitor Dashboard\n" + ("═" * 36), fg="cyan", bold=True)
    categories = state.categories
    for category, issues in categories.items():
        if not issues:
            status = "✅"
        else:
            high = any(state.levels.get(issue, "MEDIUM") == "HIGH" for issue in issues)
            warn = any(state.levels.get(issue, "MEDIUM") == "MEDIUM" for issue in issues)
            status = "⚠️" if high or warn else "✅"
        click.secho(f"{category.title():<10}: {status}", fg="white")
    click.secho("\nDetails:", fg="magenta")
    state.report()
