class DevLinkerAI:
    def analyze_failure(self, error_text):
        if "CORS" in error_text:
            return [
                "Frontend is calling backend directly",
                "Use /api/* instead of localhost:PORT"
            ]
        if "404" in error_text:
            return [
                "Route not found",
                "Check if '/api' prefix is required"
            ]
        if "connection refused" in error_text.lower():
            return [
                "Backend not reachable",
                "Ensure backend is running"
            ]
        return []
