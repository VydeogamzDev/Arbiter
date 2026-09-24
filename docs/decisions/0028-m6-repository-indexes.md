# 0028 — M6 repository indexes: content-addressed analysis, per-worktree views, refresh-before-query

- Status: Accepted
- Date: 2026-09-24
- Spec: §11.2, §11.3, §11.5, §11.7, §11.8, §11.9

## Context
Arbiter's retrieval tools need an index that is never stale, never exposes secrets, and records which index version answered. The index must also cope with branches and worktrees without re-indexing everything.

## Decisions
1. **One SQLite database per repository,** keyed by the git common directory. It lives under `<data>/indexes/`, separate from the controller database, so indexing never contends with the single event-log writer.
2. **Content-addressed analysis.** Chunks (FTS5, 60 lines each), symbols, imports and calls are keyed by the file's SHA-256.
   - **Views** map repository paths to content hashes, one view per worktree root, and record HEAD.
   - A branch switch re-analyzes only content the index hasn't seen; switching back re-analyzes nothing.
   - Two worktrees share analysis but keep separate views: the §11.5 overlay requirement.
   - Unreferenced analysis is kept for 7 days to make switching back free, then removed by `arbiter index gc`.
3. **Refresh before every query.** Files are listed with `git ls-files --cached --others --exclude-standard`, which respects `.gitignore` (outside a repository, a bounded directory walk is used).
   - Changes are detected from size and mtime. Anything modified in the last 2 seconds is re-hashed anyway, to catch same-tick, same-size edits.
   - Refresh has a time budget (`retrieval.refresh_budget_s`, default 3 s). Changed files that don't fit are **removed from the view** until the next refresh and reported as `pending`, so a query never returns stale content.
   - Indexes are warmed in the background the first time a session reports its working directory.
4. **Access policy, applied twice:** before a file is read, and before content is returned.
   - **Always denied:** secret-bearing paths (`.env*` except example/sample/template/dist, keys and certificates, `.ssh`/`.aws`/`.kube`, credential files, `*.tfvars`/`*.tfstate`, service-account JSON), binaries, and files over 1 MB.
   - **Denied by default** (`retrieval.include_generated`): generated and vendored trees, lockfiles, minified bundles and source maps.
   - **User exclusions:** paths listed in `.arbiterignore`.
   - **Redaction:** content that passes is still redacted by the ingest secret detectors, using the install key, **before it's stored**. The index database never holds a detected secret, which the tests verify by scanning its files.
5. **Structure.** Python symbols, imports and calls come from `ast`. JS/TS, Go, Rust, Java, Kotlin and C# use line-regex extraction; other files are indexed lexically only.
   - Imports resolve to repository files only where that's deterministic: Python absolute and relative imports, JS/TS relative specifiers, Go module paths, and Rust `mod` / `crate::` paths. Bare packages are external.
   - A file-level dependency graph (`state/dependency_graph.py`) gives importers and tests, and is cached per view generation.
6. **Index stamp on every retrieval:** `{version, generation, head, root, files, pending}`. When the caller maps to a session, retrievals are also audited as `internal.retrieval` events (op, result count, stamp; never content).
7. **Embeddings are off.** The interface (`retrieval/embeddings.py`) exists so a vector channel can join later; only `off` ships.
8. **MCP tools:**
   - `arbiter_search`: full text plus path matches, with `path:line` snippets;
   - `arbiter_symbol`: definitions, call and text references, importers;
   - `arbiter_related`: imports, importers, tests.

   The CLI adds `arbiter search` and `arbiter index`. In hosted sessions these serve only Arbiter's own calls (§11.8).

## Consequences
- On Arbiter's own repository (234 files), a first build takes about 2.6 s and later refresh-plus-query about 0.6 s. These tools are for agents to call, not a hook path.
- Symbol extraction outside Python is heuristic. Structure-dependent features (M9 blast radius, test selection) must treat it as candidate generation, not ground truth.
- Semantic reranking (SemIf over candidates) and adaptive top-k (§11.4) arrive with M7 and M9.
