"""Voice ingestion + (future) synthesis.

The Telegram channel pre-PR #129 dropped voice messages silently.
This package adds:

  - ``whisper.py``: voice file → text via local Whisper
  - (future) ``synthesize.py``: text → voice file for spoken replies

Both layers are **opt-in** via extras-deps:

    pip install windyfly[voice]    # pulls faster-whisper

Without the extras, ``transcribe(...)`` returns None and the channel
adapter responds with a polite text-only reply ("voice not ready yet")
rather than dropping the message silently. Same graceful-fallback
pattern as the semantic-memory layer (PR #127).

The submodule is named ``whisper`` (not ``transcribe``) so the
function ``transcribe`` doesn't shadow the submodule when callers
do ``from windyfly.voice import transcribe``.
"""

from windyfly.voice.whisper import is_available, transcribe

__all__ = ["is_available", "transcribe"]
