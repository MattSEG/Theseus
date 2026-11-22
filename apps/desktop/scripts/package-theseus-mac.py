"""Package the working Mini installation using the pinned Electron and Python builds."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib

app = Path(__file__).resolve().parents[1]
root = app.parents[1]
profile = Path.home() / ".theseus"
dest = Path.home() / "Applications/Theseus.app"
if dest.exists():
    raise SystemExit(f"App already exists: {dest}")
# MCP discovery in the packaged gateway needs the client SDK inside its copied runtime.
subprocess.run([root / ".venv/bin/python", "-c", "from mcp import ClientSession, StdioServerParameters; from mcp.client.stdio import stdio_client"],
               cwd=root, check=True)
env = {**os.environ, "THESEUS_PACKAGE": "1", "VITE_THESEUS_DESKTOP": "1",
       "VITE_THESEUS_LEARNING_DIR": str(profile / "learning-runs"),
       "PATH": f"{profile}/toolchain/node_modules/.bin:" + os.environ["PATH"]}
subprocess.run(["node", "scripts/build.mjs"], cwd=app, env=env, check=True)
dest.parent.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=dest.parent) as temp:
    stage = Path(temp)
    project = stage / "project"
    project.mkdir()
    (project / "package.json").write_text(json.dumps(dict(name="theseus", version="0.1.0",
        description="Theseus local agent desktop", author="Theseus", main="launch.mjs")))
    (project / "launch.mjs").write_text("""import path from 'node:path'
import os from 'node:os'
const profile = path.join(os.homedir(), '.theseus')
const resources = process.resourcesPath
Object.assign(process.env, {
  HERMES_HOME: profile, HERMES_RUNTIME_DIR: path.join(profile, 'runtime'),
  HERMES_DESKTOP_USER_DATA_DIR: path.join(profile, 'desktop-packaged'),
  HERMES_DESKTOP_PYTHON: path.join(resources, 'python/bin/python3'),
  HERMES_DESKTOP_HERMES_ROOT: path.join(resources, 'backend'),
  HERMES_DESKTOP_APP_NAME: 'Theseus', HERMES_DESKTOP_CDP_PORT: 'off',
  PYTHONDONTWRITEBYTECODE: '1', VITE_THESEUS_DESKTOP: '1'
})
delete process.env.HERMES_DESKTOP_DEV_SERVER
await import('./dist/electron-main.mjs')
""")
    config = dict(appId="local.theseus.desktop", productName="Theseus", asar=False,
        electronDist=str((app / "node_modules/electron/dist").resolve()),
        electronVersion=json.loads((app / "package.json").read_text())["devDependencies"]["electron"],
        mac=dict(icon=str(app / "assets/theseus/ship.icns")),
        directories=dict(output=str(stage / "out")), protocols=[dict(name="Theseus", schemes=["theseus"])])
    cfg = stage / "builder.json"
    cfg.write_text(json.dumps(config))
    subprocess.run([app/"node_modules/.bin/electron-builder","--projectDir",project,"--config",cfg,
                    "--mac","dir","--arm64","--publish","never"], cwd=app, env=env, check=True)
    bundle, = (stage / "out").glob("*/Theseus.app")
    resources = bundle / "Contents/Resources"
    shutil.copytree(app / "dist", resources / "app/dist", symlinks=True)
    shutil.copy2(app / "build/install-stamp.json", resources / "install-stamp.json")
    # Materialize only application source and resources from the working tree.
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    packages = set(metadata["tool"]["setuptools"]["packages"]["find"]["include"])
    dirs = packages | {"skills", "locales", "assets", "tui_gateway", "session_rules", "scripts", "evals"}
    backend = resources / "backend"
    backend.mkdir()
    paths = subprocess.check_output(["git","ls-files","-co","--exclude-standard","-z"], cwd=root).split(b'\0')
    for raw in filter(None, paths):
        rel = Path(os.fsdecode(raw))
        if rel.parts[0] not in dirs and not (len(rel.parts)==1 and
            (rel.suffix==".py" or rel.name in {"pyproject.toml","LICENSE","SOUL.md","cli-config.yaml.example"})):
            continue
        if (root / rel).is_file():
            target = backend / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, target)
    interpreter = (root / ".venv/bin/python").resolve()
    python = resources / "python"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "__editable__*", "direct_url.json")
    shutil.copytree(interpreter.parents[1], python, symlinks=True, ignore=ignore)
    site, = (root / ".venv/lib").glob("python*/site-packages")
    local_site = python / "lib" / site.parent.name / "site-packages"
    shutil.copytree(site, local_site, dirs_exist_ok=True, symlinks=True, ignore=ignore)
    (local_site / "theseus-runtime.pth").write_text("../../../../backend\n")
    subprocess.run(["codesign","--force","--deep","--sign","-",bundle], check=True)
    subprocess.run(["codesign","--verify","--deep","--strict",bundle], check=True)
    # Exercise the shipped interpreter without user-site or checkout packages.
    subprocess.run(
        [python / "bin/python3", "-I", "-B", "-c",
         "from pathlib import Path; import mcp, sys; "
         "from mcp import ClientSession, StdioServerParameters; "
         "from mcp.client.stdio import stdio_client; "
         "sys.exit(0 if Path(mcp.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve()) else 1)",
         python],
        cwd=resources, check=True,
    )
    bundle.rename(dest)
print(dest)
