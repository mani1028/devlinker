import click
from devlinker.detection_state import state
from devlinker.fixer import DevLinkerFixer

@click.command()
def fix():
    """Apply auto-fixes for detected issues."""
    issues = state.get_issues()
    fixer = DevLinkerFixer()
    print("\n🔧 Applying fixes...")
    results = fixer.apply_fixes(issues)
    print("\n🔧 Fix Results")
    for r in results:
        print(f"✔ {r}")
    if not results:
        print("No auto-fixes applied. All clear or manual review needed.")
