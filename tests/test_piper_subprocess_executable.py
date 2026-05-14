"""Regression: piper's fallback CLI must use sys.executable, not 'python'.

The Telegram channel uses voice.piper for outbound text-to-speech.
When the user opts into the [voice] extra (PR enabling voice ingest +
synthesis), faster-whisper handles inbound transcription and Piper
handles outbound TTS.

Piper's model auto-download has two paths in ``_attempt_download``:

  1. Python import: ``from piper import download_voices; ...`` — preferred
  2. Subprocess fallback: ``[python, -m, piper.download_voices, ...]``

The fallback used a hardcoded ``"python"`` argv[0], which exits with
status 127 on systems whose venv only has ``python3`` on PATH (no
``python`` symlink). That's the default Fedora layout.

Surfaced 2026-05-14 on Windy 0 (Fedora 43): after the [voice] extras
were installed, the bot's first synthesize() call fell through to
the subprocess fallback, hit 127, and the bot process exited because
systemd treated the child's exit as the service exit. Bot went
deactivating mid-conversation while user was actively chatting.

This test pins the contract: the subprocess fallback uses
``sys.executable`` so the inner Python is the SAME interpreter the
bot is running under — never the unreliable PATH lookup of ``python``.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

from windyfly.voice.piper import _attempt_download


class TestPiperSubprocessUsesSysExecutable:

    def test_fallback_uses_sys_executable(self, tmp_path):
        """When the import path fails, the subprocess fallback must
        invoke sys.executable, not the literal string 'python'."""

        # Force the import path to fail so we exercise the fallback.
        def _bad_import(*_args, **_kwargs):
            raise ImportError("simulated — newer piper API not present")

        captured = {}

        def fake_run(argv, *, capture_output, text, timeout):
            captured["argv"] = argv
            captured["timeout"] = timeout

            class R:
                returncode = 0
                stderr = ""
            return R()

        # Patch the `from piper import download_voices` import site to
        # raise. We do this by patching __import__ for that name.
        import builtins
        original_import = builtins.__import__

        def selective_import(name, *args, **kwargs):
            if name == "piper" or name.startswith("piper."):
                raise ImportError("simulated piper import failure")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=selective_import), \
             patch("subprocess.run", side_effect=fake_run):
            ok = _attempt_download("en_US-amy-medium", tmp_path)

        assert ok is True
        argv = captured["argv"]
        assert argv[0] == sys.executable, (
            f"fallback used {argv[0]!r}, expected sys.executable "
            f"({sys.executable!r}). Hardcoded 'python' breaks on "
            "Fedora venvs that only ship python3."
        )
        assert argv[1] == "-m"
        assert argv[2] == "piper.download_voices"
        assert "en_US-amy-medium" in argv

    def test_fallback_argv_never_contains_bare_python(self, tmp_path):
        """Stronger contract: literal 'python' must NEVER appear as the
        executable position in the argv. Catches any future regression
        even if sys.executable happens to resolve to a path containing
        the word 'python'."""
        captured = {}

        def fake_run(argv, *, capture_output, text, timeout):
            captured["argv0"] = argv[0]

            class R:
                returncode = 0
                stderr = ""
            return R()

        import builtins
        original_import = builtins.__import__

        def selective_import(name, *args, **kwargs):
            if name == "piper" or name.startswith("piper."):
                raise ImportError("forced")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=selective_import), \
             patch("subprocess.run", side_effect=fake_run):
            _attempt_download("en_US-amy-medium", tmp_path)

        # The bare command 'python' (length 6, no slashes) is the
        # specific regression pattern we forbid. sys.executable is an
        # absolute path, so this check is robust.
        assert captured["argv0"] != "python", (
            "argv[0] is the bare string 'python' — this is the exact "
            "regression that exited the bot with status 127."
        )
