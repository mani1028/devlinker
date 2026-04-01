class DevLinkerAI:
    def analyze_failure(self, error_text):
        lowered = error_text.lower()
        if "CORS" in error_text:
            return [
                "Frontend is calling backend directly",
                "Use /api/* instead of localhost:PORT"
            ]
        if "404" in error_text:
            if " get / " in lowered or lowered.strip().startswith("get /"):
                return []
            if "/api" not in lowered:
                return ["Route not found"]
            return [
                "Route not found",
                "Check if '/api' prefix is required"
            ]
        if "connection refused" in lowered:
            return [
                "Backend not reachable",
                "Ensure backend is running"
            ]
        return []
