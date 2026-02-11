# Sentinel

**Persistent project intelligence that makes AI code assistants actually understand your codebase.**

[![CI](https://github.com/evo-hydra/sentinel/actions/workflows/ci.yml/badge.svg)](https://github.com/evo-hydra/sentinel/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## The Problem

Every AI coding session starts cold. Your assistant doesn't know your naming conventions, your architectural decisions, the files that break every sprint, or the pitfalls your team has already learned the hard way. It writes code that's _almost right_ — and "almost right" is the most expensive kind of wrong.

## What Sentinel Does

Sentinel learns from your git history and stores that knowledge persistently. It then exposes it through two channels:

1. **CLI** — `sentinel hunt` scans code against learned knowledge using AST analysis + optional LLM review
2. **MCP Server** — `sentinel-mcp` gives AI tools (Claude Code, etc.) direct access to your project intelligence

```
Git History → sentinel init → Knowledge Store (SQLite+FTS5)
                                    ↓
                    sentinel hunt (CLI verification)
                    sentinel-mcp  (MCP for AI tools)
```

## Quick Start

```bash
pip install code-sentinel
cd your-project
sentinel init            # Learn from git history
sentinel hunt src/       # Scan for issues
```

With LLM-powered review:

```bash
pip install code-sentinel[llm]
sentinel hunt src/ --llm --provider anthropic
```

With Claude Code integration:

```bash
pip install code-sentinel[mcp]
sentinel mcp-setup       # Writes .mcp.json
# Claude Code now has access to your project knowledge
```

## Commands

| Command | Purpose |
|---------|---------|
| `sentinel init [path]` | Initialize Sentinel, learn from git history |
| `sentinel hunt <paths>` | Scan files for issues against knowledge |
| `sentinel swarm` | Incremental learning from new commits |
| `sentinel hive list` | List knowledge entries |
| `sentinel hive add <type> <desc>` | Add manual knowledge |
| `sentinel hive search <query>` | Full-text search knowledge |
| `sentinel watch` | Install/uninstall git hooks |
| `sentinel mcp-setup` | Write `.mcp.json` for Claude Code |

## LLM-Powered Review

Sentinel supports 5 LLM providers for deeper code review:

| Provider | Flag | Models |
|----------|------|--------|
| Ollama | `--provider ollama` | Any local model (auto-detected) |
| Anthropic | `--provider anthropic` | Claude 4.5 Sonnet, etc. |
| OpenAI | `--provider openai` | GPT-4o, o1, o3, etc. |
| Gemini | `--provider gemini` | Gemini 2.5 Pro, etc. |
| Grok | `--provider grok` | Grok models |

```bash
# Synchronous — wait for results
sentinel hunt src/ --llm --provider anthropic --model claude-sonnet-4-5-20250929

# Background — get AST results now, LLM results later
sentinel hunt src/ --llm-bg --provider ollama
sentinel hunt --llm-status    # Check progress
sentinel hunt --llm-results   # Retrieve findings

# Concurrent workers for large codebases
sentinel hunt src/ --llm --llm-workers 4
```

The LLM receives project-specific context (conventions, pitfalls, hot file warnings, co-changes) assembled by the ContextEngine, making reviews aware of _your_ codebase.

## Claude Code Integration (MCP)

Sentinel's MCP server gives Claude Code direct access to your project knowledge.

### Setup

```bash
pip install code-sentinel[mcp]
sentinel init               # If not already initialized
sentinel mcp-setup          # Writes .mcp.json to project root
```

Or manually:

```bash
claude mcp add sentinel -- sentinel-mcp
```

### Available Tools

| Tool | When to Use |
|------|------------|
| `sentinel_project_context` | Session start — prime with all project knowledge |
| `sentinel_query` | Search for specific topics across all knowledge |
| `sentinel_conventions` | Before writing code — follow established patterns |
| `sentinel_pitfalls` | Before modifying risky areas — avoid known mistakes |
| `sentinel_decisions` | Understand "why" before changing architecture |
| `sentinel_hot_files` | Prioritize review attention on high-churn files |
| `sentinel_co_changes` | When editing a file, check what else needs updating |

### Example Output

```
# Sentinel: myproject

Knowledge base: 23 conventions, 8 decisions, 12 pitfalls, 5 patterns, 15 hot files, 42 co-change pairs.

## Conventions
- **[naming]** Use snake_case for functions (confidence: 92%, seen 15x)
- **[import]** Always use future annotations (confidence: 85%, seen 10x)

## Pitfalls
- **[high]** SQL injection via string formatting — *prevent:* Use parameterized queries
- **[medium]** Missing null check on user input

## Hot Files
| File | Changes | Bug Fixes | Reverts | Churn Score |
|------|---------|-----------|---------|-------------|
| `src/auth.py` | 20 | 5 | 2 | 45 |
```

## Git Hooks

```bash
sentinel watch              # Install pre-commit + post-commit hooks
sentinel watch --uninstall  # Remove hooks
```

- **pre-commit**: Runs `sentinel hunt` on staged files
- **post-commit**: Runs `sentinel swarm` to learn from the new commit

## Knowledge Types

| Type | Source | What It Captures |
|------|--------|------------------|
| **Conventions** | Naming patterns, import styles | How code _should_ look |
| **Decisions** | Commit messages with "because" | Why things are done a certain way |
| **Pitfalls** | Reverts, bug fixes | Mistakes to avoid |
| **Patterns** | Recurring AST structures | Common code idioms |
| **Hot Files** | Change frequency, bug fixes | Files needing extra scrutiny |
| **Co-Changes** | Files in same commits | What else to update |

## Development

```bash
git clone https://github.com/evo-hydra/sentinel.git
cd sentinel
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp]"

# Test
pytest --cov

# Lint + type check
ruff check src/ tests/
mypy src/sentinel/ --ignore-missing-imports
```

## License

MIT
