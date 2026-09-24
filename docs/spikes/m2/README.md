# M2 evidence — clients + setup

All client homes are sandboxed (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `ARBITER_CLIENT_HOME` point at temp directories). The autouse fixture in `tests/conftest.py` enforces this for every test.

| Exit item | Evidence | Result |
| --- | --- | --- |
| `uv tool install` → `arbiter setup` configures Codex + Claude Code with no manual edits | [m4/tool_install_check.json](../m4/tool_install_check.json) (local wheel installed into a throwaway `UV_TOOL_DIR`; script: [m4/scripts/tool_install_check.py](../m4/scripts/tool_install_check.py)) | pass: setup exit 0; both clients configured (T1 T2); the Codex MCP entry points at the installed tool exe |
| Fixture matrix 100%, uninstall byte-identical | `tests/test_clients_setup.py`: Codex 3 `config.toml` × 3 `hooks.json` states and Claude 2 `.claude.json` × 3 `settings.json` states; setup, re-run, uninstall | pass |
| 0 corrupted configs | the same matrix, plus unsafe/unparseable files left untouched and live-file (`~/.claude.json`) re-check writes | pass |
| `arbiter doctor` shows the verified tier set | `tests/test_doctor_e2e.py`: T1 via MCP `clientInfo`, T2 via real hook round trips, T3 via the transcript parsers | pass: T1 T2 T3 for both clients |

Installing from PyPI waits on publishing (see M4).
