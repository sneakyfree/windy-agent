"""Boot sequence abstraction — the Wave 14 kernel primitive.

Captures the canonical order in which Windy Fly registers tools,
capabilities, and audit hooks, so every channel branch in ``main.py``
runs the *same* sequence rather than maintaining its own ad-hoc list.

The bug this exists to prevent
------------------------------

Before Wave 14, ``main.py`` had two parallel procedural sequences in
its ``--channel matrix`` and ``--channel telegram`` arms. The two
drifted: capability registrations were missing in the telegram branch
but present in matrix, so the bot ran but with a smaller tool surface.
Silent failure — Grant only noticed when an LLM tool call failed
because the capability wasn't registered. The cause was structural:
two ad-hoc lists are guaranteed to drift over time; one shared list
isn't.

This module makes the canonical sequence a first-class artifact:
``default_capability_registration_sequence()`` returns the steps
every channel needs, both channels invoke it through one entrypoint
(``BootSequence.run``), and missing/added steps are visible in one
place.

Future extension
----------------

The sequence is data, not code. Each ``Step`` is a name + runner +
flags. Future enhancements (manifest-driven capabilities, plugin
loader, multi-tenant boot) can compose richer sequences without
changing the runner. This is the seam through which Wave 15+ adds
new capability classes.

Usage
-----

    from windyfly.agent.boot import (
        BootContext, BootSequence, default_capability_registration_sequence,
    )
    ctx = BootContext(config, db, write_queue, tool_registry,
                      capability_registry)
    BootSequence(default_capability_registration_sequence()).run(ctx)

Add a new capability registration: append one Step in
``default_capability_registration_sequence``. Both channel branches
benefit immediately.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class BootContext:
    """Mutable container passed to every Step.

    Holds the long-lived runtime objects (db, write queue, registries,
    config) so steps can read and update them without parameter-threading.
    Also exposes a free-form ``state`` dict for steps that need to stash
    something for a later step (e.g., the reminder checker thread handle).
    """

    config: dict[str, Any]
    db: Any
    write_queue: Any
    tool_registry: Any
    capability_registry: Any
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class Step:
    """One unit of boot work.

    ``name`` is the stable id used in logs (``capabilities.shell``,
    ``tools.weather``, etc.). ``runner`` takes the ``BootContext`` and
    returns nothing — a successful run is the absence of a raise.

    ``optional`` defaults to False. When True, a raised exception is
    logged and the boot continues; otherwise the boot aborts. Use for
    nice-to-have steps (e.g., starting the reminder background checker
    when reminders aren't critical to bot health).

    ``requires`` declares prerequisite step names. If a required step
    didn't run successfully (or didn't run at all), the BootSequence
    raises ``BootDependencyError`` before invoking this step. Use to
    catch ordering mistakes loudly.
    """

    name: str
    runner: Callable[[BootContext], None]
    optional: bool = False
    requires: tuple[str, ...] = ()


class BootDependencyError(RuntimeError):
    """Raised when a Step's required prerequisite hasn't completed."""


class BootError(RuntimeError):
    """Raised when a required Step fails. Wraps the original exception."""


class BootSequence:
    """Run a list of Steps in order, with logging, timing, and dep checks.

    Single entry point: ``run(ctx)``. Logs each step's start and end
    with elapsed milliseconds. Tracks completed step names so dependency
    checks can verify a required predecessor actually ran.
    """

    def __init__(self, steps: list[Step]) -> None:
        self.steps = steps

    def run(self, ctx: BootContext) -> dict[str, Any]:
        """Execute every step in declared order. Return a summary dict.

        Aborts (via ``BootError``) on the first required-step failure.
        Optional-step failures are logged and skipped.

        Returns ``{"completed": [...], "skipped": [...], "failed": [...]}``
        — handy for tests and for the boot-summary log line.
        """
        completed: list[str] = []
        skipped: list[str] = []
        failed: list[tuple[str, str]] = []

        boot_start = time.monotonic()
        logger.info("[boot] starting %d-step sequence", len(self.steps))

        for step in self.steps:
            for dep in step.requires:
                if dep not in completed:
                    raise BootDependencyError(
                        f"step {step.name!r} requires {dep!r} which has "
                        f"not completed (completed so far: {completed})"
                    )

            t0 = time.monotonic()
            try:
                step.runner(ctx)
            except Exception as e:
                ms = int((time.monotonic() - t0) * 1000)
                if step.optional:
                    logger.warning(
                        "[boot] step %s FAILED (optional, continuing) "
                        "after %dms: %s", step.name, ms, e,
                    )
                    skipped.append(step.name)
                    failed.append((step.name, str(e)))
                    continue
                logger.error(
                    "[boot] step %s FAILED after %dms: %s — aborting boot",
                    step.name, ms, e,
                )
                raise BootError(f"required step {step.name!r} failed: {e}") from e
            ms = int((time.monotonic() - t0) * 1000)
            logger.info("[boot] step %s OK (%dms)", step.name, ms)
            completed.append(step.name)

        total_ms = int((time.monotonic() - boot_start) * 1000)
        logger.info(
            "[boot] complete in %dms — %d ok, %d skipped",
            total_ms, len(completed), len(skipped),
        )
        return {
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
            "total_ms": total_ms,
        }


# ─── Step runners ────────────────────────────────────────────────────


def _step_register_windy_api(ctx: BootContext) -> None:
    from windyfly.tools.windy_api import register_windy_tools
    register_windy_tools(ctx.tool_registry)


def _step_register_mail(ctx: BootContext) -> None:
    from windyfly.tools.mail import register_mail_tools
    register_mail_tools(ctx.tool_registry)


def _step_register_chat(ctx: BootContext) -> None:
    from windyfly.tools.chat import register_chat_tools
    register_chat_tools(ctx.tool_registry)


def _step_register_sms(ctx: BootContext) -> None:
    from windyfly.tools.sms import register_sms_tools
    register_sms_tools(ctx.tool_registry)


def _step_register_voice(ctx: BootContext) -> None:
    from windyfly.tools.voice import register_voice_tools
    register_voice_tools(ctx.tool_registry)


def _step_register_cloud(ctx: BootContext) -> None:
    from windyfly.tools.cloud import register_cloud_tools
    register_cloud_tools(ctx.tool_registry)


def _step_register_web_search(ctx: BootContext) -> None:
    from windyfly.tools.web_search import register_web_search_tool
    register_web_search_tool(ctx.tool_registry)


def _step_register_reminders(ctx: BootContext) -> None:
    from windyfly.tools.reminders import register_reminder_tools
    register_reminder_tools(ctx.tool_registry, ctx.db)


def _step_register_todos(ctx: BootContext) -> None:
    from windyfly.tools.todos import register_todo_tools
    register_todo_tools(ctx.tool_registry, ctx.db)


def _step_register_weather(ctx: BootContext) -> None:
    from windyfly.tools.weather import register_weather_tool
    register_weather_tool(ctx.tool_registry)


def _step_register_news(ctx: BootContext) -> None:
    from windyfly.tools.news import register_news_tool
    register_news_tool(ctx.tool_registry)


def _step_register_calendar(ctx: BootContext) -> None:
    from windyfly.tools.calendar import register_calendar_tools
    register_calendar_tools(ctx.tool_registry)


def _step_register_utilities(ctx: BootContext) -> None:
    from windyfly.tools.utilities import register_utility_tools
    register_utility_tools(ctx.tool_registry)


def _step_start_reminder_checker(ctx: BootContext) -> None:
    from windyfly.tools.reminders import start_reminder_checker
    start_reminder_checker(ctx.db)


def _step_register_sub_agents(ctx: BootContext) -> None:
    from windyfly.agent.sub_agents import register_sub_agent_tool
    register_sub_agent_tool(
        ctx.tool_registry, ctx.config, ctx.db, ctx.write_queue,
    )


def _step_install_audit_hooks(ctx: BootContext) -> None:
    from windyfly.agent.capabilities import install_audit_hooks
    install_audit_hooks(ctx.capability_registry, ctx.db, ctx.write_queue)


def _step_register_filesystem(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.filesystem import (
        register_filesystem_capabilities,
    )
    register_filesystem_capabilities(ctx.capability_registry, ctx.config)


def _step_register_shell(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.shell import register_shell_capabilities
    register_shell_capabilities(ctx.capability_registry, ctx.config)


def _step_register_ssh(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.ssh import register_ssh_capabilities
    register_ssh_capabilities(ctx.capability_registry, ctx.config)


def _step_register_collaborators(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.collaborators import (
        register_collaborator_capabilities,
    )
    register_collaborator_capabilities(
        ctx.capability_registry, ctx.db, ctx.write_queue, ctx.config,
    )


def _step_register_github(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.github import (
        register_github_capabilities,
    )
    register_github_capabilities(ctx.capability_registry, ctx.config)


def _step_register_email(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.email import (
        register_email_capabilities,
    )
    register_email_capabilities(ctx.capability_registry, ctx.config)


def _step_register_cloudflare(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.cloudflare import (
        register_cloudflare_capabilities,
    )
    register_cloudflare_capabilities(ctx.capability_registry, ctx.config)


def _step_register_setup(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.setup import register_setup_capabilities
    register_setup_capabilities(ctx.capability_registry, ctx.config)


def _step_register_health(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.health import register_health_capabilities
    register_health_capabilities(ctx.capability_registry, ctx.config)


def _step_register_auto_repair(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.auto_repair import (
        register_auto_repair_capabilities,
    )
    register_auto_repair_capabilities(ctx.capability_registry, ctx.config)


def _step_register_fleet(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.fleet import register_fleet_capabilities
    register_fleet_capabilities(ctx.capability_registry, ctx.config)


def default_capability_registration_sequence() -> list[Step]:
    """The canonical post-DB-open registration order for both channels.

    Add a new capability/tool here once, both telegram and matrix
    branches benefit. Order matters: tools register before capability
    audit hooks (so audit hooks see all subsequent capability
    registrations), and capability handlers (fs/shell/collaborators)
    register after audit hooks so their invocations are auditable.
    """
    return [
        # Legacy ToolRegistry first — these are pre-Capability-Plane tools
        # (weather, news, calendar, etc.) the loop still routes through.
        Step("tools.windy_api",      _step_register_windy_api),
        Step("tools.mail",           _step_register_mail),
        Step("tools.chat",           _step_register_chat),
        Step("tools.sms",            _step_register_sms),
        Step("tools.voice",          _step_register_voice),
        Step("tools.cloud",          _step_register_cloud),
        Step("tools.web_search",     _step_register_web_search),
        Step("tools.reminders",      _step_register_reminders),
        Step("tools.todos",          _step_register_todos),
        Step("tools.weather",        _step_register_weather),
        Step("tools.news",           _step_register_news),
        Step("tools.calendar",       _step_register_calendar),
        Step("tools.utilities",      _step_register_utilities),
        Step(
            "tools.reminder_checker",
            _step_start_reminder_checker,
            optional=True,  # background thread; failure is non-fatal
        ),
        Step("tools.sub_agents",     _step_register_sub_agents),

        # Capability Plane: install audit hooks BEFORE registering caps
        # so every subsequent registration is auditable from the first
        # invocation.
        Step("capabilities.audit",   _step_install_audit_hooks),
        Step(
            "capabilities.filesystem",
            _step_register_filesystem,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.shell",
            _step_register_shell,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.ssh",
            _step_register_ssh,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.collaborators",
            _step_register_collaborators,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.github",
            _step_register_github,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.email",
            _step_register_email,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.cloudflare",
            _step_register_cloudflare,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.setup",
            _step_register_setup,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.health",
            _step_register_health,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.auto_repair",
            _step_register_auto_repair,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.fleet",
            _step_register_fleet,
            requires=("capabilities.audit",),
        ),
    ]
