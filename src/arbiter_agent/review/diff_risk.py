"""Deterministic diff risk (spec §13.2, §13.4).

Each changed file gets risk tags from its path and its changed lines, and a level:

- ``critical``: a deterministic tag whose verification can never be skipped by a semantic score
  (destructive schema/data changes, removed security checks, deploy pipelines);
- ``high``: the configured review floors (security, migrations, destructive, public API, test
  harness) plus wire formats;
- ``medium``: concurrency, error handling, dependencies/build, runtime config, generator changes,
  ordinary code;
- ``low``: docs, comments, test additions.

Blast radius (``blast_radius.py``) can raise a file one level (never to critical): a tiny change in
a central module outranks hundreds of low-consequence lines. Tags are floors: nothing lowers them.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

LEVELS = ("low", "medium", "high", "critical")


def level_index(level: str) -> int:
    return LEVELS.index(level)


@dataclass
class FileChange:
    path: str
    status: str = "M"                    # A | M | D | R
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    old_path: str | None = None


@dataclass
class FileRisk:
    path: str
    level: str
    tags: list[str]
    reasons: list[str]
    added: int = 0
    removed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ unified diff parsing
_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def parse_unified_diff(text: str) -> list[FileChange]:
    """Parse ``git diff`` output (any context size) into per-file added/removed lines."""
    changes: list[FileChange] = []
    cur: FileChange | None = None
    for line in text.splitlines():
        m = _DIFF_HEADER.match(line)
        if m:
            cur = FileChange(path=m.group(2), old_path=m.group(1) if m.group(1) != m.group(2) else None)
            if cur.old_path:
                cur.status = "R"
            changes.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("new file mode"):
            cur.status = "A"
        elif line.startswith("deleted file mode"):
            cur.status = "D"
        elif line.startswith(("+++", "---", "@@", "index ", "similarity ", "rename ")):
            continue
        elif line.startswith("+"):
            cur.added.append(line[1:])
        elif line.startswith("-"):
            cur.removed.append(line[1:])
    return changes


# ------------------------------------------------------------------ tag rules
def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.IGNORECASE)


PATH_TAGS: list[tuple[str, re.Pattern[str]]] = [
    ("docs", _rx(r"\.(md|rst|adoc|txt)$", r"(^|/)docs?/", r"(^|/)(README|CHANGELOG|LICENSE)[^/]*$")),
    ("security", _rx(r"(^|/|_)(auth|authn|authz|security|crypto|permissions?|acl|oauth|jwt|login|password|"
                     r"secrets?|sessions?|csrf|sanitiz\w*|rbac|policy|policies)(/|_|\.|$)")),
    ("persistence", _rx(r"(^|/)migrations?/", r"(^|/)alembic/", r"\.sql$", r"(^|/)schema\.(rb|prisma|py|ts)$",
                        r"(^|/)models?\.py$", r"(^|/)db/")),
    ("wire_format", _rx(r"\.(proto|avsc|thrift|graphql|gql)$", r"openapi\.(ya?ml|json)$", r"swagger\.",
                        r"\.schema\.json$")),
    ("public_api", _rx(r"(^|/)__init__\.py$", r"(^|/)(index|mod|lib)\.(ts|js|rs)$", r"(^|/)api/",
                       r"(^|/)public/", r"\.d\.ts$")),
    ("deps_build", _rx(r"(^|/)(pyproject\.toml|setup\.py|setup\.cfg|requirements[^/]*\.txt|package\.json|"
                       r"Cargo\.toml|go\.mod|go\.sum|Gemfile|pom\.xml|build\.gradle(\.kts)?|Makefile|"
                       r"CMakeLists\.txt|Dockerfile|tsconfig[^/]*\.json|uv\.lock|poetry\.lock|"
                       r"package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock)$")),
    ("ci_deploy", _rx(r"(^|/)\.github/workflows/", r"(^|/)\.gitlab-ci\.yml$", r"(^|/)deploy/",
                      r"(^|/)(k8s|helm|terraform|infra)/", r"\.tf$", r"(^|/)Procfile$", r"(^|/)fly\.toml$")),
    ("config_runtime", _rx(r"(^|/)(config|settings|conf)/", r"\.(ini|cfg|env\.example)$",
                           r"(^|/)(settings|config)\.(py|ya?ml|toml|json)$")),
    ("test_harness", _rx(r"(^|/)conftest\.py$", r"(^|/)fixtures?/", r"(^|/)__snapshots__/", r"\.snap$",
                         r"(^|/)(pytest\.ini|tox\.ini|jest\.config\.\w+|vitest\.config\.\w+|karma\.conf\.\w+|"
                         r"\.coveragerc)$")),
    ("generated", _rx(r"(^|/)(generated|gen|__generated__)/", r"_pb2(_grpc)?\.py$", r"\.pb\.go$",
                      r"\.min\.(js|css)$", r"\.g\.dart$")),
    ("tests", _rx(r"(^|/)tests?/", r"(^|/)test_[^/]+\.py$", r"_test\.(py|go)$", r"\.(test|spec)\.[jt]sx?$")),
]

LINE_TAGS: list[tuple[str, re.Pattern[str]]] = [
    ("security", _rx(r"\b(password|passwd|secret|api[_-]?key|token|credential)s?\b", r"\bhmac\b",
                     r"verify_signature|check_password|is_authenticated|authorize|permission_required|"
                     r"login_required|csrf", r"\b(encrypt|decrypt|hash_password|bcrypt|jwt)\b",
                     r"shell\s*=\s*True", r"\beval\(", r"\bexec\(", r"\bpickle\.loads?\(", r"verify\s*=\s*False")),
    # injection sinks: shell/OS command execution and queries built from strings
    ("security", _rx(r"\bos\.(system|popen)\(", r"\bsubprocess\.\w+\(.*(\+|%|\bformat\(|f['\"])",
                     r"\bchild_process\.exec(Sync)?\(", r"\bRuntime\.getRuntime\(\)\.exec\(",
                     r"\.(execute|executemany|raw|query)\(\s*f['\"]",
                     r"\.(execute|executemany|raw|query)\([^)]*(\+|%\s|\.format\()",
                     r"\binnerHTML\s*=", r"dangerouslySetInnerHTML", r"\bmark_safe\(|\|\s*safe\b")),
    # request data reaching network, filesystem, templates or dynamic dispatch (SSRF, traversal, XSS, RCE)
    ("security", _rx(r"\b(requests|httpx|urllib\.request|fetch|axios)\.?\w*\(\s*[^)]*\b(request|req|params|args|"
                     r"query|body)\b",
                     r"\b(open|send_file|send_from_directory|readFile\w*|os\.path\.join)\([^)]*\b(request|req|params)\b",
                     r"\bautoescape\s*=\s*False\b", r"\|\s*safe\b", r"\{\{\{",
                     r"\.(send|public_send|constantize)\(\s*params\b", r"\bgetattr\([^)]*\b(request|params)\b",
                     r"\bProcess\.Start\(", r"\bexecuteQuery\(|\bexecuteUpdate\(|\bprepareStatement\([^)]*\+",
                     r"\bredis\w*\.flush(all|db)\(|\.flush(all|db)\(\)")),
    # insecure cookie/session flags
    ("security", _rx(r"\bhttp_?only\s*[=:]\s*(False|false)\b", r"\bsecure\s*[=:]\s*(False|false)\b",
                     r"\bsame_?site\s*[=:]\s*['\"]?None\b")),
    # SQL assembled from strings (any language), even when it's executed on a later line
    ("security", _rx(r"(Sprintf|\.format\(|%\s*\(|\+\s*\w|\$\{|f['\"]).*\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b"
                     r"|\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b[^'\"]*['\"]\s*(\+|%|\.format\()")),
    # unsafe deserialization
    ("security", _rx(r"\byaml\.load\((?![^)]*SafeLoader)", r"\bmarshal\.loads?\(", r"\bjsonpickle\.decode\(",
                     r"\bObjectInputStream\b", r"\bunserialize\(")),
    # permissive security configuration
    ("security", _rx(r"allow_origins\s*=\s*\[\s*['\"]\*", r"Access-Control-Allow-Origin['\"]?\s*[:,]\s*['\"]\*",
                     r"\bALLOWED_HOSTS\s*=\s*\[\s*['\"]\*", r"\bDEBUG\s*=\s*True\b", r"_SECURE\s*=\s*False\b",
                     r"\bcsrf_exempt\b", r"X-Frame-Options", r"\bpermissions:\s*write-all\b")),
    # tests switched off
    ("test_harness", _rx(r"@pytest\.mark\.(skip|xfail)\b", r"@unittest\.skip", r"\b(it|test|describe)\.skip\(",
                         r"\bx(it|describe)\(", r"\bt\.Skip\(", r"#\[ignore\]", r"\bpytest\.skip\(")),
    # bulk deletion
    ("destructive", _rx(r"\bdelete_many\(|\bdeleteMany\(", r"\.objects\.(all|filter)\([^)]*\)\.delete\(",
                        r"\bdestroy_all\b|\bdelete_all\b|\bremove_all\b", r"\bdrop_collection\(|\.drop\(\)")),
    ("persistence", _rx(r"\b(ALTER|CREATE)\s+TABLE\b", r"\bADD\s+COLUMN\b", r"\bCREATE\s+(UNIQUE\s+)?INDEX\b",
                        r"op\.(add|drop|alter)_column", r"\bmigrat(e|ion)\b")),
    ("destructive", _rx(r"\bDROP\s+(TABLE|COLUMN|DATABASE|INDEX)\b", r"\bTRUNCATE\b", r"\bDELETE\s+FROM\b",
                        r"rm\s+-rf", r"shutil\.rmtree", r"os\.remove\(", r"\.unlink\(", r"op\.drop_",
                        r"\bremove_column\b|\bdrop_table\b|\bRemoveField\(|\bDeleteModel\(|\bdropColumn\(|"
                        r"\bdropTable\(|\bdropIfExists\(",
                        r"force[_-]?push|--force\b", r"\bpurge\b")),
    ("concurrency", _rx(r"\bthreading\b", r"\bLock\(|\bRLock\(|\bSemaphore\(", r"\basync\s+def\b",
                        r"\bawait\b", r"\basyncio\b", r"\bMutex\b", r"\bgo\s+func\b", r"\bchan\s", r"\batomic\b",
                        r"\bsynchronized\b", r"\bconcurrent\.futures\b")),
    ("error_handling", _rx(r"^\s*except\b", r"\bcatch\s*\(", r"^\s*raise\b", r"\bthrow\b", r"\bpanic!?\(",
                           r"if\s+err\s*!=\s*nil", r"\.unwrap\(\)", r"\bfinally\b")),
]

SECURITY_PATH = dict(PATH_TAGS)["security"]
CRITICAL_REASONS = {
    "destructive_persistence": "destructive schema or data change (drop/truncate/delete) in persistence code",
    "security_check_removed": "a security check (guard, raise, verify or permission call) was removed",
    "ci_deploy": "deployment pipeline or infrastructure change",
    "security_destructive": "destructive operation in security-sensitive code",
}
HIGH_TAGS = {"security", "persistence", "destructive", "public_api", "test_harness", "wire_format"}
MEDIUM_TAGS = {"concurrency", "error_handling", "deps_build", "config_runtime", "generated"}
_COMMENT = re.compile(r"^\s*(#|//|/\*|\*|--|;|<!--|\"\"\"|''')")


def _comment_only(lines: list[str]) -> bool:
    body = [ln for ln in lines if ln.strip()]
    return bool(body) and all(_COMMENT.match(ln) for ln in body)


def _removed_public_defs(ch: FileChange) -> bool:
    """A removed (or renamed) public function/class signature: an API surface change."""
    pat = re.compile(r"^\s*(?:async\s+)?(def|class|export\s+(?:async\s+)?(function|class|const)|pub\s+fn|func)"
                     r"\s+([A-Za-z]\w*)")
    removed = {m.group(3) for ln in ch.removed if (m := pat.match(ln)) and not m.group(3).startswith("_")}
    added = {m.group(3) for ln in ch.added if (m := pat.match(ln))}
    return bool(removed - added)


CHECK = re.compile(r"^\s*if\b.*\b(not|!|is None|== *None)\b|^\s*raise\b|\babort\(\s*40[13]|\bdeny\b|"
                   r"PermissionDenied|Unauthorized|Forbidden|\bcheck_\w+\(|\bverify\w*\(|\brequire\w*\(|"
                   r"\b(require|ensure|is)(Auth|Admin|Login|LoggedIn|Authenticated|Permission)\w*\b|"
                   r"@\w*(login|auth|permission)\w*|\bis_authenticated\b|\bassert\b", re.IGNORECASE)
ACCESSISH = re.compile(r"auth|admin|login|permission|role|csrf|token|signature|owner|forbidden|unauthori",
                       re.IGNORECASE)
STYLES = re.compile(r"\.(css|scss|sass|less|styl)$", re.IGNORECASE)
PROSE = re.compile(r"\.(md|rst|adoc|txt)$", re.IGNORECASE)


_SECRET_ASSIGN = re.compile(r"([A-Za-z_][\w.]*)\s*[:=]\s*['\"]([^'\"\s]{8,})['\"]")
_PLACEHOLDER = re.compile(r"^(<.*>|\$\{.*\}|x+|changeme|example|dummy|test|password|secret|your[_-].*|\*+)$", re.I)


def _hardcoded_secret(ch: FileChange) -> str | None:
    """A credential literal in added code: a known token format (the ingest detectors), or a string
    literal assigned to a secret-looking name that isn't a placeholder or an environment lookup."""
    from arbiter_agent.privacy.secrets import SECRET_KEY_NAMES, find_secrets

    for kind, _s, _e in find_secrets("\n".join(ch.added)):
        return kind
    for ln in ch.added:
        for m in _SECRET_ASSIGN.finditer(ln):
            name, value = m.group(1), m.group(2)
            if SECRET_KEY_NAMES.search(name) and not _PLACEHOLDER.match(value) and "environ" not in ln \
                    and "getenv" not in ln and not value.startswith(("http://", "https://", "/")):
                return f"literal assigned to {name}"
    return None


def _removed_checks(ch: FileChange) -> bool:
    """Security checks (guards, raises, verify/permission calls) removed and not re-added."""
    norm = {ln.strip() for ln in ch.added}
    return any(CHECK.search(ln) and ln.strip() not in norm for ln in ch.removed)


def _weakened_asserts(ch: FileChange) -> bool:
    pat = re.compile(r"\bassert|\bexpect\(|\bself\.assert|\bt\.(Error|Fatal)|\bassert_eq!")
    return sum(1 for ln in ch.removed if pat.search(ln)) > sum(1 for ln in ch.added if pat.search(ln))


def classify(ch: FileChange, floors: dict[str, str] | None = None) -> FileRisk:
    tags: set[str] = set()
    reasons: list[str] = []
    for tag, rx in PATH_TAGS:
        if rx.search(ch.path) or (ch.old_path and rx.search(ch.old_path)):
            tags.add(tag)
    prose = bool(PROSE.search(ch.path)) and "deps_build" not in tags
    styles = bool(STYLES.search(ch.path))
    if prose:                          # docs prose: its words aren't code, and its path tags don't apply
        tags = {"docs"}
    if "tests" in tags:                # a test *about* login isn't security-sensitive code
        tags.discard("security")
    lines = ch.added + ch.removed
    code_lines = [] if prose else [ln for ln in lines if not _COMMENT.match(ln)]
    for tag, rx in LINE_TAGS:
        if tag == "security" and "tests" in tags:
            continue
        hits = [ln for ln in code_lines if rx.search(ln)]
        if hits:
            tags.add(tag)
            reasons.append(f"{tag}: `{hits[0].strip()[:80]}`")
    if not prose and "tests" not in tags and _removed_checks(ch) and any(
            ACCESSISH.search(ln) for ln in ch.removed):
        tags.add("security")
        reasons.append("access check removed")
    secret = None if prose else _hardcoded_secret(ch)
    if secret:
        tags.add("security")
        reasons.append(f"hard-coded credential ({secret})")
    if ch.status == "D" and "docs" not in tags:
        tags.add("destructive")
        reasons.append("file deleted")
    if _removed_public_defs(ch) and "tests" not in tags:
        tags.add("public_api")
        reasons.append("public function or class removed or renamed")
    if "tests" in tags and _weakened_asserts(ch):
        tags.add("test_harness")
        reasons.append("assertions removed from tests")
    comment_only = bool(lines) and _comment_only(lines)
    if comment_only:
        tags.add("comments_only")

    critical = []
    if "destructive" in tags and ("persistence" in tags or re.search(r"(^|/)migrations?/|\.sql$", ch.path)):
        critical.append("destructive_persistence")
    if "security" in tags and any(SECURITY_PATH.search(p) for p in (ch.path, ch.old_path or "")) \
            and not comment_only and _removed_checks(ch):
        critical.append("security_check_removed")
    if "ci_deploy" in tags and not comment_only:
        critical.append("ci_deploy")
    if "security" in tags and "destructive" in tags:
        critical.append("security_destructive")

    if critical:
        level = "critical"
        reasons = [CRITICAL_REASONS[c] for c in critical] + reasons
    elif tags & HIGH_TAGS and not comment_only and not ("tests" in tags and tags & HIGH_TAGS <= {"security"}):
        level = "high"
    elif comment_only or (tags <= {"docs", "comments_only"} and tags) or (
            tags & {"tests"} and not tags & (HIGH_TAGS | MEDIUM_TAGS)) or (
            styles and not tags & (HIGH_TAGS | MEDIUM_TAGS)):
        level = "low"
    else:
        level = "medium"
    for tag, floor in (floors or {}).items():
        if tag in tags and level_index(floor) > level_index(level):
            level = floor
            reasons.append(f"review floor for {tag}: {floor}")
    return FileRisk(ch.path, level, sorted(tags), reasons[:6], len(ch.added), len(ch.removed))


def floors_from_config(config: Any) -> dict[str, str]:
    """``review.*_floor`` settings -> {tag: level} (spec §26)."""
    mapping = {"security_floor": "security", "database_migration_floor": "persistence",
               "destructive_change_floor": "destructive", "public_api_floor": "public_api",
               "test_harness_floor": "test_harness"}
    out = {}
    for key, tag in mapping.items():
        val = config.get(f"review.{key}") if config is not None else None
        if val in LEVELS:
            out[tag] = str(val)
    return out


def overall(risks: list[FileRisk]) -> str:
    return max((r.level for r in risks), key=level_index, default="low")
