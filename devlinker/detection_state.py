
class DetectionState:
    def __init__(self):
        self.issues = []
        self.counts = {}
        self.levels = {}
        self.categories = {}

    def add(self, issue, level="MEDIUM", category="general"):
        self.issues.append(issue)
        self.counts[issue] = self.counts.get(issue, 0) + 1
        self.levels[issue] = level
        self.categories.setdefault(category, []).append(issue)

    def should_print(self, issue):
        return self.counts.get(issue, 0) == 1

    def get_issues(self):
        return [(issue, self.levels.get(issue, "MEDIUM"), self.counts[issue], self._get_category(issue)) for issue in self.issues]

    def summary(self):
        summary = {}
        for issue in self.issues:
            level = self.levels.get(issue, "MEDIUM")
            summary.setdefault(level, set()).add(issue)
        return summary

    def _get_category(self, issue):
        for cat, issues in self.categories.items():
            if issue in issues:
                return cat
        return "general"

    def report(self):
        print("\n🩺 DevLinker Doctor Report\n────────────────────────")
        # Group by category
        for category, issues in self.categories.items():
            if not issues:
                continue
            if category == "network":
                print("\n🌐 Network Issues")
            elif category == "routing":
                print("\n🔀 Routing Issues")
            elif category == "cors":
                print("\n🔐 CORS Issues")
            else:
                print(f"\n{category.title()} Issues")
            for issue in set(issues):
                level = self.levels.get(issue, "MEDIUM")
                if level == "HIGH":
                    print(f"❌ {issue}")
                elif level == "MEDIUM":
                    print(f"⚠️  {issue}")
                else:
                    print(f"💡 {issue}")

# Singleton instance
state = DetectionState()
