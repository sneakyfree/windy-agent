"""Tests for the unified command registry — registry, core, ecosystem.

Covers:
  - Registry loading (117 commands, aliases, categories)
  - Command execution (sync + async)
  - Dangerous command gating
  - Help formatting per platform
  - BotFather generation
  - Alias resolution
  - Edge cases (unknown commands, empty input)
"""

from __future__ import annotations

import asyncio
import pytest

from windyfly.commands.registry import CommandRegistry, Command, is_command, parse_command


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def fresh_registry():
    """A fresh empty registry (not the global singleton)."""
    return CommandRegistry()


@pytest.fixture(scope="module")
def loaded_registry():
    """Load the full 117-command registry once for the module."""
    from windyfly.commands.setup import init_all_commands
    from windyfly.commands.registry import registry
    init_all_commands()
    return registry


# ═══════════════════════════════════════════════════════════════════════
# Registry structure
# ═══════════════════════════════════════════════════════════════════════


class TestRegistryLoading:
    def test_total_command_count(self, loaded_registry):
        """Should have at least 100 unique commands."""
        total = len(loaded_registry._commands)
        assert total >= 100, f"Expected 100+ commands, got {total}"

    def test_core_count(self, loaded_registry):
        core, eco = loaded_registry.count()
        assert core >= 80, f"Expected 80+ core commands, got {core}"

    def test_ecosystem_count(self, loaded_registry):
        core, eco = loaded_registry.count()
        assert eco >= 25, f"Expected 25+ ecosystem commands, got {eco}"

    def test_aliases_registered(self, loaded_registry):
        assert len(loaded_registry._aliases) >= 40, "Expected 40+ aliases"

    def test_categories_exist(self, loaded_registry):
        cats = loaded_registry.by_category()
        assert len(cats) >= 15, f"Expected 15+ categories, got {len(cats)}"

    def test_all_commands_have_handlers(self, loaded_registry):
        for cmd in loaded_registry.all():
            assert cmd.handler is not None, f"Command {cmd.name} has no handler"
            assert callable(cmd.handler), f"Command {cmd.name} handler not callable"

    def test_all_commands_have_descriptions(self, loaded_registry):
        for cmd in loaded_registry.all():
            assert cmd.description, f"Command {cmd.name} has no description"
            assert len(cmd.description) > 5, f"Command {cmd.name} description too short"


# ═══════════════════════════════════════════════════════════════════════
# Command lookup
# ═══════════════════════════════════════════════════════════════════════


class TestCommandLookup:
    def test_get_by_name(self, loaded_registry):
        assert loaded_registry.get("doctor") is not None
        assert loaded_registry.get("help") is not None
        assert loaded_registry.get("version") is not None

    def test_get_by_alias(self, loaded_registry):
        cmd = loaded_registry.get("ver")
        assert cmd is not None
        assert cmd.name == "version"

    def test_get_strips_prefix(self, loaded_registry):
        assert loaded_registry.get("/doctor") is not None
        assert loaded_registry.get("!doctor") is not None

    def test_get_unknown_returns_none(self, loaded_registry):
        assert loaded_registry.get("nonexistent_command_xyz") is None

    def test_ecosystem_marked(self, loaded_registry):
        inbox = loaded_registry.get("inbox")
        assert inbox is not None
        assert inbox.ecosystem_only is True

    def test_core_not_ecosystem(self, loaded_registry):
        doctor = loaded_registry.get("doctor")
        assert doctor is not None
        assert doctor.ecosystem_only is False


# ═══════════════════════════════════════════════════════════════════════
# Command execution
# ═══════════════════════════════════════════════════════════════════════


class TestCommandExecution:
    def test_version_returns_string(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("version")
        )
        assert "Windy Fly" in result
        assert "0.5.1" in result

    def test_ping_returns_pong(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("ping")
        )
        assert "Pong" in result

    def test_whoami_returns_identity(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("whoami")
        )
        assert "I am" in result

    def test_models_returns_providers(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("models")
        )
        assert "OpenAI" in result
        assert "Anthropic" in result

    def test_channels_returns_list(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("channels")
        )
        assert "CLI" in result
        assert "Telegram" in result

    def test_unknown_command(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("totally_unknown_xyz")
        )
        assert "Unknown command" in result

    def test_empty_input(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("")
        )
        assert "help" in result.lower()

    def test_command_with_args(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("help version")
        )
        assert "version" in result.lower()

    def test_presets_returns_list(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("presets")
        )
        assert "buddy" in result
        assert "engineer" in result


# ═══════════════════════════════════════════════════════════════════════
# Dangerous command gating
# ═══════════════════════════════════════════════════════════════════════


class TestDangerousCommands:
    def test_kill_blocked_without_confirm(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("kill")
        )
        assert "dangerous" in result.lower() or "confirm" in result.lower()

    def test_kill_allowed_with_confirm(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("kill --confirm")
        )
        # Should NOT be the gating message
        assert "dangerous" not in result.lower() or "confirm" not in result

    def test_factory_reset_blocked(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("factory-reset", {"platform": "terminal"})
        )
        assert "dangerous" in result.lower() or "confirm" in result.lower()

    def test_forget_blocked(self, loaded_registry):
        result = asyncio.run(
            loaded_registry.execute("forget")
        )
        assert "dangerous" in result.lower() or "confirm" in result.lower()

    def test_doctor_not_blocked(self, loaded_registry):
        """Non-dangerous commands should execute without confirmation."""
        result = asyncio.run(
            loaded_registry.execute("doctor")
        )
        assert "confirm" not in result.lower()


# ═══════════════════════════════════════════════════════════════════════
# Help formatting
# ═══════════════════════════════════════════════════════════════════════


class TestHelpFormatting:
    def test_terminal_prefix(self, loaded_registry):
        help_text = loaded_registry.format_help("terminal")
        assert "windy " in help_text

    def test_telegram_prefix(self, loaded_registry):
        help_text = loaded_registry.format_help("telegram")
        assert "/doctor" in help_text or "/version" in help_text

    def test_matrix_prefix(self, loaded_registry):
        help_text = loaded_registry.format_help("matrix")
        assert "!doctor" in help_text or "!version" in help_text

    def test_ecosystem_marker(self, loaded_registry):
        help_text = loaded_registry.format_help("terminal")
        assert "⚡" in help_text

    def test_help_mentions_hfly(self, loaded_registry):
        help_text = loaded_registry.format_help("terminal")
        assert "HiFly" in help_text


# ═══════════════════════════════════════════════════════════════════════
# is_command / parse_command utilities
# ═══════════════════════════════════════════════════════════════════════


class TestCommandDetection:
    def test_slash_command(self):
        assert is_command("/doctor") is True

    def test_bang_command(self):
        assert is_command("!doctor") is True

    def test_regular_text(self):
        assert is_command("hello") is False

    def test_empty_string(self):
        assert is_command("") is False

    def test_just_slash(self):
        assert is_command("/") is False

    def test_slash_number(self):
        assert is_command("/123") is False

    def test_parse_command_strips_prefix(self):
        assert parse_command("/doctor") == "doctor"
        assert parse_command("!help") == "help"
        assert parse_command("/model set gpt-4o") == "model set gpt-4o"


# ═══════════════════════════════════════════════════════════════════════
# Fresh registry (isolated tests)
# ═══════════════════════════════════════════════════════════════════════


class TestFreshRegistry:
    def test_register_and_get(self, fresh_registry):
        async def handler(ctx):
            return "ok"

        cmd = Command(
            name="test-cmd", description="A test", category="test",
            handler=handler,
        )
        fresh_registry.register(cmd)
        assert fresh_registry.get("test-cmd") is not None
        assert fresh_registry.get("test-cmd").name == "test-cmd"

    def test_alias_resolution(self, fresh_registry):
        async def handler(ctx):
            return "ok"

        cmd = Command(
            name="greet", description="Say hi", category="test",
            handler=handler, aliases=["hello", "hi"],
        )
        fresh_registry.register(cmd)
        assert fresh_registry.get("hello").name == "greet"
        assert fresh_registry.get("hi").name == "greet"

    def test_count_empty(self, fresh_registry):
        core, eco = fresh_registry.count()
        assert core == 0
        assert eco == 0

    def test_execute_unknown(self, fresh_registry):
        result = asyncio.run(
            fresh_registry.execute("nothing")
        )
        assert "Unknown command" in result


# ═══════════════════════════════════════════════════════════════════════
# BotFather generation
# ═══════════════════════════════════════════════════════════════════════


class TestBotFather:
    def test_generates_output(self, loaded_registry):
        from windyfly.commands.botfather import generate_botfather_commands
        output = generate_botfather_commands()
        lines = output.strip().split("\n")
        assert len(lines) >= 50, f"Expected 50+ lines, got {len(lines)}"
        assert len(lines) <= 100, "BotFather max is 100 commands"

    def test_format_is_correct(self, loaded_registry):
        from windyfly.commands.botfather import generate_botfather_commands
        output = generate_botfather_commands()
        for line in output.strip().split("\n"):
            assert " - " in line, f"Line missing ' - ' separator: {line}"
