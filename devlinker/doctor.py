import click
from devlinker.detection_state import state
from devlinker.detector_ai import DevLinkerAI
from devlinker.logger import print_fix

@click.command()
def doctor():
    """Run DevLinker diagnostics and print a health dashboard."""
    ai = DevLinkerAI()
    print("\n🩺 DevLinker Health Dashboard\n" + ("═" * 36))
    # Grouped status summary
    categories = state.categories
    for category, issues in categories.items():
        if not issues:
            status = "✅"
        else:
            high = any(state.levels.get(issue, "MEDIUM") == "HIGH" for issue in issues)
            warn = any(state.levels.get(issue, "MEDIUM") == "MEDIUM" for issue in issues)
            status = "⚠️" if high or warn else "✅"
        print(f"{category.title():<10}: {status}")
    print("\nDetails:")
    state.report()
    print("\nFix Suggestions:")
    for issue, level, count, category in state.get_issues():
        suggestions = ai.analyze_failure(issue)
        for s in suggestions:
            print_fix(s)
