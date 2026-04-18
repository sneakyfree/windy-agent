"""Remote-hatch entry point — emits ceremony progress as newline-delimited
JSON on stdout so the gateway (or any other SSE relay) can forward it
to a rich UI consumer (Electron, mobile, etc.).

Invoked by the Bun gateway's ``POST /hatch/remote`` handler as a
subprocess. Each event is a single ``{"event": "<name>", "data": {...}}``
JSON line, flushed immediately, so the gateway can treat every line as
one SSE ``data:`` frame without buffering.

Input is taken from CLI flags or environment variables so the gateway
doesn't have to stitch together a complex argv. Required inputs
mirror the ``/hatch/remote`` request body:

* ``windy_identity_id`` — Windy Pro account id (identity link-back)
* ``passport_number`` — pre-allocated passport id, if any
* ``broker_token`` — short-lived LLM credential from Pro's broker
  endpoint (stored as the active provider's API key for this hatch)
* ``owner_email`` / ``owner_phone`` / ``owner_name``

Output is JSON Lines. Do not print anything else to stdout. Logs go to
stderr only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("windyfly.hatch_remote")


# Event names the gateway relays as SSE `event:` frames. Kept in order
# here as documentation — the orchestrator itself decides when each
# fires. Keep this list in sync with docs/HATCH_SSE_EVENTS.md.
EVENT_ORDER: list[str] = [
    "eternitas.registering",
    "eternitas.registered",
    "mail.provisioning",
    "mail.provisioned",
    "chat.provisioning",
    "chat.provisioned",
    "cloud.provisioning",
    "cloud.provisioned",
    "phone.assigning",
    "phone.assigned",
    "birth_certificate.generating",
    "birth_certificate.ready",
    "hatch.complete",
]


def _emit_json(event: str, data: dict[str, Any]) -> None:
    """Write one JSON-Lines event to stdout, flushed."""
    payload = {"event": event, "data": data}
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def _apply_broker_token(broker_token: str) -> str:
    """Store the broker_token as the active provider's API key in the
    process environment so downstream LLM calls pick it up.

    Skips the "paste API key" prompt entirely (that's the whole point
    of the managed-credential flow).

    Returns the env var name that was populated, or "" if no token.
    """
    if not broker_token:
        return ""

    # The broker token's provider is encoded in the token itself when
    # Pro mints it, but we don't parse it here — we just populate every
    # provider env var we care about. The agent's config file selects
    # which one is active via DEFAULT_MODEL. This is intentionally
    # permissive: Pro's broker is the single source of truth for which
    # provider the user is on; we just cache the key everywhere a
    # provider-specific client might look for it.
    #
    # If a caller wants a single env var populated, they can set
    # WINDY_BROKER_PROVIDER (one of openai/anthropic/grok/gemini/...)
    # and only that one will be written.
    preferred = os.environ.get("WINDY_BROKER_PROVIDER", "").strip().lower()
    provider_to_env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "grok": "GROK_API_KEY",
        "xai": "GROK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "google": "GEMINI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "mistral": "MISTRAL_API_KEY",
    }
    if preferred in provider_to_env:
        env_var = provider_to_env[preferred]
        os.environ[env_var] = broker_token
        return env_var

    # No preferred provider — set all of them. The active_model config
    # picks the one that actually gets used.
    for env_var in provider_to_env.values():
        os.environ.setdefault(env_var, broker_token)
    return "*"


def run(
    *,
    agent_name: str,
    windy_identity_id: str,
    passport_number: str,
    broker_token: str,
    owner_email: str,
    owner_phone: str,
    owner_name: str,
    render_mode: str = "json",
    animate: bool = True,
) -> int:
    """Invoke the orchestrator with event streaming. Returns exit code."""

    # Seed environment so orchestrator sub-modules (mail, phone, sms,
    # eternitas linkback) see the owner details and the managed
    # credential. This matches how `windy go` sets these today.
    if owner_email:
        os.environ["OWNER_EMAIL"] = owner_email
    if owner_phone:
        os.environ["OWNER_PHONE"] = owner_phone
    if owner_name:
        os.environ["WINDY_OWNER_NAME"] = owner_name
    if windy_identity_id:
        os.environ["WINDY_IDENTITY_ID"] = windy_identity_id
    if passport_number:
        os.environ["ETERNITAS_PASSPORT"] = passport_number

    env_var_set = _apply_broker_token(broker_token)
    _emit_json("hatch.starting", {
        "agent_name": agent_name,
        "windy_identity_id": windy_identity_id,
        "passport_number": passport_number,
        "broker_credential_env": env_var_set,
        "render_mode": render_mode,
    })

    # Play the hatching ceremony in the requested mode before kicking
    # off provisioning. In JSON mode the stages are emitted as
    # ``ceremony.*`` events so a remote UI can animate in sync with
    # the backend's timing. In terminal mode we fall back to Rich
    # output on stderr (stdout is reserved for JSON events).
    from windyfly.hatching import play_hatching
    if render_mode == "json":
        play_hatching(animate=animate, render_mode="json", on_event=_emit_json)
    else:
        # Terminal mode inside hatch_remote is only useful when
        # someone is driving this subprocess manually — still log to
        # stderr so stdout stays JSON-only (or pure JSON mode only).
        play_hatching(animate=animate, render_mode="terminal")

    # Late import: the orchestrator touches httpx/rich/etc. which are
    # heavy. Keep CLI startup cheap so the SSE "open" turnaround stays
    # snappy in the Electron consumer.
    from windyfly.hatch_orchestrator import orchestrate_hatch

    async def _go() -> int:
        try:
            result = await orchestrate_hatch(
                agent_name=agent_name,
                owner_id=windy_identity_id,
                owner_name=owner_name,
                on_event=_emit_json,
            )
        except Exception as exc:
            logger.exception("Hatch failed with exception")
            _emit_json("hatch.error", {"message": str(exc), "type": type(exc).__name__})
            return 1
        # hatch.complete is already emitted inside the orchestrator.
        # Only surface a non-zero exit when the passport itself failed;
        # individual downstream service failures land in result.errors
        # and should not mark the whole ceremony red.
        return 0 if result.passport_id else 2

    return asyncio.run(_go())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="windyfly.hatch_remote")
    parser.add_argument("--agent-name", default=os.environ.get("WINDYFLY_AGENT_NAME", "Windy Fly"))
    parser.add_argument("--windy-identity-id", default=os.environ.get("WINDY_IDENTITY_ID", ""))
    parser.add_argument("--passport-number", default=os.environ.get("ETERNITAS_PASSPORT", ""))
    parser.add_argument("--broker-token", default=os.environ.get("WINDY_BROKER_TOKEN", ""))
    parser.add_argument("--owner-email", default=os.environ.get("OWNER_EMAIL", ""))
    parser.add_argument("--owner-phone", default=os.environ.get("OWNER_PHONE", ""))
    parser.add_argument("--owner-name", default=os.environ.get("WINDY_OWNER_NAME", ""))
    parser.add_argument(
        "--render-mode",
        choices=("json", "terminal"),
        default="json",
        help="Ceremony renderer. JSON emits structured events; terminal uses Rich ASCII.",
    )
    parser.add_argument(
        "--no-animate",
        action="store_true",
        help="Skip dramatic pauses between ceremony stages (useful for tests).",
    )
    args = parser.parse_args(argv)

    # Log to stderr only — stdout is reserved for JSON events.
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "WARNING"),
        stream=sys.stderr,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    return run(
        agent_name=args.agent_name,
        windy_identity_id=args.windy_identity_id,
        passport_number=args.passport_number,
        broker_token=args.broker_token,
        owner_email=args.owner_email,
        owner_phone=args.owner_phone,
        owner_name=args.owner_name,
        render_mode=args.render_mode,
        animate=not args.no_animate,
    )


if __name__ == "__main__":
    sys.exit(main())
