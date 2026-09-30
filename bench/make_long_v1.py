"""Generate the long_v1 suite: each real-repo suite's five tasks chained into one 15-prompt session.

People often keep one agent thread for a day's work. Every request then re-sends the whole history,
so a long thread costs more than the same prompts split into a thread per task. This suite measures
that gap (and anything that closes it, like compaction or a handoff to a fresh thread): the prompts
and hidden tests are exactly realrepo_v1's (sympy) and realrepo_js_v1's (date-fns), in order.

    python -m bench.make_long_v1
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

BENCH = Path(__file__).resolve().parent
OUT = BENCH / "tasks_long_v1"
SOURCES = {"long_sympy": BENCH / "tasks_realrepo_v1", "long_datefns": BENCH / "tasks_realrepo_js_v1"}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for tid, src in SOURCES.items():
        tasks = [yaml.safe_load((d / "task.yaml").read_text("utf-8")) | {"dir": d}
                 for d in sorted(src.iterdir()) if (d / "task.yaml").is_file()]
        d = OUT / tid
        (d / "hidden").mkdir(parents=True)
        first = dict(tasks[0])
        skip = ("id", "category", "prompts", "requirements", "dir", "notes")
        meta = {k: v for k, v in first.items() if k not in skip}
        meta = {"id": tid, "category": "long_session", "prompts": [p for t in tasks for p in t["prompts"]],
                "requirements": {k: v for t in tasks for k, v in t["requirements"].items()}, **meta,
                "notes": f"{len(tasks)} tasks of {src.name} in one session ({', '.join(t['id'] for t in tasks)})"}
        if first.get("hidden_runner") == "vitest":
            imports: dict[str, set[str]] = {}
            bodies = []
            for t in tasks:
                text = (t["dir"] / "hidden" / "test.ts").read_text("utf-8")
                for names, mod in re.findall(r'^import \{([^}]*)\} from "([^"]+)";\s*$', text, re.M):
                    imports.setdefault(mod, set()).update(n.strip() for n in names.split(",") if n.strip())
                body = re.sub(r'^import \{[^}]*\} from "[^"]+";\s*$\n?', "", text, flags=re.M)
                bodies.append(f"// ---- {t['id']}\n{{\n{body.strip()}\n}}\n")
            head = "".join(f'import {{ {", ".join(sorted(v))} }} from "{m}";\n' for m, v in imports.items())
            (d / "hidden" / "test.ts").write_text(head + "\n" + "\n".join(bodies), encoding="utf-8", newline="\n")
        else:
            lines = []
            for t in tasks:
                mod = f"test_{t['id']}"
                shutil.copy2(t["dir"] / "hidden" / "test_hidden.py", d / "hidden" / f"{mod}.py")
                lines.append(f"from {mod} import *  # noqa: F403")
            (d / "hidden" / "test_hidden.py").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        # Solutions: the tasks touch different files, so their final states overlay cleanly.
        for t in tasks:
            if (t["dir"] / "solution").is_dir():
                shutil.copytree(t["dir"] / "solution", d / "solution", dirs_exist_ok=True)
        (d / "task.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, width=110), encoding="utf-8")
    print(f"wrote {len(SOURCES)} tasks to {OUT}")


if __name__ == "__main__":
    main()
