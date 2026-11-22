"""File selection for Theseus artifact workflows. Authentication stores are never evidence."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat


def reject_auth_store(path, selected=None):
    home = Path.home()
    roots = {home / ".codex", home / ".hermes", home / ".theseus"}
    for name in ("HERMES_HOME", "CODEX_HOME"):
        if os.environ.get(name):
            roots.add(Path(os.environ[name]))
    candidate = Path(path).resolve()
    if selected is None:
        try:
            selected = candidate.stat()
        except FileNotFoundError:
            pass
    for root in roots:
        protected_path = root / "auth.json"
        if candidate == protected_path.resolve():
            raise ValueError("authentication stores cannot be selected as artifacts")
        if selected is not None:
            try:
                protected = protected_path.stat()
            except FileNotFoundError:
                continue
            if os.path.samestat(selected, protected):
                raise ValueError("authentication stores cannot be selected as artifacts")


@contextmanager
def open_artifact(path, *, follow_links=True):
    flags = os.O_RDONLY | os.O_NONBLOCK
    if not follow_links:
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        selected = os.fstat(fd)
        if not stat.S_ISREG(selected.st_mode):
            raise ValueError("select a regular artifact file")
        reject_auth_store(path, selected)
        yield stream
