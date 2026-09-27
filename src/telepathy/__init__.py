"""Telepathy: your coding agent's memory, on every machine."""

__version__ = "0.1.0.dev0"


def main() -> None:
    from .cli import main as run
    run()
