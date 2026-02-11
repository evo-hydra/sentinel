"""sentinel hunt — scan files for issues against knowledge base."""

from __future__ import annotations

from pathlib import Path

import typer

from sentinel.cli import theme
from sentinel.cli.output import emit
from sentinel.models.enums import Severity


def hunt(
    paths: list[Path] = typer.Argument(..., help="Files or directories to scan."),
    severity: str | None = typer.Option(None, "--severity", "-s",
                                           help="Minimum severity to show (critical/high/medium/low/info)."),
    fail_on: str | None = typer.Option(None, "--fail-on",
                                          help="Exit 1 if findings at this severity or above."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output JSON."),
    verbose: bool = typer.Option(False, "--verbose", help="Show extra details."),
) -> None:
    """Scan files for issues against the project knowledge base."""
    from sentinel.core.config import SentinelConfig
    from sentinel.core.git import find_git_root
    from sentinel.core.knowledge import KnowledgeStore
    from sentinel.core.verifier import VerificationEngine

    # Derive git root from provided paths, fall back to cwd
    start = paths[0].resolve() if paths else Path.cwd()
    git_root = find_git_root(start)
    if git_root is None:
        theme.error("Not in a git repository.")
        raise typer.Exit(1)

    sentinel_dir = git_root / ".sentinel"
    if not sentinel_dir.exists():
        theme.error("Sentinel not initialized. Run: sentinel init")
        raise typer.Exit(1)

    config = SentinelConfig.load(sentinel_dir)
    if severity:
        config.min_severity = Severity(severity)
    if fail_on:
        config.fail_on = Severity(fail_on)

    # Resolve file list
    file_list: list[Path] = []
    for p in paths:
        p = p.resolve()
        if p.is_file():
            if config.should_scan(p):
                file_list.append(p)
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and config.should_scan(f):
                    file_list.append(f)

    if not file_list:
        theme.warn("No scannable files found.")
        raise typer.Exit(0)

    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    with store:
        engine = VerificationEngine(store, config)
        report = engine.verify_files(file_list, git_root)

    # Filter by severity
    sev_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
    min_idx = sev_order.index(config.min_severity)
    report.findings = [f for f in report.findings if sev_order.index(f.severity) <= min_idx]

    if json_output:
        emit(report.to_dict(), json_mode=True)
    else:
        if not report.findings:
            theme.success(f"Clean scan. {report.files_scanned} files, 0 findings.")
        else:
            theme.banner()
            theme.info(f"Scanned {report.files_scanned} files, found {report.total} issues:\n")

            for finding in report.findings:
                sev_style = theme.severity_style(finding.severity.value)
                loc = f"{finding.file_path}"
                if finding.line:
                    loc += f":{finding.line}"
                theme.console.print(
                    f"  [{sev_style}]{finding.severity.value.upper():8}[/] "
                    f"[bold]{loc}[/]"
                )
                theme.console.print(f"           {finding.message}")
                if finding.suggestion and verbose:
                    theme.console.print(f"           [sentinel.muted]→ {finding.suggestion}[/]")
                theme.console.print()

            counts = report.count_by_severity()
            summary_parts = [f"{k}: {v}" for k, v in counts.items()]
            theme.muted(f"  Summary: {', '.join(summary_parts)}")

    # Exit code
    if config.fail_on and report.findings:
        fail_idx = sev_order.index(config.fail_on)
        if any(sev_order.index(f.severity) <= fail_idx for f in report.findings):
            raise typer.Exit(1)
