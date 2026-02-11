"""sentinel hunt — scan files for issues against knowledge base."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from sentinel.cli import theme
from sentinel.cli.output import emit
from sentinel.models.enums import Severity

if TYPE_CHECKING:
    from sentinel.core.background import LLMRunStatus
    from sentinel.core.config import SentinelConfig
    from sentinel.core.knowledge import KnowledgeStore


def hunt(
    paths: list[Path] = typer.Argument(None, help="Files or directories to scan."),
    severity: str | None = typer.Option(None, "--severity", "-s",
                                           help="Minimum severity to show (critical/high/medium/low/info)."),
    fail_on: str | None = typer.Option(None, "--fail-on",
                                          help="Exit 1 if findings at this severity or above."),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output JSON."),
    verbose: bool = typer.Option(False, "--verbose", help="Show extra details."),
    llm: bool = typer.Option(False, "--llm", help="Enable LLM-powered code review (synchronous)."),
    llm_bg: bool = typer.Option(False, "--llm-bg", help="Run LLM review in background, return AST results immediately."),
    llm_workers: int | None = typer.Option(None, "--llm-workers", help="Number of concurrent LLM workers."),
    llm_status: bool = typer.Option(False, "--llm-status", help="Show status of latest background LLM run."),
    llm_results: bool = typer.Option(False, "--llm-results", help="Show findings from latest background LLM run."),
    run_id: str | None = typer.Option(None, "--run-id", help="Target a specific background run ID."),
    llm_provider: str | None = typer.Option(None, "--provider",
                                               help="LLM provider: ollama or anthropic."),
    llm_model: str | None = typer.Option(None, "--model", "-m",
                                            help="LLM model name (e.g. deepseek-coder:6.7b-instruct-q4_K_M)."),
) -> None:
    """Scan files for issues against the project knowledge base."""
    # Handle status/results queries — no file scanning needed
    if llm_status or llm_results:
        _handle_llm_query(llm_status, llm_results, run_id, json_output, verbose)
        return

    # Require paths for scanning
    if not paths:
        theme.error("No paths provided. Usage: sentinel hunt <path> [options]")
        raise typer.Exit(1)

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

    _valid_severities = {s.value for s in Severity}
    config = SentinelConfig.load(sentinel_dir)
    if severity:
        if severity.lower() not in _valid_severities:
            theme.error(f"Invalid severity: {severity!r}. Must be one of: {', '.join(sorted(_valid_severities))}")
            raise typer.Exit(1)
        config.min_severity = Severity(severity.lower())
    if fail_on:
        if fail_on.lower() not in _valid_severities:
            theme.error(f"Invalid --fail-on: {fail_on!r}. Must be one of: {', '.join(sorted(_valid_severities))}")
            raise typer.Exit(1)
        config.fail_on = Severity(fail_on.lower())
    if llm_provider:
        config.llm_provider = llm_provider
    if llm_model:
        config.llm_model = llm_model
    if llm_workers is not None:
        config.llm_workers = llm_workers

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

        # LLM verification
        if llm:
            # Synchronous LLM review (now concurrent via ConcurrentLLMVerifier)
            llm_findings = _run_llm_verification(store, config, file_list, git_root, json_output)
            report.findings.extend(llm_findings)
        elif llm_bg:
            # Background LLM review — launch detached, return AST results immediately
            _launch_background_llm(sentinel_dir, config, file_list, git_root, json_output)

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


def _run_llm_verification(
    store: KnowledgeStore,
    config: SentinelConfig,
    file_list: list[Path],
    git_root: Path,
    json_output: bool,
) -> list:
    """Run synchronous LLM verification with concurrent workers."""
    from sentinel.core.concurrent_verifier import ConcurrentLLMVerifier
    from sentinel.core.context import ContextEngine
    from sentinel.core.llm_provider import LLMProviderError
    from sentinel.core.llm_verifier import LLMVerifier
    from sentinel.core.provider_factory import create_provider
    from sentinel.models.findings import Finding

    try:
        provider = create_provider(config)
    except LLMProviderError as exc:
        theme.error(str(exc))
        return []

    if not json_output:
        theme.info(f"Using LLM provider: [bold]{config.llm_provider}[/] ({config.llm_model})")
        theme.info(f"Workers: {config.llm_workers}")

    context_engine = ContextEngine(store)
    verifier = LLMVerifier(context_engine, provider, config)
    concurrent = ConcurrentLLMVerifier(verifier, max_workers=config.llm_workers)

    findings: list[Finding] = []

    if json_output:
        findings = concurrent.verify_files(file_list, git_root)
    else:
        from rich.progress import Progress, SpinnerColumn, TextColumn

        with Progress(
            SpinnerColumn(),
            TextColumn("[sentinel.info]LLM review:[/] {task.description}"),
            console=theme.console,
            transient=True,
        ) as progress:
            task = progress.add_task("starting...", total=len(file_list))

            def on_progress(rel_path: str, done: int, total: int) -> None:
                progress.update(task, description=rel_path, completed=done)

            findings = concurrent.verify_files(file_list, git_root, on_progress=on_progress)

    return findings


def _launch_background_llm(
    sentinel_dir: Path,
    config: SentinelConfig,
    file_list: list[Path],
    git_root: Path,
    json_output: bool,
) -> None:
    """Launch LLM verification as a background process."""
    from sentinel.core.background import launch_background

    run_id = launch_background(
        sentinel_dir=sentinel_dir,
        file_paths=file_list,
        provider=config.llm_provider,
        model=config.llm_model,
        workers=config.llm_workers,
        git_root=git_root,
    )

    if json_output:
        emit({"llm_background": True, "run_id": run_id}, json_mode=True)
    else:
        theme.info(f"LLM review launched in background (run: [bold]{run_id}[/])")
        theme.muted("  Check status:  sentinel hunt --llm-status")
        theme.muted("  View results:  sentinel hunt --llm-results")


def _handle_llm_query(
    show_status: bool,
    show_results: bool,
    run_id: str | None,
    json_output: bool,
    verbose: bool,
) -> None:
    """Handle --llm-status and --llm-results queries."""
    from sentinel.core.background import get_findings, get_latest_run, get_run
    from sentinel.core.git import find_git_root

    git_root = find_git_root(Path.cwd())
    if git_root is None:
        theme.error("Not in a git repository.")
        raise typer.Exit(1)

    sentinel_dir = git_root / ".sentinel"
    if not sentinel_dir.exists():
        theme.error("Sentinel not initialized. Run: sentinel init")
        raise typer.Exit(1)

    # Get the target run
    status = get_run(sentinel_dir, run_id) if run_id else get_latest_run(sentinel_dir)

    if status is None:
        theme.warn("No background LLM runs found.")
        raise typer.Exit(0)

    if show_status:
        if json_output:
            emit(status.to_dict(), json_mode=True)
        else:
            _print_status(status)

    if show_results:
        if status.status not in ("complete", "failed"):
            if not json_output:
                theme.warn(f"Run {status.run_id} is still {status.status}.")
                _print_status(status)
            else:
                emit({"status": status.status, "run_id": status.run_id}, json_mode=True)
            return

        findings = get_findings(sentinel_dir, status.run_id)
        if json_output:
            emit([f.to_dict() for f in findings], json_mode=True)
        else:
            if not findings:
                theme.success(f"Run {status.run_id}: No LLM findings.")
            else:
                theme.info(f"Run [bold]{status.run_id}[/]: {len(findings)} LLM findings\n")
                for finding in findings:
                    sev_style = theme.severity_style(finding.severity.value)
                    loc = finding.file_path
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


def _print_status(status: LLMRunStatus) -> None:
    """Print human-readable run status."""
    style = {
        "pending": "sentinel.muted",
        "running": "sentinel.info",
        "complete": "sentinel.ok",
        "failed": "sentinel.error",
    }.get(status.status, "sentinel.muted")

    theme.console.print(f"  Run:      [bold]{status.run_id}[/]")
    theme.console.print(f"  Status:   [{style}]{status.status}[/]")
    theme.console.print(f"  Provider: {status.provider} ({status.model})")
    theme.console.print(f"  Progress: {status.files_done}/{status.files_total} files")
    if status.findings_count:
        theme.console.print(f"  Findings: {status.findings_count}")
    if status.error:
        theme.console.print(f"  Error:    [sentinel.error]{status.error}[/]")
    if status.pid:
        theme.console.print(f"  PID:      {status.pid}")
