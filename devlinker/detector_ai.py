class DevLinkerAI:
    def analyze_prefix_mismatch(
        self,
        api_path: str,
        prefixed_status: int,
        unprefixed_status: int,
        api_prefix: str = "/api",
    ):
        if prefixed_status != 404:
            return []
        if unprefixed_status >= 500 or unprefixed_status == 404:
            return []
        return [
            f"Detected API prefix mismatch for {api_path}",
            f"Enable strip_prefix=true so requests to {api_prefix}/* are forwarded without the prefix",
        ]

    def analyze_failure(self, error_text):
        lowered = error_text.lower()
        if "cors" in lowered:
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
                "Check if '/api' prefix is required",
                "If backend routes are root-based, enable strip_prefix=true",
            ]
        if "connection refused" in lowered:
            return [
                "Backend not reachable",
                "Ensure backend is running"
            ]
        if "502" in lowered or "unreachable" in lowered:
            return [
                "Proxy cannot reach backend",
                "If using Docker, ensure backend binds to 0.0.0.0 (not 127.0.0.1)",
                "Check mapped backend port and local firewall rules",
            ]
        return []
