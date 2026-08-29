from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import folder_paths


CACHE_CATEGORIES = ("transcripts", "thumbnails", "media", "metadata")
_CACHE_OWNER = "alice_lab_audio_tools"


def cache_root() -> Path:
    """Return a persistent, server-internal ALICE Lab cache root."""
    system_user_directory = getattr(folder_paths, "get_system_user_directory", None)
    if system_user_directory is not None:
        owner_root = Path(system_user_directory(_CACHE_OWNER))
    else:
        # Older compatible ComfyUI builds may not expose system-user helpers.
        owner_root = Path(folder_paths.get_user_directory()) / f"__{_CACHE_OWNER}"
    owner_root = Path(os.path.abspath(owner_root))
    if owner_root.is_symlink():
        raise RuntimeError(f"Unsafe ALICE Lab cache owner path: {owner_root}")
    return owner_root / "cache"


def cache_key(kind: str, identity: Any, settings: Any = None) -> str:
    """Build a deterministic key without exposing source paths or URLs."""
    payload = json.dumps(
        {"version": 1, "kind": kind, "identity": identity, "settings": settings},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def local_source_identity(path: str | Path) -> dict[str, int | str]:
    resolved = Path(path).expanduser().resolve(strict=True)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def cache_path(
    category: str,
    key: str,
    suffix: str,
    *,
    namespace: str | None = None,
) -> Path:
    if category not in CACHE_CATEGORIES:
        raise ValueError(f"Unknown ALICE Lab cache category: {category}")
    if not key or any(character not in "0123456789abcdef" for character in key.lower()):
        raise ValueError("Cache keys must be non-empty hexadecimal strings")
    if not suffix.startswith(".") or "/" in suffix or "\\" in suffix:
        raise ValueError("Cache suffix must be a simple file extension")
    root = cache_root()
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise RuntimeError(f"Unsafe ALICE Lab cache root: {root}")
    directory = root / category
    if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
        raise RuntimeError(f"Unsafe cache category path: {directory}")
    if namespace is not None:
        if not namespace or not namespace.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Invalid cache namespace")
        directory /= namespace
        if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
            raise RuntimeError(f"Unsafe cache namespace path: {directory}")
    return directory / f"{key}{suffix}"


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(
        path,
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )


def _remove_tree_contents(directory: Path) -> tuple[int, int]:
    files = 0
    bytes_removed = 0
    if not directory.exists():
        return files, bytes_removed
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError(f"Unsafe cache category path: {directory}")
    for entry in os.scandir(directory):
        path = Path(entry.path)
        if entry.is_symlink():
            path.unlink()
            files += 1
        elif entry.is_dir(follow_symlinks=False):
            child_files, child_bytes = _remove_tree_contents(path)
            files += child_files
            bytes_removed += child_bytes
            path.rmdir()
        else:
            try:
                bytes_removed += entry.stat(follow_symlinks=False).st_size
            except FileNotFoundError:
                continue
            path.unlink()
            files += 1
    return files, bytes_removed


def cache_usage(category: str = "all") -> dict[str, dict[str, int]]:
    categories = CACHE_CATEGORIES if category == "all" else (category,)
    if any(item not in CACHE_CATEGORIES for item in categories):
        raise ValueError(f"Unknown ALICE Lab cache category: {category}")
    result: dict[str, dict[str, int]] = {}
    root = cache_root()
    for item in categories:
        directory = root / item
        files = size = 0
        if directory.exists():
            if directory.is_symlink() or not directory.is_dir():
                raise RuntimeError(f"Unsafe cache category path: {directory}")
            for current_root, directories, filenames in os.walk(directory, followlinks=False):
                directories[:] = [
                    name for name in directories if not (Path(current_root) / name).is_symlink()
                ]
                for name in filenames:
                    path = Path(current_root) / name
                    files += 1
                    if not path.is_symlink():
                        size += path.stat().st_size
        result[item] = {"files": files, "bytes": size}
    return result


def clear_cache(category: str = "all") -> dict[str, dict[str, int]]:
    """Clear only whitelisted descendants of the resolved ALICE cache root."""
    categories = CACHE_CATEGORIES if category == "all" else (category,)
    if any(item not in CACHE_CATEGORIES for item in categories):
        raise ValueError(f"Unknown ALICE Lab cache category: {category}")
    root = cache_root()
    if root.exists() and (root.is_symlink() or not root.is_dir()):
        raise RuntimeError(f"Unsafe ALICE Lab cache root: {root}")
    result: dict[str, dict[str, int]] = {}
    for item in categories:
        directory = root / item
        try:
            directory.resolve(strict=False).relative_to(root)
        except ValueError as error:
            raise RuntimeError("Cache deletion escaped the ALICE Lab cache root") from error
        files, size = _remove_tree_contents(directory)
        result[item] = {"files": files, "bytes": size}
    return result
