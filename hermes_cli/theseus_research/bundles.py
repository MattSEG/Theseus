"""Publish an immutable group of local files together; validate any previous copy."""

import os
import shutil
import tempfile
from pathlib import Path
from hermes_constants import get_hermes_home


def publish(name, files):
    root = Path(get_hermes_home()) / "research" / "exports"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if Path(name).name != name or name in ("", ".", ".."):
        raise ValueError("Invalid bundle name.")
    for rel in files:
        if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ValueError("Invalid bundle member.")
    target = root / name

    def verify():
        existing = {
            p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()
        }
        if (
            target.is_symlink()
            or any(p.is_symlink() for p in target.rglob("*"))
            or existing != set(files)
            or any((target / k).read_bytes() != v for k, v in files.items())
        ):
            raise ValueError(
                "An existing export has changed. Move it aside before rebuilding."
            )

    if target.exists():
        verify()
        return target
    temp = Path(tempfile.mkdtemp(dir=root, prefix=".preparing-"))
    try:
        for name, body in files.items():
            path = temp / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("xb") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
        try:
            os.rename(temp, target)
        except OSError:
            if not target.is_dir():
                raise
            verify()
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return target
