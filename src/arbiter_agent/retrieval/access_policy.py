"""Access policy for indexing and retrieval (spec §11.5, §11.7).

Applied twice: before a file is read for indexing, and again before any content leaves a
retrieval call. Denied by default:
- credential stores and secret-bearing files (``.env``, keys, certificates, cloud/SSH config);
- generated and vendored trees, lockfiles, minified bundles, source maps;
- binaries and files over the size cap;
- anything matched by ``.arbiterignore`` in the repository root.
File *content* that passes still goes through secret redaction before it's stored.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_BYTES = 1024 * 1024

SECRET_PATHS = re.compile(
    r"(^|/)\.env(\.(?!example$|sample$|template$|dist$)[^/]*)?$|"
    r"\.(pem|key|p12|pfx|jks|keystore|asc|gpg|kdbx|ovpn|tfvars|tfstate|tfstate\.backup)$|"
    r"(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$|"
    r"(^|/)\.(ssh|aws|gnupg|azure|kube|docker|gcloud)/|"
    r"(^|/)(credentials|secrets?)(\.[a-z0-9]+)?$|"
    r"(^|/)\.(npmrc|pypirc|netrc|git-credentials|htpasswd|pgpass)$|"
    r"(^|/)kubeconfig[^/]*$|(^|/)service[-_]?account[^/]*\.json$",
    re.I)
GENERATED_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "target", ".tox",
                  ".nox", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".next", ".nuxt", "coverage", ".gradle",
                  "bin", "obj", "vendor", ".cache", "site-packages", ".arbiter", ".idea", ".vscode", "out",
                  ".terraform", "bower_components", ".svelte-kit", ".turbo", ".parcel-cache"}
GENERATED_FILES = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Cargo\.lock|uv\.lock|go\.sum|"
    r"composer\.lock|Gemfile\.lock|Pipfile\.lock|bun\.lockb?)$|\.min\.(js|css)$|\.map$|\.pyc$|\.pb\.go$|"
    r"_pb2(_grpc)?\.py$|\.generated\.[a-z]+$",
    re.I)
BINARY_EXT = re.compile(
    r"\.(png|jpe?g|gif|bmp|ico|webp|svgz|tiff?|psd|pdf|zip|gz|tgz|bz2|xz|7z|rar|tar|exe|dll|so|dylib|a|lib|o|obj|"
    r"class|jar|war|whl|egg|woff2?|ttf|otf|eot|mp[34]|m4a|wav|flac|ogg|mov|avi|mkv|webm|sqlite3?|db|bin|dat|"
    r"wasm|pyd|onnx|safetensors|gguf|pt|pth|ckpt|npy|npz|parquet|feather|xlsx?|docx?|pptx?)$",
    re.I)


@dataclass
class AccessPolicy:
    root: Path
    include_generated: bool = False
    max_bytes: int = MAX_FILE_BYTES
    ignore_patterns: list[str] = field(default_factory=list)

    @classmethod
    def for_repo(cls, root: Path, include_generated: bool = False) -> AccessPolicy:
        pats: list[str] = []
        f = root / ".arbiterignore"
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    pats.append(line)
        except OSError:
            pass
        return cls(root, include_generated, ignore_patterns=pats)

    def path_reason(self, rel: str) -> str | None:
        """Why a repository-relative path is denied (None = allowed)."""
        rel = rel.replace("\\", "/").lstrip("/")
        if SECRET_PATHS.search(rel):
            return "secret-bearing path"
        parts = rel.split("/")
        if not self.include_generated:
            if any(p in GENERATED_DIRS for p in parts[:-1]):
                return "generated or vendored directory"
            if GENERATED_FILES.search(rel):
                return "generated file"
        if BINARY_EXT.search(rel):
            return "binary file type"
        for pat in self.ignore_patterns:
            p = pat.rstrip("/")
            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, f"{p}/*") or any(
                    fnmatch.fnmatch(part, p) for part in parts):
                return ".arbiterignore"
        return None

    def content_reason(self, data: bytes) -> str | None:
        if len(data) > self.max_bytes:
            return "over size cap"
        if b"\x00" in data[:8192]:
            return "binary content"
        return None

    def allowed(self, rel: str) -> bool:
        return self.path_reason(rel) is None
