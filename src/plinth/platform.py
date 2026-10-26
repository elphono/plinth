"""Which kind of machine this runs on.

Three things depend on it, and nothing else: how a URL reaches the browser
(browser.py), how a terminal tab opens (terminal.py), and whether the WSL
distribution is needed.

| Kind    | Detected by                                             |
|---------|---------------------------------------------------------|
| `wsl`   | `WSL_DISTRO_NAME`, or "microsoft" in the kernel release |
| `macos` | `sys.platform == "darwin"` (not measured)               |
| `linux` | any other Linux                                         |
| `other` | anything else: no browser or terminal is guessed        |

`PLINTH_PLATFORM` overrides the detection: tests use it, and so can a user
whose machine is detected wrongly.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PLATFORMS = ("wsl", "linux", "macos", "other")
OVERRIDE = "PLINTH_PLATFORM"


class PlatformError(Exception):
    """Something about this machine could not be found out."""


def kernel_release() -> str:
    try:
        return Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except OSError:
        return ""


def platform() -> str:
    forced = os.environ.get(OVERRIDE, "").strip().lower()
    if forced in PLATFORMS:
        return forced
    if sys.platform == "darwin":
        return "macos"
    if not sys.platform.startswith("linux"):
        return "other"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "wsl" if "microsoft" in kernel_release().lower() else "linux"


def wsl_distro() -> str:
    """The WSL distribution this runs in: `WSL_DISTRO_NAME`, which systemd does
    not pass to a daemon, else the share name of `wslpath -w /`
    (`\\\\wsl.localhost\\<distro>\\`)."""
    name = os.environ.get("WSL_DISTRO_NAME", "").strip()
    if name:
        return name
    try:
        done = subprocess.run(["wslpath", "-w", "/"], capture_output=True, text=True,
                              timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PlatformError(f"cannot find the WSL distribution: {exc}") from exc
    parts = [p for p in done.stdout.strip().split("\\") if p]
    if done.returncode != 0 or len(parts) < 2:
        raise PlatformError(f"cannot find the WSL distribution from `wslpath -w /`: "
                            f"{done.stdout.strip()!r}")
    return parts[1]
