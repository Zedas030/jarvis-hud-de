"""
Which data leave the house in this installation? Read-only, no network.

    .venv\\Scripts\\python.exe scripts\\data_flows.py            # the list, with what is active here
    .venv\\Scripts\\python.exe scripts\\data_flows.py --write    # rewrite docs/datenfluesse.md

Settings come from .env (values are never printed).
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import paths  # noqa: E402,F401
import data_flows  # noqa: E402
import doctor  # noqa: E402


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if "--write" in argv:
        target = ROOT / "docs" / "datenfluesse.md"
        target.write_text(data_flows.markdown(), encoding="utf-8", newline="\n")
        print(f"geschrieben: {target}")
        return 0
    env = dict(doctor.read_env(ROOT / ".env"))
    env.update({k: v for k, v in os.environ.items() if k not in env})
    print(data_flows.report(env))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
