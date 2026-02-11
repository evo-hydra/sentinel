# Sentinel

**Persistent project intelligence & AI code quality gate.**

Sentinel learns your codebase from git history and verifies code against that knowledge. It hunts bugs in the tunnels.

## Quick Start

```bash
pip install -e ".[dev]"

# Initialize in your project
cd your-project
sentinel init

# Hunt for issues
sentinel hunt src/

# Learn from more history
sentinel swarm --full

# Manage knowledge
sentinel hive list
sentinel hive add pitfall "Never use eval() on user input" --severity critical
sentinel hive search "auth"

# Install git hooks
sentinel watch
```

## Commands

| Command | Purpose |
|---------|---------|
| `sentinel init [path]` | Initialize Sentinel, learn from git history |
| `sentinel hunt <paths>` | Scan files for issues against knowledge |
| `sentinel swarm` | Incremental learning from new git history |
| `sentinel hive list` | List knowledge entries |
| `sentinel hive add <type> <desc>` | Add manual knowledge |
| `sentinel hive search <query>` | Full-text search knowledge |
| `sentinel watch` | Install/uninstall git hooks |

## How It Works

1. **`sentinel init`** — Scans your git history to learn conventions, architectural decisions, common pitfalls, hot files, and co-change patterns.
2. **`sentinel hunt`** — Verifies code against learned knowledge using AST analysis, pattern matching, and churn metrics.
3. **`sentinel swarm`** — Incrementally learns from new commits since the last scan.
4. **`sentinel watch`** — Installs git hooks so every commit is automatically checked.

## Knowledge Types

- **Conventions** — Naming, structure, commit message patterns
- **Decisions** — Architectural decisions extracted from commit messages
- **Pitfalls** — Known mistakes from reverts and bug fixes
- **Patterns** — Recurring code patterns
- **Hot Files** — Files with high churn that need extra scrutiny
- **Co-Changes** — Files that usually change together

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest --cov
ruff check src/ tests/
mypy src/sentinel/
```

## License

MIT
