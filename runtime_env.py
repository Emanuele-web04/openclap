"""
FILE: runtime_env.py
Purpose: Detects whether ClapTrigger is running from source or from a bundled
macOS app so install/bootstrap code can choose the correct launch target.
Depends on: app_paths.py for shared app naming metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


# Allow both system-wide and per-user app installs when deciding whether the
# bundled app is in a stable location suitable for background LaunchAgents.
APPLICATIONS_DIRS = (Path("/Applications"), Path.home() / "Applications")


@dataclass(frozen=True)
class RuntimeEnvironment:
    """Resolved information about the current source or bundled runtime."""

    project_root: Path
    executable_path: Path
    bundle_path: Path | None
    frozen: bool

    @classmethod
    def current(cls, entry_file: str | Path) -> "RuntimeEnvironment":
        """Detects the active runtime using sys.executable and the entry file."""

        executable_path = Path(sys.executable).resolve()
        bundle_path = _detect_bundle_path(executable_path)
        if getattr(sys, "frozen", False):
            project_root = bundle_path.parent if bundle_path is not None else executable_path.parent
            return cls(
                project_root=project_root,
                executable_path=executable_path,
                bundle_path=bundle_path,
                frozen=True,
            )

        project_root = Path(entry_file).resolve().parent
        return cls(
            project_root=project_root,
            executable_path=executable_path,
            bundle_path=None,
            frozen=False,
        )

    @property
    def is_bundled_app(self) -> bool:
        """Returns True when the process is the executable inside a .app bundle."""

        return self.frozen and self.bundle_path is not None

    @property
    def working_directory(self) -> Path:
        """Returns a stable working directory appropriate for launchd."""

        if self.is_bundled_app and self.bundle_path is not None:
            return self.bundle_path.parent
        return self.project_root

    def is_installed_in_applications(self) -> bool:
        """Checks whether the .app lives inside Applications for stable autostart."""

        if self.bundle_path is None:
            return False
        resolved_bundle = self.bundle_path.resolve()
        return any(app_dir == resolved_bundle.parent or app_dir in resolved_bundle.parents for app_dir in APPLICATIONS_DIRS)


def _detect_bundle_path(executable_path: Path) -> Path | None:
    """Returns the parent .app bundle when the executable is inside one."""

    parent = executable_path.parent
    if parent.name != "MacOS":
        return None
    contents_dir = parent.parent
    if contents_dir.name != "Contents":
        return None
    bundle_path = contents_dir.parent
    if bundle_path.suffix != ".app":
        return None
    return bundle_path
