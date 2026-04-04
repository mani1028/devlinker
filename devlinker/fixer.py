class DevLinkerFixer:
    def apply_fixes(self, issues):
        fixes = []
        for issue in issues:
            desc = issue[0] if isinstance(issue, (list, tuple)) else issue.get("issue", "")
            if "CORS" in desc:
                fixes.append(self.fix_env())
            if "Missing /api" in desc or "missing '/api'" in desc:
                fixes.append(self.suggest_api_fix())
        return fixes

    def fix_env(self):
        return (
            "Runtime injection is active: no .env update needed. "
            "Use the DevLinker proxy URL and verify API calls go through /api."
        )

    def suggest_api_fix(self):
        return "Suggest: Replace hardcoded http://localhost:8000 with /api in frontend code (manual review)"
