import click
from devlinker.detection_state import state
from devlinker.detector_ai import DevLinkerAI
from devlinker.logger import print_fix

@click.command()
def doctor():
    """Run DevLinker diagnostics and print a doctor report."""
    ai = DevLinkerAI()
    # Doctor now uses real-time, categorized issues from the global state
    state.report()
    print("\nFix Suggestions:")
    for issue, level, count, category in state.get_issues():
        suggestions = ai.analyze_failure(issue)
        for s in suggestions:
            print_fix(s)
