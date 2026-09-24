"""Symbol and import extraction (spec §11.2): exact for Python (``ast``), line-regex for JS/TS, Go,
Rust, Java/Kotlin, C#. Other files get lexical indexing only."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

LANG_BY_EXT = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".scala": "scala", ".sh": "shell", ".ps1": "powershell", ".sql": "sql", ".md": "markdown",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".html": "html", ".css": "css",
}


@dataclass
class Symbol:
    name: str
    kind: str                 # function | method | class | interface | type | enum | const | module
    line: int
    end_line: int
    parent: str = ""
    signature: str = ""


@dataclass
class Import:
    module: str               # as written ("a.b", "./x", "crate::a", "github.com/x/y")
    line: int
    names: list[str] = field(default_factory=list)
    level: int = 0            # Python relative import level


@dataclass
class Analysis:
    lang: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    calls: list[tuple[str, int]] = field(default_factory=list)


def language_of(path: str) -> str:
    dot = path.rfind(".")
    return LANG_BY_EXT.get(path[dot:].lower(), "") if dot >= 0 else ""


# ----------------------------------------------------------------------------- Python
def _py(text: str) -> Analysis:
    a = Analysis("python")
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _regex(text, "python")

    def visit(node: ast.AST, parent: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [x.arg for x in child.args.args]
                kind = "method" if parent and parent[:1].isupper() else "function"
                a.symbols.append(Symbol(child.name, kind, child.lineno, getattr(child, "end_lineno", child.lineno) or
                                        child.lineno, parent, f"{child.name}({', '.join(args)})"))
                visit(child, f"{parent}.{child.name}" if parent else child.name)
            elif isinstance(child, ast.ClassDef):
                a.symbols.append(Symbol(child.name, "class", child.lineno,
                                        getattr(child, "end_lineno", child.lineno) or child.lineno, parent))
                visit(child, f"{parent}.{child.name}" if parent else child.name)
            elif isinstance(child, ast.Assign) and not parent:
                for t in child.targets:
                    if isinstance(t, ast.Name) and t.id.isupper():
                        a.symbols.append(Symbol(t.id, "const", child.lineno, child.lineno))
                visit(child, parent)
            elif isinstance(child, ast.Import):
                for n in child.names:
                    a.imports.append(Import(n.name, child.lineno, [n.asname or n.name]))
            elif isinstance(child, ast.ImportFrom):
                a.imports.append(Import(child.module or "", child.lineno, [n.name for n in child.names],
                                        child.level or 0))
            elif isinstance(child, ast.Call):
                f = child.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                if name:
                    a.calls.append((name, child.lineno))
                visit(child, parent)
            else:
                visit(child, parent)

    visit(tree, "")
    return a


# ----------------------------------------------------------------------------- regex languages
_KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "function", "else", "new", "typeof", "await", "super",
             "constructor"}
PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "javascript": [
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)")),
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
                                r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
                                r"(?:async\s+)?function")),
        ("method", re.compile(r"^\s+(?:static\s+|async\s+|public\s+|private\s+|protected\s+|readonly\s+)*"
                              r"([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::\s*[^{]+)?\{\s*$")),
    ],
    "go": [
        ("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[\[(]")),
        ("type", re.compile(r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface|func|\w)")),
    ],
    "rust": [
        ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?"
                                r"fn\s+([A-Za-z_]\w*)")),
        ("type", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type|union)\s+([A-Za-z_]\w*)")),
        ("module", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)")),
    ],
    "java": [
        ("class", re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract|sealed)\s+)*"
                             r"(?:class|interface|enum|record|@interface)\s+([A-Za-z_]\w*)")),
        ("method", re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|default)\s+)+"
                              r"[\w<>\[\],.?\s]+\s+([A-Za-z_]\w*)\s*\(")),
    ],
    "kotlin": [
        ("class", re.compile(r"^\s*(?:(?:public|private|internal|data|sealed|abstract|open|enum)\s+)*"
                             r"(?:class|interface|object)\s+([A-Za-z_]\w*)")),
        ("function", re.compile(r"^\s*(?:(?:public|private|internal|override|suspend|inline)\s+)*fun\s+"
                                r"(?:<[^>]+>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\(")),
    ],
    "csharp": [
        ("class", re.compile(r"^\s*(?:(?:public|private|protected|internal|static|sealed|abstract|partial)\s+)*"
                             r"(?:class|interface|struct|enum|record)\s+([A-Za-z_]\w*)")),
        ("method", re.compile(r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|async|sealed)"
                              r"\s+)+[\w<>\[\],.?\s]+\s+([A-Za-z_]\w*)\s*\(")),
    ],
    "python": [
        ("function", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)")),
        ("class", re.compile(r"^\s*class\s+([A-Za-z_]\w*)")),
    ],
}
PATTERNS["typescript"] = PATTERNS["javascript"] + [
    ("interface", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?interface\s+([A-Za-z_$][\w$]*)")),
    ("type", re.compile(r"^\s*(?:export\s+)?(?:declare\s+)?type\s+([A-Za-z_$][\w$]*)\s*(?:<[^=]*>)?\s*=")),
    ("enum", re.compile(r"^\s*(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)")),
]
IMPORTS: dict[str, list[re.Pattern[str]]] = {
    "javascript": [re.compile(r"""(?:^|\s)import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]"""),
                   re.compile(r"""(?:^|\s)export\s+[^'"]*?\s+from\s+['"]([^'"]+)['"]"""),
                   re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)"""),
                   re.compile(r"""\bimport\(\s*['"]([^'"]+)['"]\s*\)""")],
    "go": [re.compile(r'^\s*(?:import\s+)?(?:[A-Za-z_.]+\s+)?"([^"]+)"\s*$')],
    "rust": [re.compile(r"^\s*(?:pub\s+)?use\s+([\w:]+)"), re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;")],
    "java": [re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)")],
    "kotlin": [re.compile(r"^\s*import\s+([\w.]+)")],
    "csharp": [re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;")],
}
IMPORTS["typescript"] = IMPORTS["javascript"]


def _regex(text: str, lang: str) -> Analysis:
    a = Analysis(lang)
    pats = PATTERNS.get(lang, [])
    imps = IMPORTS.get(lang, [])
    in_go_import = False
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 1000:
            continue
        for kind, pat in pats:
            m = pat.match(line)
            if m and m.group(1) not in _KEYWORDS:
                a.symbols.append(Symbol(m.group(1), kind, i, i, signature=line.strip()[:160]))
                break
        if lang == "go":
            s = line.strip()
            if s.startswith("import ("):
                in_go_import = True
                continue
            if in_go_import and s == ")":
                in_go_import = False
                continue
            if not (in_go_import or s.startswith("import ")):
                continue
        for pat in imps:
            for m in pat.finditer(line):
                a.imports.append(Import(m.group(1), i))
    return a


def analyze(path: str, text: str) -> Analysis:
    lang = language_of(path)
    if lang == "python":
        return _py(text)
    if lang in PATTERNS:
        return _regex(text, lang)
    return Analysis(lang)
