import click
from devlinker.detector_ai import DevLinkerAI
from devlinker.logger import print_fix
from devlinker.runtime_api import fetch_issues, proxy_base_url

@click.command()
def doctor():
    """Run DevLinker diagnostics and print a health dashboard."""
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
    categories = payload.get("categories", {})
    ai = DevLinkerAI()
    print("\n🩺 DevLinker Health Dashboard\n" + ("═" * 36))
    # Grouped status summary
    if not categories:
        categories = {"general": "OK"}
    for category, level in categories.items():
        status = "✅" if level == "OK" else "⚠️"
        print(f"{category.title():<10}: {status}")
    print("\nDetails:")
    if not issues:
        print("✅ No issues detected yet.")
    else:
        for issue in issues:
            issue_text = issue.get("issue", "Unknown issue")
            level = str(issue.get("level", "MEDIUM")).upper()
            count = int(issue.get("count", 1))
            if level == "HIGH":
                print(f"❌ {issue_text} (x{count})")
            elif level == "MEDIUM":
                print(f"⚠️  {issue_text} (x{count})")
            else:
                print(f"💡 {issue_text} (x{count})")
    print("\nFix Suggestions:")
    for issue in issues:
        issue_text = issue.get("issue", "")
        if not issue_text:
            continue
        suggestions = ai.analyze_failure(issue_text)
        for s in suggestions:
            print_fix(s)
