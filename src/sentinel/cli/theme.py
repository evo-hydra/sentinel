"""Matrix-themed Rich output for Sentinel CLI."""

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.theme import Theme

SENTINEL_THEME = Theme({
    "sentinel.ok": "bold green",
    "sentinel.warn": "bold yellow",
    "sentinel.error": "bold red",
    "sentinel.info": "bold cyan",
    "sentinel.muted": "dim white",
    "sentinel.matrix": "bold green",
    "sentinel.accent": "bold bright_green",
    "sentinel.critical": "bold red reverse",
    "sentinel.high": "bold red",
    "sentinel.medium": "bold yellow",
    "sentinel.low": "bold cyan",
})

console = Console(theme=SENTINEL_THEME)


def banner() -> None:
    """Print Sentinel startup banner."""
    text = Text()
    text.append("╔═══════════════════════════════════════╗\n", style="sentinel.matrix")
    text.append("║  ", style="sentinel.matrix")
    text.append("SENTINEL", style="bold bright_green")
    text.append("  —  ", style="sentinel.muted")
    text.append("hunting bugs in the tunnels", style="green")
    text.append("  ║\n", style="sentinel.matrix")
    text.append("╚═══════════════════════════════════════╝", style="sentinel.matrix")
    console.print(text)


def success(msg: str) -> None:
    """Print success message."""
    console.print(f"[sentinel.ok]✓[/] {msg}")


def warn(msg: str) -> None:
    """Print warning message."""
    console.print(f"[sentinel.warn]⚠[/] {msg}")


def error(msg: str) -> None:
    """Print error message."""
    console.print(f"[sentinel.error]✗[/] {msg}")


def info(msg: str) -> None:
    """Print info message."""
    console.print(f"[sentinel.info]→[/] {msg}")


def muted(msg: str) -> None:
    """Print muted/dim message."""
    console.print(f"[sentinel.muted]{msg}[/]")


def panel(title: str, content: str, style: str = "green") -> None:
    """Print a themed panel."""
    console.print(Panel(content, title=f"[bold]{title}[/]", border_style=style))


def severity_style(severity: str) -> str:
    """Get Rich style for a severity level."""
    return {
        "critical": "sentinel.critical",
        "high": "sentinel.high",
        "medium": "sentinel.medium",
        "low": "sentinel.low",
    }.get(severity, "sentinel.muted")
