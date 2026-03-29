
class DetectionState:
    def __init__(self):
        self.issues = []
        self.counts = {}
        self.levels = {}
        self.categories = {}

    def add(self, issue, level="MEDIUM", category="general"):
        key = issue.strip().lower()
        if key in self.counts:
            self.counts[key] += 1
            return False  # already shown
        else:
            self.counts[key] = 1
            self.issues.append({
                "issue": issue,
                "level": level,
                "category": category
            })
            return True  # first time

    def get_count(self, issue):
        key = issue.strip().lower()
        return self.counts.get(key, 0)

    def should_print(self, issue):
        key = issue.strip().lower()
        return self.counts.get(key, 0) == 1

    def get_issues(self):
        return [
            (i["issue"], i["level"], self.get_count(i["issue"]), i["category"])
            for i in self.issues
        ]

    def summary(self):
        summary = {}
        for i in self.issues:
            level = i["level"]
            summary.setdefault(level, set()).add(i["issue"])
        return summary

    def _get_category(self, issue):
        for i in self.issues:
            if i["issue"] == issue:
                return i["category"]
        return "general"

    def report(self):
        print("\n🩺 DevLinker Doctor Report\n────────────────────────")
        # Group by category
        for i in self.issues:
            issue = i["issue"]
            level = i["level"]
            category = i["category"]
            count = self.get_count(issue)
            if level == "HIGH":
                print(f"❌ {issue} (x{count})")
            elif level == "MEDIUM":
                print(f"⚠️  {issue} (x{count})")
            else:
                print(f"💡 {issue} (x{count})")

# Singleton instance
state = DetectionState()
