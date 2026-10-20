import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_size(path: Path, max_size_mb: int) -> None:
    if path.stat().st_size > max_size_mb * 1024 * 1024:
        raise ValueError(f"file exceeds {max_size_mb} MB limit: {path.name}")
