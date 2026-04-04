import click
from devlinker.fixer import DevLinkerFixer
from devlinker.runtime_api import fetch_issues, proxy_base_url

@click.command()
def fix():
    """Apply auto-fixes for detected issues."""
    try:
        payload = fetch_issues()
    except Exception as exc:
        click.secho(
            f"Could not reach running DevLinker proxy at {proxy_base_url()} ({exc})",
            fg="red",
        )
        click.secho("Start DevLinker first, then run this command from another terminal.", fg="yellow")
        return

    issues = payload.get("items", [])
    fixer = DevLinkerFixer()
    print("\n🔧 Applying fixes...")
    results = fixer.apply_fixes(issues)
    print("\n🔧 Fix Results")
    for r in results:
        print(f"✔ {r}")
    if not results:
        print("No auto-fixes applied. All clear or manual review needed.")
