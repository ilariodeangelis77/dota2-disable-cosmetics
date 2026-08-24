"""Dota installation discovery, operation locking, and build history."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .constants import HISTORY_FORMAT_VERSION, HISTORY_KIND
from .domain import ProgressCallback
from .errors import GeneratorError
from .keyvalues import KVObject, TokenStream, obj_to_simple_dict, parse_value
from .reporting import write_json


def _steam_roots_windows() -> list[Path]:
    roots = [Path("C:/Program Files (x86)/Steam"), Path("C:/Program Files/Steam")]
    try:
        import winreg

        registry_locations = (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        )
        for hive, key_name, value_name in registry_locations:
            try:
                with winreg.OpenKey(hive, key_name) as key:
                    roots.append(Path(winreg.QueryValueEx(key, value_name)[0]))
            except OSError:
                pass
    except ImportError:
        pass
    return roots


def _steam_roots_posix(platform: str, home: Path) -> list[Path]:
    if platform == "darwin":
        return [home / "Library/Application Support/Steam"]
    return [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
    ]


def _dota_install_candidates(steam_roots: list[Path], *, windows_paths: bool) -> list[Path]:
    candidates: list[Path] = []
    for steam_root in steam_roots:
        candidates.append(steam_root / "steamapps/common/dota 2 beta")
        library_file = steam_root / "steamapps/libraryfolders.vdf"
        if not library_file.is_file():
            continue
        try:
            contents = library_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r'"path"\s+"([^"]+)"', contents):
            raw_path = match.group(1)
            if windows_paths:
                raw_path = raw_path.replace("\\\\", "\\")
            candidates.append(Path(raw_path).expanduser() / "steamapps/common/dota 2 beta")
    return candidates


def find_dota_install(explicit: Optional[str]) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if (candidate / "game/dota/pak01_dir.vpk").is_file():
            return candidate
        raise FileNotFoundError(
            f"Not a Dota 2 install (missing game/dota/pak01_dir.vpk): {candidate}"
        )

    if os.name == "nt":
        candidates = _dota_install_candidates(_steam_roots_windows(), windows_paths=True)
    else:
        candidates = _dota_install_candidates(
            _steam_roots_posix(sys.platform, Path.home()),
            windows_paths=False,
        )

    seen: set[Path] = set()
    for candidate in candidates:
        normalized = candidate.resolve()
        if normalized in seen:
            continue
        seen.add(normalized)
        if (normalized / "game/dota/pak01_dir.vpk").is_file():
            return normalized
    raise FileNotFoundError(
        "Could not auto-detect Dota 2. Pass --dota with the path to 'dota 2 beta'."
    )


@contextmanager
def dota_operation_lock(dota: Path) -> Iterator[None]:
    """Refuse overlapping build/clean operations for the same Dota installation."""

    identity = str(dota.resolve())
    if os.name == "nt":
        identity = identity.casefold()
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    lock_directory = Path(tempfile.gettempdir()) / "dota2-cosmetic-disabler-locks"
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_path = lock_directory / f"{digest}.lock"
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise GeneratorError(
            "Another build or cleanup is already using this Dota 2 installation. "
            "Wait for it to finish and try again."
        ) from exc
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def find_dota_appmanifest(dota: Path) -> Optional[Path]:
    """Find Steam's app manifest for Dota without searching outside its library."""

    common = dota.parent
    steamapps = common.parent
    if common.name.casefold() != "common" or steamapps.name.casefold() != "steamapps":
        return None
    manifest = steamapps / "appmanifest_570.acf"
    return manifest if manifest.is_file() else None


def parse_dota_appmanifest(path: Path) -> dict[str, str]:
    tokens = TokenStream(path.read_text(encoding="utf-8-sig", errors="replace"))
    root = tokens.next()
    value = parse_value(tokens)
    if root.casefold() != "appstate" or not isinstance(value, KVObject):
        raise ValueError(f"Unexpected Steam app manifest structure: {path}")
    fields = obj_to_simple_dict(value)
    if fields.get("appid") != "570":
        raise ValueError(f"Steam app manifest is not for Dota 2 (app 570): {path}")
    return fields


def capture_dota_version(dota: Path) -> dict:
    """Capture a comparable Dota identity without hashing the potentially large VPK."""

    pak = dota / "game/dota/pak01_dir.vpk"
    stat = pak.stat()
    captured = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "steam_app_id": "570",
        "steam_build_id": None,
        "steam_last_updated_unix": None,
        "steam_manifest_path": None,
        "pak01_dir": {
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "modified_at_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        },
    }
    manifest = find_dota_appmanifest(dota)
    if manifest is None:
        return captured
    captured["steam_manifest_path"] = str(manifest)
    try:
        fields = parse_dota_appmanifest(manifest)
    except (OSError, ValueError) as exc:
        captured["steam_manifest_error"] = str(exc)
        return captured
    captured["steam_build_id"] = fields.get("buildid") or None
    captured["steam_last_updated_unix"] = fields.get("LastUpdated") or None
    return captured


def dota_version_label(version: object) -> str:
    if not isinstance(version, dict):
        return "unrecorded"
    build_id = version.get("steam_build_id")
    if isinstance(build_id, str) and build_id:
        return f"Steam build {build_id}"
    pak = version.get("pak01_dir")
    if isinstance(pak, dict):
        size = pak.get("size_bytes")
        modified = pak.get("modified_at_utc")
        if isinstance(size, int) and isinstance(modified, str):
            return f"VPK stamp {size} bytes, {modified}"
    return "unknown Dota version"


def compare_dota_versions(recorded: object, current: object) -> tuple[str, str]:
    """Return ``(same|different|unknown, comparison basis)``."""

    if not isinstance(recorded, dict) or not isinstance(current, dict):
        return "unknown", "no comparable version record"
    recorded_build = recorded.get("steam_build_id")
    current_build = current.get("steam_build_id")
    if (
        isinstance(recorded_build, str)
        and recorded_build
        and isinstance(current_build, str)
        and current_build
    ):
        state = "same" if recorded_build == current_build else "different"
        return state, "Steam build ID"
    recorded_pak = recorded.get("pak01_dir")
    current_pak = current.get("pak01_dir")
    if isinstance(recorded_pak, dict) and isinstance(current_pak, dict):
        recorded_stamp = (recorded_pak.get("size_bytes"), recorded_pak.get("mtime_ns"))
        current_stamp = (current_pak.get("size_bytes"), current_pak.get("mtime_ns"))
        if all(isinstance(value, int) for value in (*recorded_stamp, *current_stamp)):
            state = "same" if recorded_stamp == current_stamp else "different"
            return state, "pak01_dir.vpk size and modification time"
    return "unknown", "no comparable Steam build ID or VPK stamp"


def dota_changed_during_build(initial: dict, latest: dict) -> bool:
    initial_build = initial.get("steam_build_id")
    latest_build = latest.get("steam_build_id")
    if (
        isinstance(initial_build, str)
        and initial_build
        and isinstance(latest_build, str)
        and latest_build
        and initial_build != latest_build
    ):
        return True
    initial_pak = initial.get("pak01_dir")
    latest_pak = latest.get("pak01_dir")
    if isinstance(initial_pak, dict) and isinstance(latest_pak, dict):
        initial_stamp = (initial_pak.get("size_bytes"), initial_pak.get("mtime_ns"))
        latest_stamp = (latest_pak.get("size_bytes"), latest_pak.get("mtime_ns"))
        if all(isinstance(value, int) for value in (*initial_stamp, *latest_stamp)):
            return initial_stamp != latest_stamp
    return False


def read_version_history(path: Path) -> dict:
    if not path.is_file():
        return {
            "kind": HISTORY_KIND,
            "format_version": HISTORY_FORMAT_VERSION,
            "entries": [],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeneratorError(f"Could not read Dota version history: {path}") from exc
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("kind") != HISTORY_KIND
        or payload.get("format_version") != HISTORY_FORMAT_VERSION
        or not isinstance(entries, list)
        or not all(isinstance(entry, dict) for entry in entries)
    ):
        raise GeneratorError(f"Invalid Dota version history: {path}")
    return payload


def append_version_history(path: Path, payload: dict, entry: dict) -> None:
    updated = dict(payload)
    updated["entries"] = [*payload["entries"], entry]
    write_json(path, updated)


def safely_append_version_history(
    path: Path,
    payload: Optional[dict],
    entry: dict,
    *,
    warning: ProgressCallback,
) -> bool:
    if payload is None:
        return False
    try:
        append_version_history(path, payload, entry)
    except Exception as exc:
        warning(f"WARNING: Overrides were built, but version history could not be updated: {exc}")
        return False
    return True


__all__ = [
    "append_version_history",
    "capture_dota_version",
    "compare_dota_versions",
    "dota_changed_during_build",
    "dota_operation_lock",
    "dota_version_label",
    "find_dota_appmanifest",
    "find_dota_install",
    "parse_dota_appmanifest",
    "read_version_history",
    "safely_append_version_history",
]
