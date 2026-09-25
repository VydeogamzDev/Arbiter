"""Windows packaged-app (MSIX) awareness.

Processes started by a packaged app (for example the Claude desktop app, whose Code tab runs Claude
Code and its hooks and MCP servers) inherit its package identity, and Windows silently redirects
their writes under ``%LOCALAPPDATA%`` / ``%APPDATA%`` into the package's private
``...\\Packages\\<name>\\LocalCache``. Processes outside the package, like Arbiter's daemon launched
through WMI or the real client apps, never see those files.

Consequences Arbiter handles:
- its home defaults to ``%USERPROFILE%\\.arbiter`` on Windows (not virtualized), so the CLI, hooks,
  MCP shims and the daemon always share one set of files;
- setup refuses to edit client configs under AppData while packaged (the real client would read
  the unmodified file), and says to run it from a regular terminal instead.
"""

from __future__ import annotations

import functools
import os
import sys
from pathlib import Path


def package_name() -> str | None:
    r"""The package whose file virtualization applies to this process, or None.

    Identity APIs aren't enough: helper processes a packaged app starts (shells, hooks, MCP servers)
    may have no package identity yet still get redirected writes. So this probes: write a marker
    under ``%LOCALAPPDATA%`` and look for it inside ``Packages\*\LocalCache\Local``.
    """
    if sys.platform != "win32":
        return None
    forced = os.environ.get("ARBITER_TEST_PACKAGED")          # tests only
    if forced is not None:
        return forced or None
    return _probe()


@functools.lru_cache(maxsize=1)
def _probe() -> str | None:
    import secrets

    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    name = f".arbiter-vfs-probe-{os.getpid()}-{secrets.token_hex(4)}"
    marker = local / name
    try:
        marker.write_text("probe", encoding="utf-8")
    except OSError:
        return None
    try:
        for pkg in (local / "Packages").glob("*"):
            if (pkg / "LocalCache" / "Local" / name).exists():
                return pkg.name
        return None
    finally:
        try:
            marker.unlink()
        except OSError:
            pass


def _appdata_roots() -> list[tuple[Path, str]]:
    home = Path.home()
    return [(Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local").resolve(), "Local"),
            (Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming").resolve(), "Roaming")]


@functools.lru_cache(maxsize=64)
def _dir_redirected(directory: str, package: str) -> bool:
    """Probe one directory: does a file written there land in the package's private copy?"""
    import secrets

    d = Path(directory)
    for root, kind in _appdata_roots():
        if d == root or root in d.parents:
            rel = d.relative_to(root)
            local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
            shadow = local / "Packages" / package / "LocalCache" / kind / rel
            name = f".arbiter-vfs-probe-{os.getpid()}-{secrets.token_hex(4)}"
            try:
                (d / name).write_text("probe", encoding="utf-8")
            except OSError:
                return True           # can't even write there: treat as unsafe
            try:
                return (shadow / name).exists()
            finally:
                try:
                    (d / name).unlink()
                except OSError:
                    pass
    return False


def virtualized(path: Path) -> bool:
    r"""True when this process is packaged and writes to ``path`` would be redirected. Probed per
    directory: not everything under AppData is redirected (``AppData\Local\Temp`` isn't)."""
    pkg = package_name()
    if pkg is None:
        return False
    try:
        p = path.expanduser().resolve()
    except OSError:
        p = path
    d = p.parent
    while not d.exists() and d != d.parent:
        d = d.parent
    if not any(d == r or r in d.parents for r, _ in _appdata_roots()):
        return False
    return _dir_redirected(str(d), pkg)
