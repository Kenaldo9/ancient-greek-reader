import os
import shlex
import readline

import click
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

console = Console()

# \001 / \002 wrap non-printing sequences so readline measures prompt width correctly
_PROMPT = "\001\033[1;33m\002greek\001\033[0m\002\001\033[33m\002›\001\033[0m\002 "
_HISTORY_FILE = os.path.join(
    os.environ.get("GREEK_DATA_DIR", "./data"), ".greek_history"
)


def _setup_readline() -> None:
    readline.parse_and_bind("tab: complete")
    try:
        readline.read_history_file(_HISTORY_FILE)
    except FileNotFoundError:
        pass


@click.command("shell")
@click.pass_context
def shell_cmd(ctx):
    """Start an interactive REPL session."""
    _setup_readline()

    group = ctx.parent.command
    available = sorted(name for name in group.commands if name != "shell")

    title = Text()
    title.append("ANCIENT GREEK READER", style="bold gold1")
    title.append("  ·  ", style="dim")
    title.append("Μῆνιν ἄειδε θεά", style="italic dim cyan")

    cmd_text = Text("  ".join(available), style="cyan")

    console.print()
    console.print(
        Panel(
            f"[dim]Commands:[/dim]  {cmd_text}\n\n"
            "[dim]Type [/dim][cyan]help[/cyan][dim] for usage, "
            "[/dim][cyan]exit[/cyan][dim] or Ctrl-D to quit.[/dim]",
            title=title,
            border_style="gold1",
            padding=(1, 2),
        )
    )
    console.print()

    try:
        while True:
            try:
                line = input(_PROMPT)
            except EOFError:
                console.print()
                console.print(Rule(style="gold1"))
                break
            except KeyboardInterrupt:
                console.print()
                continue

            line = line.strip()
            if not line:
                continue
            if line in ("exit", "quit"):
                console.print(Rule(style="gold1"))
                break
            if line in ("help", "?"):
                try:
                    group.main(["--help"], standalone_mode=False)
                except (SystemExit, click.exceptions.Exit):
                    pass
                continue

            try:
                args = shlex.split(line)
            except ValueError as e:
                console.print(f"[red]Parse error:[/red] {e}")
                continue

            if args and args[0] == "shell":
                console.print("[yellow]Already in a shell session.[/yellow]")
                continue

            try:
                group.main(args, standalone_mode=False)
            except SystemExit:
                pass
            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted.[/yellow]")
            except click.exceptions.UsageError as e:
                console.print(f"[red]Error:[/red] {e.format_message()}")
            except click.exceptions.Abort:
                console.print("[dim]Aborted.[/dim]")
            except click.exceptions.Exit:
                pass

    finally:
        readline.set_history_length(1000)
        try:
            readline.write_history_file(_HISTORY_FILE)
        except OSError:
            pass
