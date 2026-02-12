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
- **Semantic search** — embedding-based similarity search with hybrid FTS5 fallback
- **Feedback loop** — track accepted/rejected suggestions, self-improving confidence scores
- **PR review** — analyze pull requests against project knowledge with risk assessment
- **Cross-project knowledge** — anonymized pattern sharing between projects

Sentinel does not modify code. It does not execute commands. It does not act autonomously.
It is a **read-only intelligence surface** over your repository.

---

## Install

A human provisions memory for the agent:

```bash
pip install git-sentinel[mcp]    # Core + MCP server
cd your-project
sentinel init                      # Learn from git history
sentinel init --embed              # Learn + generate embeddings for semantic search
sentinel mcp-setup                 # Write .mcp.json for Claude Code
```

That's it. The coding LLM now has access to project intelligence via MCP.

Alternative MCP registration:

```bash
claude mcp add sentinel -- sentinel-mcp
```

---

## MCP Tool Contract

Sentinel exposes 8 tools via MCP (stdio transport, FastMCP). All read tools are **read-only** with **no side effects**. The feedback tool is the only write operation.

### Tools

| Tool | Purpose | When to Call |
|------|---------|-------------|
| `sentinel_project_context` | Full intelligence summary | Session start |
| `sentinel_query` | Free-text or semantic search | Searching specific topics |
| `sentinel_conventions` | Conventions with confidence | Before writing code |
| `sentinel_pitfalls` | Pitfalls with severity | Before modifying risky areas |
| `sentinel_decisions` | Architectural decisions | Understanding "why" |
| `sentinel_hot_files` | Risk-ranked file table | Prioritizing review attention |
| `sentinel_co_changes` | Co-change pairs for a file | Checking what else to update |
| `sentinel_feedback` | Submit feedback on knowledge | After acting on a suggestion |

### Parameters

| Tool | Parameters | Type |
|------|-----------|------|
| `sentinel_project_context` | (none) | |
| `sentinel_query` | `query: str`, `limit: int` (opt), `offset: int` (opt), `semantic: bool` (opt) | Free-text or natural language search |
| `sentinel_conventions` | `limit: int` (opt), `offset: int` (opt) | Default limit=50 |
| `sentinel_pitfalls` | `limit: int` (opt), `offset: int` (opt) | Default limit=50 |
| `sentinel_decisions` | `limit: int` (opt), `offset: int` (opt) | Default limit=30 |
| `sentinel_hot_files` | (none) | |
| `sentinel_co_changes` | `file_path: str`, `limit: int` (opt), `offset: int` (opt) | Relative path, e.g. `"src/auth.py"` |
| `sentinel_feedback` | `knowledge_id: str`, `outcome: str`, `context: str` (optional) | ID from tool output, `"accepted"` / `"rejected"` / `"modified"` |

**Pagination:** Tools that accept `limit`/`offset` append a footer when more results are available:

```
*Showing 1–50 of 127. Use offset=50 to see more.*
```

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

**`sentinel_query`** with `semantic=True` includes similarity scores:

```markdown
## Search Results for `how do we handle errors`

- **[convention]** Use structured logging for all error paths (92% match)
- **[pitfall]** Swallowed exceptions in middleware (78% match)
```

**`sentinel_conventions`**, **`sentinel_pitfalls`**, and **`sentinel_decisions`** now include truncated knowledge IDs (e.g. `(id: abc123de)`) so agents can reference specific entries when submitting feedback.

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

**`sentinel_feedback`** returns:

```
Feedback recorded: accepted on abc123de... (3 total feedback entries for this item)
```

**Error responses** (no `.sentinel/` found):

```
No `.sentinel/` directory found. Run `sentinel init` in your project root to initialize Sentinel.
```

### Guarantees

- **Read-only** (except feedback). No tool modifies files, executes code, or writes to the repository.
- **Deterministic.** Same knowledge store produces same output. No randomness.
- **Fail-safe.** Missing `.sentinel/` returns a clear error string, never throws.
- **No network.** MCP server reads local SQLite only. Zero external calls.
- **Self-contained.** Each tool call opens and closes its own DB connection. No leaked state.

### Batching Guidance

Sentinel tools are safe to call in parallel with each other — they use independent SQLite connections with WAL mode.

However, **do not batch Sentinel calls alongside tools that may fail** (e.g., `Bash(tsc)`, linters, test runners). In Claude Code, a sibling tool failure in the same parallel batch cancels all in-flight MCP calls with `"Sibling tool call errored"`. Since Sentinel calls are fast (<100ms) and never fail, batching them with fallible tools wastes the results.

---

## Feedback Loop

Sentinel learns from your feedback. When a convention, pitfall, or decision is surfaced, you can tell Sentinel whether it was useful:

```bash
sentinel feedback submit <knowledge_id> accepted
sentinel feedback submit <knowledge_id> rejected --context "Not relevant to this project"
sentinel feedback stats
```

Or via MCP (agents can do this automatically):

```
sentinel_feedback(knowledge_id="abc123de", outcome="accepted")
```

**How it works:**
- `accepted` / `rejected` feedback increments counters on conventions and pitfalls
- Convention confidence is recalculated: `new = 0.6 * (accepted / total) + 0.4 * current`
- Frequently rejected entries naturally drop in confidence and visibility
- Knowledge IDs are shown in all MCP tool output for easy reference

---

## PR Review

Analyze pull requests against project knowledge before merging:

```bash
sentinel pr-review                          # Review current branch vs main
sentinel pr-review --base develop           # Custom base branch
sentinel pr-review --json                   # Structured output
sentinel pr-review --post                   # Post as GitHub PR comment (requires gh CLI)
sentinel pr-review --update                 # Create or update a single PR comment (upsert)
sentinel pr-review --exit-code              # Exit with code 1 if risk is HIGH
```

PR review checks:
- **Convention violations** and **pitfall matches** in changed files
- **Hot files touched** with churn/fragility stats
- **Missing co-changes** — files that usually change together but weren't in the PR
- **Relevant context** — decisions and pitfalls related to the changed area

---

## GitHub Action

Run Sentinel PR reviews automatically on every push with a composite GitHub Action. No hosting required — uses your existing CI infrastructure.

### Quick Start

```yaml
# .github/workflows/sentinel.yml
name: Sentinel PR Review
on:
  pull_request:
    branches: [main]

permissions:
  pull-requests: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Full history required

      - uses: evo-hydra/sentinel@v1
        with:
          exit-code: "true"  # Fail if HIGH risk
```

### Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `version` | latest | `git-sentinel` version to install |
| `python-version` | `3.12` | Python version to use |
| `base-branch` | repo default | Base branch for PR comparison |
| `max-commits` | `500` | Maximum commits to analyze during init |
| `exit-code` | `false` | Fail workflow if risk level is HIGH |
| `post-comment` | `true` | Post/update a review comment on the PR |

### Outputs

| Output | Description |
|--------|-------------|
| `risk-level` | `HIGH`, `MEDIUM`, or `LOW` |
| `findings-count` | Number of findings detected |

### How It Works

1. **Cache** — restores `.sentinel/` from GitHub Actions cache for fast incremental updates
2. **Init or Swarm** — runs `sentinel init` on first run, `sentinel swarm` (incremental, <1s) on subsequent runs
3. **Review** — analyzes PR changes against project knowledge
4. **Comment** — creates or updates a single PR comment (no spam — uses `--update` to upsert)
5. **Gate** — optionally fails the workflow if risk level is HIGH

### Notes

- **Full git history required** — use `fetch-depth: 0` in your checkout step
- **No LLM needed** — the action uses rule-based analysis only (no API keys required)
- **Fork PRs** — `github.token` cannot write comments on PRs from forks; set `post-comment: "false"` and use `risk-level` output instead
- **Comment upsert** — uses an HTML marker (`<!-- sentinel-review -->`) to find and update existing comments, avoiding comment spam on repeated pushes

---

## Cross-Project Knowledge

Share anonymized patterns between projects:

```bash
# Export (strips commit SHAs, authors, file paths)
sentinel share export --output patterns.json

# Import into another project (deduplicates, caps confidence at 0.3)
sentinel share import patterns.json
```

Exported data includes only pattern descriptions, categories, severity, confidence, and frequency. No PII. The source project is identified only by a SHA256 hash for deduplication.

---

## Semantic Search

Sentinel's default FTS5 search is keyword-based — searching "authentication" won't find entries about "login flow". Embedding-based semantic search closes this gap.

### Setup

Generate embeddings for all knowledge entries:

```bash
sentinel embed                              # Default: Ollama + nomic-embed-text
sentinel embed --provider openai            # Use OpenAI text-embedding-3-small
sentinel embed --model custom-model         # Custom model
sentinel embed --type convention            # Only embed conventions
sentinel embed --force                      # Re-embed everything
```

Or generate embeddings during init:

```bash
sentinel init --embed                       # Learn + embed in one step
sentinel init --enrich --embed              # Learn + enrich + embed
```

### Usage

**CLI** — `hive search` auto-detects whether to use semantic or FTS5 search. If embeddings exist and the query doesn't use FTS5 syntax (AND, OR, NOT, `"`, `*`), semantic search is used automatically:

```bash
sentinel hive search "how do we handle auth"    # Semantic (auto-detected)
sentinel hive search "auth AND login"           # FTS5 (detected by syntax)
sentinel hive search "auth" --semantic          # Force semantic
```

Semantic results include similarity scores:

```
  convention   abc123de… [92%] authentication — Auth module conventions
  pitfall      def456gh… [78%] SQL injection vulnerability
```

**MCP** — pass `semantic=True` to `sentinel_query`:

```
sentinel_query(query="how do we handle errors", semantic=True)
```

When `semantic=True` but no embeddings exist, or the embedding provider is unavailable, Sentinel falls back to FTS5 silently.

### Embedding Providers

| Provider | Model (default) | Requires |
|----------|-----------------|----------|
| `ollama` | `nomic-embed-text` (768d) | Local Ollama instance |
| `openai` | `text-embedding-3-small` (1536d) | `OPENAI_API_KEY` |

Configure in `.sentinel/config.yaml`:

```yaml
embed_provider: ollama
embed_model: nomic-embed-text
embed_batch_size: 50
```

### How It Works

- Embeddings are stored as packed float32 BLOBs in SQLite (schema v6, `embeddings` table)
- Search uses pure Python cosine similarity — O(n) scan, trivially fast at Sentinel's scale
- `semantic_search()` is a separate method from `search()` — callers choose which to use
- No new required dependencies: Ollama uses stdlib `urllib`, OpenAI reuses the existing `[llm]` extra

---

## Performance Characteristics

| Operation | Cost | Notes |
|-----------|------|-------|
| `sentinel init` | O(commits) | One-time. ~1s per 100 commits. |
| `sentinel init --deep` | O(commits * files) | Deeper analysis. Slower but richer. |
| `sentinel init --enrich` | O(commits / batch) | LLM enrichment. ~30s per 25 commits. |
| `sentinel embed` | O(entries / batch) | Embedding generation. ~5s per 50 entries (Ollama). |
| `sentinel swarm` | O(new commits) | Incremental. Runs in <1s for typical workflows. |
| MCP tool call | O(1) | SQLite reads. Sub-100ms. |
| Semantic search | O(embeddings) | Pure Python cosine sim. Sub-100ms for typical DBs. |
| DB size | ~1KB per 10 commits | `.sentinel/sentinel.db` stays small. |

---

## Knowledge Store Schema

All data lives in `.sentinel/sentinel.db` (SQLite with FTS5, schema version 6). Knowledge types:

| Type | Source | What It Captures |
|------|--------|------------------|
| Conventions | Naming patterns, import styles | How code _should_ look |
| Decisions | Commit messages with rationale | Why things are done a certain way |
| Pitfalls | Reverts, bug fixes | Mistakes to avoid repeating |
| Patterns | Recurring AST structures | Common code idioms |
| Hot Files | Change frequency, bug density | Files needing extra scrutiny |
| Co-Changes | Files in same commits | Coupling that isn't in the imports |
| Feedback | User/agent responses | Which suggestions are useful |
| Shared Patterns | Cross-project imports | Patterns from other codebases |
| Embeddings | Vector representations | Semantic search over knowledge |

Schema migrations run automatically when opening a database from an older version. No manual intervention required.

---

## CLI Reference

| Command | Purpose |
|---------|---------|
| `sentinel init [path]` | Initialize, learn from git history |
| `sentinel init --deep` | Deep analysis (file-level metrics) |
| `sentinel init --enrich` | LLM-powered semantic enrichment |
| `sentinel init --embed` | Generate embeddings for semantic search |
| `sentinel embed` | Generate/update embeddings for semantic search |
| `sentinel hunt <paths>` | Scan files against knowledge |
| `sentinel hunt --llm` | LLM-powered review (5 providers) |
| `sentinel hunt --llm-bg` | Background LLM review |
| `sentinel swarm` | Incremental learning from new commits |
| `sentinel swarm --embed` | Incremental learning + refresh embeddings |
| `sentinel hive list [--offset N]` | List knowledge entries (paginated) |
| `sentinel hive add <type> <desc>` | Add manual knowledge |
| `sentinel hive search <query>` | Full-text search (auto-detects semantic) |
| `sentinel hive search <q> --semantic` | Force semantic search |
| `sentinel feedback submit <id> <outcome>` | Submit feedback on a knowledge entry |
| `sentinel feedback stats` | View aggregate feedback statistics |
| `sentinel pr-review` | Analyze PR against project knowledge |
| `sentinel pr-review --update` | Create or update a single PR comment (upsert) |
| `sentinel pr-review --exit-code` | Exit with code 1 if risk is HIGH |
| `sentinel share export` | Export anonymized patterns |
| `sentinel share import <file>` | Import cross-project patterns |
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

Install with: `pip install git-sentinel[llm]`

### Embedding Providers

```bash
sentinel embed --provider <name>
```

| Provider | Default Model | Requires |
|----------|--------------|----------|
| `ollama` | `nomic-embed-text` | Local Ollama instance |
| `openai` | `text-embedding-3-small` | `OPENAI_API_KEY` |

---

## Development

```bash
git clone https://github.com/evo-hydra/sentinel.git
cd sentinel
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp]"

pytest --cov                                        # 329 tests
ruff check src/ tests/                              # Lint
mypy src/sentinel/ --ignore-missing-imports         # Types
```

---

## License

MIT
