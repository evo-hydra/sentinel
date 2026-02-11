# Sentinel

**Persistent project memory for coding LLMs.**

[![CI](https://github.com/evo-hydra/sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/evo-hydra/sentinel/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

LLMs are stateless. Git is historical. Sentinel converts git history into structured, queryable intelligence so coding agents do not start from zero.

It provides:

- **Conventions** — naming patterns, import styles, commit conventions (with confidence scores)
- **Pitfalls** — mistakes extracted from bug-fix and revert commits (with severity and prevention)
- **Decisions** — architectural choices inferred from commit messages (with rationale)
- **Hot files** — fragility metrics based on churn, bug density, and revert frequency
- **Co-changes** — files that historically change together (coupling detection)
- **Patterns** — recurring AST structures in the codebase

Sentinel does not modify code. It does not execute commands. It does not act autonomously.
It is a **read-only intelligence surface** over your repository.

---

## Install

A human provisions memory for the agent:

```bash
pip install code-sentinel[mcp]    # Core + MCP server
cd your-project
sentinel init                      # Learn from git history
sentinel mcp-setup                 # Write .mcp.json for Claude Code
```

That's it. The coding LLM now has access to project intelligence via MCP.

Alternative MCP registration:

```bash
claude mcp add sentinel -- sentinel-mcp
```

---

## MCP Tool Contract

Sentinel exposes 7 tools via MCP (stdio transport, FastMCP). All tools are **read-only** with **no side effects**.

### Tools

| Tool | Purpose | When to Call |
|------|---------|-------------|
| `sentinel_project_context` | Full intelligence summary | Session start |
| `sentinel_query` | FTS5 free-text search | Searching specific topics |
| `sentinel_conventions` | Conventions with confidence | Before writing code |
| `sentinel_pitfalls` | Pitfalls with severity | Before modifying risky areas |
| `sentinel_decisions` | Architectural decisions | Understanding "why" |
| `sentinel_hot_files` | Risk-ranked file table | Prioritizing review attention |
| `sentinel_co_changes` | Co-change pairs for a file | Checking what else to update |

### Parameters

| Tool | Parameters | Type |
|------|-----------|------|
| `sentinel_project_context` | (none) | |
| `sentinel_query` | `query: str` | Free-text search terms |
| `sentinel_conventions` | (none) | |
| `sentinel_pitfalls` | (none) | |
| `sentinel_decisions` | (none) | |
| `sentinel_hot_files` | (none) | |
| `sentinel_co_changes` | `file_path: str` | Relative path, e.g. `"src/auth.py"` |

### Response Shape

All tools return **markdown strings**. Response structure is deterministic per tool.

**`sentinel_project_context`** returns:

```markdown
# Sentinel: <project_name>

Knowledge base: N conventions, N decisions, N pitfalls, N patterns, N tracked files, N co-change pairs.

## Conventions
- **[naming]** Use snake_case for functions (confidence: 92%, seen 15x)

## Pitfalls
- **[high]** SQL injection via string formatting -- *prevent:* Use parameterized queries

## Architectural Decisions
- Use SQLite for persistence
  > Zero external dependencies, WAL mode supports concurrent reads

## Hot Files
| File | Risk | Fragility | Likely Pair |
|------|------|-----------|-------------|
| `src/auth.py` | 74 | **67% FRAGILE** | `tests/test_auth.py` (8) |
```

**`sentinel_hot_files`** returns tiered tables:

```markdown
## Hot Files

*FRAGILE = more than half of all changes are bug fixes.*

### Tier A -- Architecture Risk (N files)
| File | Risk | Fragility | Likely Pair |
|------|------|-----------|-------------|
| `src/main.py` | 74 | **67% FRAGILE** | `src/config.py` (12) |

### Tier B -- Core Volatility (N files)
| File | Risk | Fragility | Likely Pair |
|------|------|-----------|-------------|
| `src/auth.py` | 34 | 25% | `tests/test_auth.py` (8) |

### Tier C -- Worth Watching (N files)
| File | Risk | Fragility |
|------|------|-----------|
| `src/utils.py` | 8 | 10% |
```

**Column definitions:**

| Column | Type | Definition |
|--------|------|------------|
| Risk | int | `churn_score * (0.5 + fragility)` — composite scalar |
| Fragility | pct | `bug_fix_count / change_count` — bug-fix ratio |
| Likely Pair | str | Top co-change partner (min 2 co-changes, Tier A/B only) |
| FRAGILE | label | Applied when fragility >= 50% |

**Tier thresholds** (by churn score):

| Tier | Churn | Label |
|------|-------|-------|
| A | >= 50 | Architecture Risk |
| B | >= 20 | Core Volatility |
| C | >= 10 | Worth Watching |
| (omitted) | < 10 | Below threshold |

**Noise filtering:** Images (`.png`, `.jpg`, `.svg`, etc.), lock files (`.lock`, `.sum`), and build artifacts (`.min.js`, `.min.css`, `.map`) are excluded from hot file output.

**`sentinel_co_changes`** returns:

```markdown
## Files that change with `src/auth.py`

- `tests/test_auth.py` (8 co-changes)
- `src/config.py` (4 co-changes)

*When editing the target file, check if these files also need updates.*
```

**Error responses** (no `.sentinel/` found):

```
No `.sentinel/` directory found. Run `sentinel init` in your project root to initialize Sentinel.
```

### Guarantees

- **Read-only.** No tool modifies files, executes code, or writes to the repository.
- **Deterministic.** Same knowledge store produces same output. No randomness.
- **Fail-safe.** Missing `.sentinel/` returns a clear error string, never throws.
- **No network.** MCP server reads local SQLite only. Zero external calls.
- **Self-contained.** Each tool call opens and closes its own DB connection. No leaked state.

---

## Performance Characteristics

| Operation | Cost | Notes |
|-----------|------|-------|
| `sentinel init` | O(commits) | One-time. ~1s per 100 commits. |
| `sentinel init --deep` | O(commits * files) | Deeper analysis. Slower but richer. |
| `sentinel swarm` | O(new commits) | Incremental. Runs in <1s for typical workflows. |
| MCP tool call | O(1) | SQLite reads. Sub-100ms. |
| DB size | ~1KB per 10 commits | `.sentinel/sentinel.db` stays small. |

---

## Knowledge Store Schema

All data lives in `.sentinel/sentinel.db` (SQLite with FTS5). Knowledge types:

| Type | Source | What It Captures |
|------|--------|------------------|
| Conventions | Naming patterns, import styles | How code _should_ look |
| Decisions | Commit messages with rationale | Why things are done a certain way |
| Pitfalls | Reverts, bug fixes | Mistakes to avoid repeating |
| Patterns | Recurring AST structures | Common code idioms |
| Hot Files | Change frequency, bug density | Files needing extra scrutiny |
| Co-Changes | Files in same commits | Coupling that isn't in the imports |

---

## CLI Reference

| Command | Purpose |
|---------|---------|
| `sentinel init [path]` | Initialize, learn from git history |
| `sentinel init --deep` | Deep analysis (file-level metrics) |
| `sentinel hunt <paths>` | Scan files against knowledge |
| `sentinel hunt --llm` | LLM-powered review (5 providers) |
| `sentinel hunt --llm-bg` | Background LLM review |
| `sentinel swarm` | Incremental learning from new commits |
| `sentinel hive list` | List knowledge entries |
| `sentinel hive add <type> <desc>` | Add manual knowledge |
| `sentinel hive search <query>` | Full-text search |
| `sentinel watch` | Install git hooks (pre-commit + post-commit) |
| `sentinel mcp-setup` | Write `.mcp.json` for Claude Code |

### LLM Providers

```bash
sentinel hunt src/ --llm --provider <name>
```

| Provider | Requires |
|----------|----------|
| `ollama` | Local Ollama instance |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `gemini` | `GEMINI_API_KEY` |
| `grok` | `GROK_API_KEY` |

Install with: `pip install code-sentinel[llm]`

---

## Development

```bash
git clone https://github.com/evo-hydra/sentinel.git
cd sentinel
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp]"

pytest --cov                                        # 192 tests
ruff check src/ tests/                              # Lint
mypy src/sentinel/ --ignore-missing-imports         # Types
```

---

## License

MIT
