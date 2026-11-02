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


def _step_register_windy_plans(ctx: BootContext) -> None:
    from windyfly.tools.windy_plans import register_windy_plans_tool
    register_windy_plans_tool(ctx.tool_registry)


def _step_register_mail(ctx: BootContext) -> None:
    from windyfly.tools import outbound_identity
    from windyfly.tools.mail import register_mail_tools
    outbound_identity.set_config(ctx.config)
    register_mail_tools(ctx.tool_registry)


def _step_register_chat(ctx: BootContext) -> None:
    from windyfly.tools.chat import register_chat_tools
    register_chat_tools(ctx.tool_registry)


def _step_register_sms(ctx: BootContext) -> None:
    from windyfly.tools.sms import register_sms_tools
    register_sms_tools(ctx.tool_registry, ctx.db)


def _step_register_cloud(ctx: BootContext) -> None:
    from windyfly.tools.cloud import register_cloud_tools
    register_cloud_tools(ctx.tool_registry, config=ctx.config, db=ctx.db, write_queue=ctx.write_queue)


def _step_register_web_search(ctx: BootContext) -> None:
    from windyfly.tools.web_search import register_web_search_tool
    register_web_search_tool(ctx.tool_registry)


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


def _step_register_mind_model(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.mind_model import register_mind_model_capabilities
    register_mind_model_capabilities(ctx.capability_registry, ctx.config)


def _step_register_windy_calendar(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.windy_calendar import register_windy_calendar_capabilities
    register_windy_calendar_capabilities(ctx.capability_registry, ctx.config)


def _step_register_vault(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.vault import register_vault_capabilities
    register_vault_capabilities(ctx.capability_registry, ctx.config)


def _step_register_teams(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.teams import register_teams_capabilities
    register_teams_capabilities(ctx.capability_registry, ctx.config)


def _step_register_phone_tools(ctx: BootContext) -> None:
    from windyfly.agent.capabilities.phone import register_phone_capabilities
    register_phone_capabilities(ctx.capability_registry, ctx.config)


def _step_register_memory_search(ctx: BootContext) -> None:
    """Chronicle retrieval — the model's key to its own past
    (Chronicle Doctrine MUST-BUILD #2, 2026-07-18)."""
    from windyfly.agent.capabilities.memory_search import (
        register_memory_search_capabilities,
    )
    register_memory_search_capabilities(
        ctx.capability_registry, ctx.db, ctx.config,
    )


def _step_refresh_ept(ctx: BootContext) -> None:
    """Refresh the Eternitas passport token in the background.

    Runs on a daemon thread so a slow or unreachable Eternitas never delays
    boot; the result is logged by ``windyfly.eternitas.ept_refresh``."""
    from windyfly.eternitas.ept_refresh import refresh_in_background
    refresh_in_background()


def _step_agent_keys(ctx: BootContext) -> None:
    """Make sure the agent has an Eternitas signing key (agent-keys v1).

    Background thread, like the EPT refresh. Until Eternitas ships the
    agent-keys routes this logs one INFO line and does nothing else."""
    from windyfly.eternitas.agent_keys import ensure_in_background
    ensure_in_background()


def _step_housing_report(ctx: BootContext) -> None:
    """Tell Eternitas which machine the agent lives on (housing history; DARK until
    WINDY_HOUSING_REPORT=1). Background thread; a no-op (no thread, no network) when off."""
    from windyfly.eternitas.housing import report_in_background
    report_in_background()


def _step_record_tool_count(ctx: BootContext) -> None:
    from windyfly.observability.tool_count import record
    record(ctx.tool_registry, ctx.capability_registry)


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
        Step("tools.windy_plans",    _step_register_windy_plans),
        Step("tools.mail",           _step_register_mail),
        Step("tools.chat",           _step_register_chat),
        Step("tools.sms",            _step_register_sms),
        Step("tools.cloud",          _step_register_cloud),
        Step("tools.web_search",     _step_register_web_search),

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
            "capabilities.mind_model",
            _step_register_mind_model,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.windy_calendar",
            _step_register_windy_calendar,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.vault",
            _step_register_vault,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.teams",
            _step_register_teams,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.phone_tools",
            _step_register_phone_tools,
            requires=("capabilities.audit",),
        ),
        Step(
            "capabilities.memory_search",
            _step_register_memory_search,
            requires=("capabilities.audit",),
        ),
        Step(
            "eternitas.ept_refresh",
            _step_refresh_ept,
            optional=True,  # background thread; must never block boot
        ),
        Step(
            "eternitas.agent_keys",
            _step_agent_keys,
            optional=True,  # background thread; must never block boot
        ),
        Step(
            "eternitas.housing",
            _step_housing_report,
            optional=True,  # dark background thread; must never block boot
        ),
        # LAST, after every tool and capability is registered: the owner-turn tool count as a fact for the
        # journey probe (Windy Mind refuses > 128 tools).
        Step(
            "observability.tool_count",
            _step_record_tool_count,
            optional=True,  # a fact file; never blocks boot
        ),
    ]
