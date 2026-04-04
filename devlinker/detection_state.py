
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
            self.levels[issue] = level
            self.categories.setdefault(category, []).append(issue)
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

    def get_issue_records(self):
        records = []
        for issue in self.issues:
            issue_text = issue["issue"]
            records.append(
                {
                    "issue": issue_text,
                    "level": issue["level"],
                    "category": issue["category"],
                    "count": self.get_count(issue_text),
                }
            )
        return records

    def get_category_statuses(self):
        statuses = {}
        for category, issues in self.categories.items():
            if not issues:
                statuses[category] = "OK"
                continue
            has_high = any(self.levels.get(issue, "MEDIUM") == "HIGH" for issue in issues)
            has_medium = any(self.levels.get(issue, "MEDIUM") == "MEDIUM" for issue in issues)
            if has_high:
                statuses[category] = "HIGH"
            elif has_medium:
                statuses[category] = "MEDIUM"
            else:
                statuses[category] = "LOW"
        return statuses

    def snapshot(self):
        return {
            "total_issues": len(self.issues),
            "items": self.get_issue_records(),
            "categories": self.get_category_statuses(),
        }

# Singleton instance
state = DetectionState()
