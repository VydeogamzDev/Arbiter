"""M6: repository indexes. Exit: no stale results after edits or branch switches, secrets never
indexed, index version recorded on every retrieval."""

from __future__ import annotations

import os
import secrets
import subprocess
import time
from pathlib import Path

import pytest

from arbiter_agent.config.loader import build_config
from arbiter_agent.eval.trace_replay import git_init
from arbiter_agent.retrieval.access_policy import AccessPolicy
from arbiter_agent.retrieval.service import RetrievalService
from arbiter_agent.state.store import connect

SECRET = "sk-ant-api03-" + "RetrievalSecretValue9" * 3
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=GIT_ENV).stdout


@pytest.fixture
def svc(tmp_path):
    s = RetrievalService(tmp_path / "data", secrets.token_bytes(32), build_config({})).start()
    yield s
    s.stop()


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def paths(out: dict) -> set[str]:
    return {h["path"] for h in out["hits"]}


# ------------------------------------------------------------------ access policy
@pytest.mark.parametrize(("rel", "denied"), [
    (".env", True), ("config/.env.production", True), (".env.example", False), ("id_rsa", True),
    (".aws/credentials", True), ("certs/server.pem", True), ("secrets.yaml", True), ("infra/prod.tfvars", True),
    ("node_modules/lib/index.js", True), ("package-lock.json", True), ("dist/app.min.js", True),
    ("assets/logo.png", True), ("src/app.py", False), ("docs/credentials-guide.md", False),
])
def test_access_policy_paths(tmp_path, rel, denied):
    assert (AccessPolicy.for_repo(tmp_path).path_reason(rel) is not None) == denied, rel


def test_access_policy_arbiterignore_and_content(tmp_path):
    (tmp_path / ".arbiterignore").write_text("private/\n*.secret.txt\n")
    pol = AccessPolicy.for_repo(tmp_path)
    assert pol.path_reason("private/notes.md") == ".arbiterignore"
    assert pol.path_reason("a/b/c.secret.txt") == ".arbiterignore"
    assert pol.content_reason(b"abc\x00def") == "binary content"
    assert pol.content_reason(b"x" * (2 * 1024 * 1024)) == "over size cap"


# ------------------------------------------------------------------ freshness
def test_no_stale_results_after_edits_and_deletes(svc, tmp_path):
    repo = tmp_path / "repo"
    write(repo, "src/calc.py", "def add(a, b):\n    return a + b  # alpha_marker\n")
    assert paths(svc.search(str(repo), "alpha_marker")) == {"src/calc.py"}
    write(repo, "src/calc.py", "def add(a, b):\n    return b + a  # beta_marker\n")   # same second, same size
    assert paths(svc.search(str(repo), "alpha_marker")) == set()
    assert paths(svc.search(str(repo), "beta_marker")) == {"src/calc.py"}
    (repo / "src/calc.py").unlink()
    assert paths(svc.search(str(repo), "beta_marker")) == set()


def test_budget_cut_drops_changed_files_instead_of_serving_stale(svc, tmp_path):
    repo = tmp_path / "repo"
    write(repo, "a.py", "old_content_token = 1\n")
    svc.search(str(repo), "old_content_token")
    write(repo, "a.py", "new_content_token = 2\n")
    ident, idx, policy, res = svc.prepare(str(repo), budget_s=0.0)
    assert res.pending >= 1
    assert idx.search(ident.root, "old_content_token", policy=policy) == []   # not stale, just pending
    assert paths(svc.search(str(repo), "new_content_token")) == {"a.py"}       # next refresh catches up


@pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0, reason="needs git")
def test_branch_switch_and_worktree_views(svc, tmp_path):
    repo = tmp_path / "repo"
    write(repo, "app.py", "def handler():\n    return 'main_only_token'\n")
    assert git_init(repo)
    git(repo, "branch", "-M", "main")
    git(repo, "checkout", "-q", "-b", "feature")
    write(repo, "app.py", "def handler():\n    return 'feature_only_token'\n")
    git(repo, "commit", "-qam", "feature")
    out = svc.search(str(repo), "feature_only_token")
    assert paths(out) == {"app.py"}
    gen_feature = out["index"]["generation"]
    git(repo, "checkout", "-q", "main")
    time.sleep(0.05)
    out = svc.search(str(repo), "main_only_token")
    assert paths(out) == {"app.py"} and paths(svc.search(str(repo), "feature_only_token")) == set()
    assert out["index"]["generation"] > gen_feature and out["index"]["head"] == git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "feature")                                   # switching back reuses analysis
    _, _, _, res = svc.prepare(str(repo))
    assert res.analyzed == 0 and res.changed >= 1
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "main")
    assert paths(svc.search(str(wt), "main_only_token")) == {"app.py"}         # separate view, same database
    assert paths(svc.search(str(repo), "feature_only_token")) == {"app.py"}
    assert svc.index_for(svc.prepare(str(wt))[0]) is svc.index_for(svc.prepare(str(repo))[0])


# ------------------------------------------------------------------ secrets
def test_secrets_never_indexed(svc, tmp_path):
    repo = tmp_path / "repo"
    write(repo, ".env", f"ANTHROPIC_API_KEY={SECRET}\n")
    write(repo, "config.py", f'API_KEY = "{SECRET}"\nTIMEOUT = 30\n')
    write(repo, "keys/deploy.pem", "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
    out = svc.search(str(repo), "API_KEY")
    assert paths(out) == {"config.py"}
    assert SECRET not in str(out) and "[REDACTED:" in str(out)
    assert paths(svc.search(str(repo), SECRET)) == set()
    assert paths(svc.search(str(repo), "ANTHROPIC_API_KEY")) == set()          # .env never read
    assert paths(svc.search(str(repo), "PRIVATE KEY")) == set()
    svc.stop()
    blob = b"".join(p.read_bytes() for p in (tmp_path / "data" / "indexes").glob("*") if p.is_file())
    assert SECRET.encode() not in blob


# ------------------------------------------------------------------ structure
def test_symbols_imports_and_tests(svc, tmp_path):
    repo = tmp_path / "repo"
    write(repo, "pkg/__init__.py", "")
    write(repo, "pkg/core.py", "class Engine:\n    def run(self, job):\n        return job\n\n\ndef make():\n"
                               "    return Engine()\n")
    write(repo, "pkg/cli.py", "from pkg.core import make\n\n\ndef main():\n    make().run(1)\n")
    write(repo, "tests/test_core.py",
          "from pkg.core import Engine\n\n\ndef test_run():\n    assert Engine().run(2) == 2\n")
    write(repo, "web/util.ts", "export function slugify(s: string): string { return s }\n")
    write(repo, "web/page.ts", "import { slugify } from './util'\nexport const title = slugify('x')\n")
    sym = svc.symbol(str(repo), "make")
    assert [(d["path"], d["line"], d["kind"]) for d in sym["definitions"]] == [("pkg/core.py", 6, "function")]
    assert any(r["path"] == "pkg/cli.py" for r in sym["references"])
    assert "pkg/cli.py" in sym["importers"]
    rel = svc.related(str(repo), "pkg/core.py")
    assert rel["importers"] == ["pkg/cli.py", "tests/test_core.py"] and rel["tests"] == ["tests/test_core.py"]
    assert svc.related(str(repo), "web/page.ts")["imports"] == ["web/util.ts"]
    assert svc.symbol(str(repo), "slugify")["definitions"][0]["path"] == "web/util.ts"
    assert "error" in svc.related(str(repo), ".env")


# ------------------------------------------------------------------ audit + MCP rendering via the daemon
def test_retrieval_records_index_version(home, tmp_path):
    from arbiter_agent.daemon.server import Daemon
    from arbiter_agent.shims.mcp_server import render_retrieval

    repo = tmp_path / "repo"
    write(repo, "calc.py", "def add(a, b):\n    return a + b\n")
    d = Daemon(home).start()
    try:
        d.ingest_hook("claude_code", {"hook_event_name": "UserPromptSubmit", "session_id": "r1", "cwd": str(repo),
                                      "prompt": "look at add"}, surface="http")
        d.engine.drain(10)
        out = d.dispatch("retrieve", {"op": "search", "query": "add", "session_id": "r1"})
        assert out["index"]["version"] >= 1 and out["hits"][0]["path"] == "calc.py"
        text = render_retrieval("arbiter_search", out)
        assert "calc.py:1" in text and "[index v" in text
        d.engine.drain(10)
        rc = connect(home.db, readonly=True)
        try:
            n = rc.execute("SELECT COUNT(*) FROM event_log WHERE event_type = 'internal.retrieval' "
                           "AND json_extract(attrs_json, '$.index_version') >= 1").fetchone()[0]
        finally:
            rc.close()
        assert n == 1
    finally:
        d.shutdown()
