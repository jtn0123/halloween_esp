"""A sandboxed Radio never reads or writes the repo's own track library.

grade report 2026-09-24 B7: `import server` loaded tools/track_lib.py — which
binds TRACKS at import — through desktop_tools → rich_show → render_cues, a
line before radio_jobs set CASTLE_TRACKS. So track_lib.TRACKS was the repo's
tracks/ while the manifest beside it was sandboxed. radio_env.py is the fix
(the sandbox first, then tools/ on the path); these hold it there.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TOOLS = {p.stem for p in (ROOT / "tools").glob("*.py")}

#: Run in a fresh interpreter, as the launcher and the desktop app start the
#: server: import it, then report every module-level path that any loaded
#: tools/ module holds, plus the knobs the children will inherit.
PROBE = """
import json, os, sys
from pathlib import Path
import server
tools = Path(sys.argv[1])
paths = {}
for name, mod in list(sys.modules.items()):
    src = getattr(mod, "__file__", None)
    if not src or Path(src).parent != tools:
        continue
    for attr, value in vars(mod).items():
        if isinstance(value, Path):
            paths[f"{name}.{attr}"] = str(value)
knobs = {k: os.environ.get(k) for k in
         ("CASTLE_TRACKS", "CASTLE_SCENES", "CASTLE_BUILD", "CASTLE_HOST")}
print(json.dumps({"paths": paths, "knobs": knobs}))
"""


def first_tools_import(tree):
    """(line of the first import of a tools/ name, line of the first
    `radio_env` import) for one parsed module; None where there is none."""
    tools_at = env_at = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module.split(".")[0]]
        else:
            continue
        if "radio_env" in names and (env_at is None or node.lineno < env_at):
            env_at = node.lineno
        if TOOLS.intersection(names) and (tools_at is None or node.lineno < tools_at):
            tools_at = node.lineno
    return tools_at, env_at


class SandboxedServerTest(unittest.TestCase):
    def test_no_tools_module_holds_a_path_into_the_repos_library(self):
        real = [
            ROOT / "tracks",
            ROOT / "scenes",
            ROOT / "audio",
            ROOT / "firmware" / "generated",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "radio"
            env = {**os.environ, "CASTLE_RADIO_DATA": str(data)}
            # An operator's shell that pointed the knobs at the real show
            # must not leak through: the radio sets its own.
            env.update(CASTLE_TRACKS=str(ROOT / "tracks"), CASTLE_HOST="castle.lan")
            out = subprocess.run(
                [sys.executable, "-c", PROBE, str(ROOT / "tools")],
                cwd=HERE,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            got = json.loads(out.stdout.strip().splitlines()[-1])
        # The import that broke it must actually have happened, or the
        # check below is vacuous.
        self.assertIn("track_lib.TRACKS", got["paths"])
        self.assertEqual(got["paths"]["track_lib.TRACKS"], str(data / "tracks"))
        leaks = {
            name: path
            for name, path in got["paths"].items()
            if any(Path(path).is_relative_to(r) for r in real)
        }
        self.assertEqual(leaks, {}, "a tools/ module bound the repo's own show")
        self.assertEqual(
            got["knobs"],
            {
                "CASTLE_TRACKS": str(data / "tracks"),
                "CASTLE_SCENES": str(data / "scenes.yaml"),
                "CASTLE_BUILD": str(data / "build"),
                "CASTLE_HOST": "",
            },
        )


class OneDoorTest(unittest.TestCase):
    def test_every_module_that_imports_tools_imports_radio_env_first(self):
        """Import order is the bug, so the order is what is checked: in every
        radio module, radio_env comes before the first tools/ name, and no
        module opens a second door with its own sys.path.insert."""
        late, doors = [], []
        for path in sorted(HERE.glob("*.py")):
            if path.name == "radio_env.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "sys.path.insert" in text and path.name != Path(__file__).name:
                doors.append(path.name)
            tools_at, env_at = first_tools_import(ast.parse(text))
            if tools_at is not None and (env_at is None or env_at > tools_at):
                late.append(f"{path.name}:{tools_at}")
        self.assertEqual(late, [], "import radio_env before the first tools/ name")
        self.assertEqual(doors, [], "go through radio_env, not sys.path")


if __name__ == "__main__":
    unittest.main()
