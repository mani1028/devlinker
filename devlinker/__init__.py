"""Dev Linker package."""

try:
    from importlib.metadata import version

    __version__ = version("devlinker")
except Exception:
    __version__ = "0.0.0"

__all__ = [
    "main",
    "runner",
    "detector",
    "proxy",
    "tunnel",
]
