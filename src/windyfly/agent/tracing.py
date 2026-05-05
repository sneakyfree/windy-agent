"""Request-level tracing spine — the Wave 14 kernel primitive.

Every user-facing operation (``agent_respond``, slash command,
channel inbound) generates a UUID4 ``request_id`` at entry, which is
stashed in a ``contextvars.ContextVar`` so every plane downstream can
pick it up without explicit threading.

Why this matters
----------------

Before this module: a Telegram message → agent loop → 5 LLM calls,
8 capability invocations, 3 episode rows, 12 log lines, 4 events,
1 cost ledger entry. None of them shareable a correlation id. When
something broke, you'd grep the log by approximate timestamp and
hope. This was the operational reality before Wave 14.

After this module: every one of those rows and log lines carries
the same ``request_id``. ``SELECT * FROM agent_actions WHERE
request_id = '…'`` returns every capability that request invoked.
``grep 'req:abc12345' windy-0.log`` returns the entire chain. The
user-facing ``err:abc123`` (in ``channels/errors.py``) is the first
six chars of the request_id, so a user complaint becomes a one-query
trace.

This also unlocks Wave 15+ work that we'll want eventually:
``snapshot/restore`` (replay a request with new prompt), ``audit
search`` (find every fs.write_file call in the last hour), and
multi-tenancy scoping (every request also carries a tenant_id once
the schema scaffolding lands).

Usage
-----

At the top of any user-facing entry point::

    from windyfly.agent.tracing import set_request_id
    set_request_id()  # generates and stores a fresh UUID4

Anywhere downstream::

    from windyfly.agent.tracing import get_request_id
    rid = get_request_id()
    if rid:
        logger.info("doing thing", extra={"request_id": rid})

Or use the helper ``request_id_short()`` for the 8-char form that's
nicer in logs::

    logger.info("[req:%s] doing thing", request_id_short())
"""

from __future__ import annotations

import contextvars
import logging
import uuid

# The ContextVar — one per Python interpreter, scoped via Python's
# context machinery. Async tasks inherit, so a request started on the
# event loop propagates the request_id through every awaited capability.
# Sync threads need to call ``set_request_id`` explicitly at their entry.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "windyfly_request_id", default=None,
)


def set_request_id(rid: str | None = None) -> str:
    """Set the request_id for the current context, generating one if absent.

    Returns the id that was set (so the caller can echo or log it).

    Generally called once per user-facing operation entry. If you call
    it again inside the same operation, you'll override — which is
    probably a bug; treat the request_id as immutable for the lifetime
    of one user message.
    """
    rid = rid or uuid.uuid4().hex
    request_id_var.set(rid)
    return rid


def get_request_id() -> str | None:
    """Return the current context's request_id, or None if unset."""
    return request_id_var.get()


def request_id_short() -> str:
    """Return the 8-char form of the current request_id, or '--------'.

    Convenience for log lines where the full UUID4 hex (32 chars) is
    too noisy. The first 8 chars are sufficient for log correlation
    given a single bot's daily request volume.
    """
    rid = request_id_var.get()
    return rid[:8] if rid else "--------"


def request_id_for_user() -> str:
    """Return the 6-char form used in user-facing ``err:abc123`` reports.

    Stable across the full request lifecycle, so when a user quotes
    ``err:abc123`` back to support, it greps the same log lines as the
    8-char short form (just truncated further).
    """
    rid = request_id_var.get()
    return rid[:6] if rid else "------"


class RequestIdLogFilter(logging.Filter):
    """Attach the current request_id to every log record.

    Install once at process startup::

        for handler in logging.getLogger().handlers:
            handler.addFilter(RequestIdLogFilter())

    Then any log line emitted within a request context has
    ``record.request_id`` available, suitable for inclusion in the
    formatter (e.g., ``%(request_id)s``).

    Records emitted outside a request context get the placeholder
    ``--------`` so format strings never KeyError.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_short()
        return True


def install_log_filter() -> None:
    """Install ``RequestIdLogFilter`` on the root logger and all handlers.

    Idempotent — safe to call from boot sequence even if already
    installed (filter equality dedup).
    """
    flt = RequestIdLogFilter()
    root = logging.getLogger()
    for h in root.handlers:
        # Avoid stacking duplicate filters across reloads
        if not any(isinstance(f, RequestIdLogFilter) for f in h.filters):
            h.addFilter(flt)
    # Also install on the root itself for handlers added later
    if not any(isinstance(f, RequestIdLogFilter) for f in root.filters):
        root.addFilter(flt)
