import os

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
        env_path = os.path.join("frontend", ".env")
        line = "VITE_API_URL=http://localhost:8001"
        # Only add if not already present
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                if line in f.read():
                    return "VITE_API_URL already set in frontend/.env"
        with open(env_path, "a") as f:
            f.write(f"\n{line}\n")
        return "Added VITE_API_URL to frontend/.env"

    def suggest_api_fix(self):
        return "Suggest: Replace hardcoded http://localhost:8000 with /api in frontend code (manual review)"
