"""Console entry point. Hooks take a fast path that skips the Typer/Rich CLI start-up,
because Antigravity spawns them on every matching tool call."""

import contextlib
import sys


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # never crash on non-UTF-8 consoles (e.g. cp1252 pipes)
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    if len(sys.argv) == 3 and sys.argv[1] == "hook":
        from agflow.hooks import HANDLERS, run_hook

        if sys.argv[2] in HANDLERS:
            sys.stdout.write(run_hook(sys.argv[2]) + "\n")
            return
    from agflow.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
