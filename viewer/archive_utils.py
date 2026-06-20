"""Shared filtering for source archives sent to remote hosts."""

from pathlib import Path


EXCLUDED_DIRS = {
    ".aws",
    ".azure",
    ".cache",
    ".git",
    ".local",
    ".ssh",
    ".venv",
    "__pycache__",
    "artifacts",
    "checkpoints",
    "env",
    "logs",
    "models",
    "node_modules",
    "output",
    "secrets",
    "temp",
    "tmp",
    "venv",
}

EXCLUDED_NAMES = {
    "config.json",
    "credentials.json",
    "secrets.local.md",
    "workstations.csv",
}

EXCLUDED_SUFFIXES = (".key", ".p8", ".pem")


def should_exclude_archive_path(path: Path) -> bool:
    """Return True for generated, large, or credential-bearing paths."""
    if any(part.lower() in EXCLUDED_DIRS for part in path.parts):
        return True

    name = path.name.lower()
    return (
        name in EXCLUDED_NAMES
        or name.startswith(".env")
        or name.startswith("config.local.")
        or name.endswith(".local.md")
        or name.endswith(EXCLUDED_SUFFIXES)
    )
