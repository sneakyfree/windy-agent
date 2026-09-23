"""Terminal prompts that never crash when there's no terminal.

`windy go </dev/null` (a script, CI, `docker exec` without `-i`) used to die
with an ``EOFError`` traceback at the first ``Confirm.ask`` (0.7.2.1 PyPI
proof, 09-23). Every prompt on the setup paths has a stated default, so with
no one there to answer we take it and say so in one line.

Call sites keep passing the rich ``Confirm.ask`` / ``Prompt.ask`` they
import, so tests that patch ``module.Confirm`` / ``module.Prompt`` still
steer them.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T")


def ask(ask_fn: Callable[..., T], question: str, *, default: T, **kwargs: Any) -> T:
    """``ask_fn(question, default=default, **kwargs)``, or ``default`` on EOF."""
    try:
        return ask_fn(question, default=default, **kwargs)
    except EOFError:
        from rich.console import Console

        if default is True:
            shown = "yes"
        elif default is False:
            shown = "no"
        elif default in ("", None):
            shown = "skip"
        else:
            shown = str(default)
        # The prompt text is still on the line; finish it before the note.
        Console().print(f"\n  [dim](no terminal to answer — using the default: {shown})[/dim]")
        return default
