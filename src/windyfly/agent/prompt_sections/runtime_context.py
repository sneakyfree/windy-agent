"""RUNTIME CONTEXT — positive truth pinning (PR #192 + #202 CWD).

Counterbalances the RUNTIME GUARDRAIL "don't claim X" rules with
"you DO know Y" facts. Reads runtime signals (env vars, CWD, auth
path) at assembly time so the prompt stays correct across host
migrations without per-instance hardcodes.

Exposes `render_runtime_context(config)` rather than a constant
because the content varies by run.
"""

from __future__ import annotations

import os
from typing import Any


def render_runtime_context(config: dict[str, Any]) -> str:
    """Assemble the RUNTIME CONTEXT block for the current run.

    Content sources (per as-built doc §3):
      - Model: config["agent"]["default_model"]
      - Auth: windyfly.agent.models.get_anthropic_auth_path()
      - Supervisor: env detection (INVOCATION_ID / /.dockerenv /
        KUBERNETES_SERVICE_HOST / AWS_LAMBDA_FUNCTION_NAME)
      - CWD: os.getcwd()
    """
    parts = [
        "RUNTIME CONTEXT (true facts, not guesses — quote these "
        "instead of inventing):",
    ]

    active_model = config.get("agent", {}).get("default_model") or \
        config.get("default_model", "unknown")
    parts.append(f"- Model: {active_model}")

    try:
        from windyfly.agent.models import get_anthropic_auth_path
        auth = get_anthropic_auth_path()
        parts.append(f"- Anthropic auth: {auth['label_long']}")
    except Exception:  # noqa: BLE001 — best-effort enrichment
        parts.append("- Anthropic auth: unknown")

    if os.environ.get("INVOCATION_ID"):
        supervisor = "native systemd service"
    elif os.path.exists("/.dockerenv"):
        supervisor = "Docker container"
    elif os.environ.get("KUBERNETES_SERVICE_HOST"):
        supervisor = "Kubernetes pod"
    elif os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        supervisor = "AWS Lambda"
    else:
        supervisor = "unsupervised process"
    parts.append(
        f"- Process: {supervisor} on the operator's machine "
        "(NOT a remote VPS unless you can name a tool that proves it)"
    )

    try:
        cwd = os.getcwd()
        parts.append(
            f"- CWD: {cwd} (when the user says 'this repo' / 'here' "
            "/ 'this folder', resolve against this path first)"
        )
    except OSError:
        pass

    parts.append(
        "If asked which model / auth / billing / host you have, "
        "QUOTE the corresponding line above. Do not hedge with "
        "'I can't tell you exactly' — these lines ARE the answer."
    )
    return "\n".join(parts)
