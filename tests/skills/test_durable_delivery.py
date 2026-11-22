from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[2] / "skills/software-development/learning-run/scripts/deliver.py"


def test_process_death_after_link_recovers_without_replacing_output(tmp_path):
    env = {"HERMES_HOME": str(tmp_path / "profile")}
    source, dest = tmp_path / "source", tmp_path / "output"
    source.write_bytes(b"press proof")
    crash = """import os,runpy,sys,signal
link=os.link
def interrupted(*args):
    link(*args)
    os.kill(os.getpid(),signal.SIGKILL)
os.link=interrupted
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""
    ended = subprocess.run([sys.executable, "-c", crash, str(SCRIPT), "file", "proof", str(source), str(dest)], env=env)
    assert ended.returncode < 0 and dest.read_bytes() == source.read_bytes()
    listing = json.loads(subprocess.check_output([sys.executable, str(SCRIPT), "list"], env=env))
    assert listing == [{"job": "proof", "path": str(dest), "acknowledged": False}]
    stage = list(tmp_path.glob(".theseus-delivery-*.part"))
    assert len(stage) == 1
    def inspect():
        return json.loads(subprocess.check_output([sys.executable, str(SCRIPT), "inspect", "proof"], env=env))
    state = inspect()
    assert state["destination"] == "matching" and not state["acknowledged"]
    assert state["observed_sha256"] == state["expected_sha256"] and stage[0].exists()
    ino = dest.stat().st_ino
    source.unlink()
    def resume(_):
        return json.loads(subprocess.check_output([sys.executable, str(SCRIPT), "resume", "proof"], env=env))
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert {r["state"] for r in pool.map(resume, range(12))} == {"verified"}
    assert dest.stat().st_ino == ino and not list(tmp_path.glob("*.part"))
    assert inspect()["acknowledged"]
    dest.write_bytes(b"operator edit")
    assert inspect()["destination"] == "conflict"
    retry = subprocess.run([sys.executable, str(SCRIPT), "resume", "proof"], env=env, capture_output=True)
    assert retry.returncode == 2 and json.loads(retry.stdout)["state"] == "conflict"
    assert dest.read_bytes() == b"operator edit"
    dest.unlink()
    retry = subprocess.run([sys.executable, str(SCRIPT), "resume", "proof"], env=env, capture_output=True)
    assert retry.returncode == 2 and json.loads(retry.stdout)["state"] == "missing" and not dest.exists()


def test_admitted_snapshot_survives_source_change_and_existing_file(tmp_path):
    env = {"HERMES_HOME": str(tmp_path / "profile")}
    src, other, out = [tmp_path / x for x in ["source", "other", "out"]]
    src.write_bytes(b"approved")
    other.write_bytes(b"protected")
    out.symlink_to(other)
    args = [sys.executable, str(SCRIPT), "file", "edition", str(src), str(out)]
    assert subprocess.run(args, env=env, capture_output=True).returncode != 0
    assert other.read_bytes() == b"protected"
    out.unlink()
    src.write_bytes(b"new revision")
    assert subprocess.run(args, env=env, capture_output=True).returncode != 0
    subprocess.run([sys.executable, str(SCRIPT), "resume", "edition"], env=env, check=True)
    assert out.read_bytes() == b"approved"


def test_authentication_file_is_never_admitted_or_published(tmp_path):
    import sqlite3
    profile = tmp_path / "trial"
    profile.mkdir()
    auth = profile / "auth.json"
    auth.write_text("harmless fixture")
    alias = tmp_path / "selected.txt"
    os.link(auth, alias)
    output = tmp_path / "output.txt"
    env = {"HOME": str(tmp_path), "HERMES_HOME": str(profile)}
    for source in (auth, alias):
        result = subprocess.run([sys.executable, str(SCRIPT), "file", "fixture", str(source), str(output)],
                                env=env, text=True, capture_output=True)
        assert result.returncode != 0 and "authentication stores" in result.stderr
    assert not output.exists()
    with sqlite3.connect(profile / "deliveries/jobs.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
    auth.unlink()
    result = subprocess.run([sys.executable, str(SCRIPT), "file", "fixture", str(alias), str(auth)],
                            env=env, text=True, capture_output=True)
    assert result.returncode != 0 and not auth.exists()

def test_inspection_does_not_publish_an_admitted_job(tmp_path):
    profile = tmp_path / "profile with spaces"
    env = {"HERMES_HOME": str(profile)}
    cmd = [sys.executable, str(SCRIPT)]
    assert json.loads(subprocess.check_output([*cmd, "list"], env=env)) == []
    assert not profile.exists()
    source, dest = tmp_path / "input", tmp_path / "output"
    source.write_bytes(b"fresh print")
    crash = """import os,runpy,sys
os.link=lambda *args: os._exit(17)
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""
    dead = subprocess.run([sys.executable, "-c", crash, str(SCRIPT), "file", "fresh", str(source), str(dest)], env=env)
    assert dead.returncode == 17 and not dest.exists()
    key = json.loads(subprocess.check_output([*cmd, "list"], env=env))[0]["job"]
    stage, = tmp_path.glob(".theseus-delivery-*.part")
    before = stage.stat()
    for _ in range(2):
        receipt = json.loads(subprocess.check_output([*cmd, "inspect", key], env=env))
        assert receipt["destination"] == "missing" and not receipt["acknowledged"]
    assert not dest.exists() and stage.stat().st_ino == before.st_ino and stage.stat().st_mtime_ns == before.st_mtime_ns
    subprocess.run([*cmd, "resume", key], env=env, check=True)
    assert dest.read_bytes() == source.read_bytes() and not stage.exists()
