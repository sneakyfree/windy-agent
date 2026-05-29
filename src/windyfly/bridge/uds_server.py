"""IPC Bridge — JSON-over-stream server for Bun gateway communication.

Exposes the Python brain's capabilities over a JSON protocol.
Uses Unix Domain Sockets on Mac/Linux and TCP on Windows.
The IPC mode and paths are resolved via :mod:`windyfly.platform`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

from windyfly.agent.loop import agent_respond
from windyfly.control_panel import get_slider_info, get_sliders, set_slider
from windyfly.dashboard.data import get_dashboard_summary
from windyfly.memory.cost_ledger import get_daily_spend
from windyfly.memory.database import Database
from windyfly.memory.nodes import search_nodes
from windyfly.memory.write_queue import WriteQueue
from windyfly.platform import get_ipc_config, IPCConfig

logger = logging.getLogger(__name__)


class UDSBridge:
    """JSON-over-stream IPC server for gateway communication.

    Despite the class name (kept for backward compatibility), this now
    supports both UDS and TCP transports based on platform detection.
    """

    def __init__(
        self,
        config: dict[str, Any],
        db: Database,
        write_queue: WriteQueue,
        socket_path: str | None = None,
        ipc_config: IPCConfig | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.write_queue = write_queue
        self.ipc = ipc_config or get_ipc_config()
        # Legacy socket_path arg overrides platform detection for UDS mode
        if socket_path is not None:
            self.ipc = IPCConfig(
                mode="uds",
                socket_path=socket_path,
                tcp_host=self.ipc.tcp_host,
                tcp_port=self.ipc.tcp_port,
            )
        self._server: asyncio.AbstractServer | None = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection."""
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break

                try:
                    request = json.loads(data.decode("utf-8"))
                except json.JSONDecodeError:
                    await self._send_error(writer, "invalid", "Invalid JSON")
                    continue

                request_id = request.get("id", str(uuid.uuid4()))
                method = request.get("method", "")
                params = request.get("params", {})

                try:
                    result = await self._dispatch(method, params)
                    response = {"id": request_id, "result": result, "error": None}
                except Exception as e:
                    logger.error("UDS method %s failed: %s", method, e)
                    response = {"id": request_id, "result": None, "error": str(e)}

                writer.write(json.dumps(response).encode("utf-8") + b"\n")
                await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("UDS client error: %s", e)
        finally:
            writer.close()

    async def _dispatch(self, method: str, params: dict) -> Any:
        """Dispatch a method call to the appropriate handler."""
        handlers = {
            "agent.respond": self._handle_respond,
            "memory.search": self._handle_search,
            "memory.delete": self._handle_memory_delete,
            "sliders.get": self._handle_sliders_get,
            "sliders.set": self._handle_sliders_set,
            "sliders.info": self._handle_sliders_info,
            "cost.daily": self._handle_cost_daily,
            "cost.monthly": self._handle_cost_monthly,
            "config.reload": self._handle_config_reload,
            "intents.list": self._handle_intents_list,
            "dashboard.summary": self._handle_dashboard_summary,
            "trust.webhook": self._handle_trust_webhook,
            "soul.preview": self._handle_soul_preview,
            "soul.import": self._handle_soul_import,
            "sms.inbound": self._handle_sms_inbound,
            "sms.send": self._handle_sms_send,
            "email.inbound": self._handle_email_inbound,
            "email.send": self._handle_email_send,
            "journal.list": self._handle_journal_list,
            "assessment.run": self._handle_assessment_run,
            "shape_shift.execute": self._handle_shape_shift,
            "shape_shift.restore": self._handle_shape_shift_restore,
            # --- Group 1: Personality versioning ---
            "personality.history": self._handle_personality_history,
            "personality.snapshot": self._handle_personality_snapshot,
            "personality.drift": self._handle_personality_drift,
            "personality.rollback": self._handle_personality_rollback,
            # --- Group 2: Skills management ---
            "skills.list": self._handle_skills_list,
            "skills.create": self._handle_skills_create,
            "skills.evaluate": self._handle_skills_evaluate,
            "skills.promote": self._handle_skills_promote,
            "skills.rollback": self._handle_skills_rollback,
            "skills.golden_tests": self._handle_skills_golden_tests,
            "skills.regression": self._handle_skills_regression,
            # --- Group 3: Decay, conflicts, moments, failures ---
            "decay.run": self._handle_decay_run,
            "conflicts.list": self._handle_conflicts_list,
            "conflicts.resolve": self._handle_conflicts_resolve,
            "moments.list": self._handle_moments_list,
            "failures.list": self._handle_failures_list,
            # --- Group 4: Mode, offline, events ---
            "mode.get": self._handle_mode_get,
            "mode.set": self._handle_mode_set,
            "offline.status": self._handle_offline_status,
            "events.list": self._handle_events_list,
        }

        handler = handlers.get(method)
        if not handler:
            raise ValueError(f"Unknown method: {method}")

        return await handler(params)

    async def _handle_respond(self, params: dict) -> dict:
        message = params.get("message", "")
        session_id = params.get("session_id", str(uuid.uuid4()))
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, agent_respond, self.config, self.db,
            self.write_queue, message, session_id,
        )
        return {"response": response}

    async def _handle_search(self, params: dict) -> dict:
        query = params.get("query", "")
        limit = params.get("limit", 10)
        nodes = search_nodes(self.db, query, limit=limit)
        return {"nodes": nodes}

    async def _handle_memory_delete(self, params: dict) -> dict:
        from windyfly.memory.nodes import delete_node
        node_id = params.get("node_id", "")
        user_id = params.get("user_id", "default")
        if not node_id:
            return {"deleted": False, "error": "missing node_id"}
        deleted = delete_node(self.db, node_id, user_id=user_id)
        return {"deleted": deleted}

    async def _handle_sliders_get(self, params: dict) -> dict:
        sliders = get_sliders(self.db)
        return {"sliders": sliders}

    async def _handle_sliders_set(self, params: dict) -> dict:
        name = params.get("name", "")
        value = params.get("value", 5)
        set_slider(self.db, name, value)
        return {"success": True}

    async def _handle_sliders_info(self, params: dict) -> dict:
        info = get_slider_info(self.db)
        return {"sliders": info}

    async def _handle_cost_daily(self, params: dict) -> dict:
        spend = get_daily_spend(self.db)
        return {"daily_spend": spend}

    async def _handle_cost_monthly(self, params: dict) -> dict:
        from datetime import datetime
        rows = self.db.fetchall(
            """
            SELECT model, COALESCE(SUM(cost_usd), 0.0) as total
            FROM cost_ledger
            WHERE created_at >= date('now', 'start of month')
            GROUP BY model
            """
        )
        by_model = {r["model"]: round(r["total"], 4) for r in rows}
        total = sum(by_model.values())
        return {
            "month": datetime.now().strftime("%Y-%m"),
            "total_usd": round(total, 4),
            "by_model": by_model,
        }

    async def _handle_config_reload(self, params: dict) -> dict:
        """Reload config from disk. Called by gateway after setup wizard."""
        try:
            from windyfly.config import load_config
            new_config = load_config()
            self.config.update(new_config)
            return {"success": True}
        except Exception as e:
            logger.warning("Config reload failed: %s", e)
            return {"success": False, "error": str(e)}

    async def _handle_intents_list(self, params: dict) -> dict:
        # Return all active intents (matches the "active" count on Home).
        # surface_pending_intents() is the 24h chat-inferred inbox and left
        # the dashboard "Active Goals" list empty despite a non-zero count.
        from windyfly.dashboard.data import get_active_intents
        user_id = params.get("user_id", "default")
        return {"intents": get_active_intents(self.db, user_id)}

    async def _handle_dashboard_summary(self, params: dict) -> dict:
        user_id = params.get("user_id", "default")
        summary = get_dashboard_summary(
            self.db, user_id=user_id, config=self.config
        )
        return {"dashboard": summary}

    async def _handle_trust_webhook(self, params: dict) -> dict:
        """Eternitas trust.changed webhook (fanned from gateway).

        Params:
            body_b64:  base64 of the raw request body (so we can HMAC
                       the bytes that were actually wired).
            headers:   dict of inbound header name → value.
            payload:   parsed JSON of the body (convenience — ignored
                       if verification fails).
        """
        import base64

        from windyfly.trust.verify import verify_webhook
        from windyfly.trust.webhook import handle_trust_changed

        try:
            raw_body = base64.b64decode(params.get("body_b64", ""))
        except Exception:
            return {"ok": False, "reason": "invalid body_b64"}
        headers = {str(k): str(v) for k, v in (params.get("headers") or {}).items()}

        verification = verify_webhook(raw_body, headers)
        if not verification.ok:
            logger.warning("Trust webhook rejected: %s", verification.reason)
            return {"ok": False, "reason": verification.reason}

        try:
            payload = params.get("payload") or {}
            result = await handle_trust_changed(payload, db=self.db)
            return {
                "ok": True,
                "passport": result.passport,
                "direction": result.direction,
                "cache_invalidated": result.cache_invalidated,
                "key_rotated": result.key_rotated,
                "owner_notified": result.owner_notified,
            }
        except Exception as exc:
            logger.exception("Trust webhook handler failed")
            return {"ok": False, "reason": f"handler error: {exc}"}

    async def _handle_soul_preview(self, params: dict) -> dict:
        """Soul Passport preview — parse and preview without writing."""
        from windyfly.soul_import.orchestrator import import_soul
        export_path = params.get("export_path", "")
        source_type = params.get("source_type")
        result = import_soul(self.db, export_path, source_type, user_approved=False)
        # Don't send parsed_data over the wire — just the preview text
        result.pop("parsed_data", None)
        return result

    async def _handle_soul_import(self, params: dict) -> dict:
        """Soul Passport import — parse, preview, and write to database."""
        from windyfly.soul_import.orchestrator import import_soul
        export_path = params.get("export_path", "")
        source_type = params.get("source_type")
        result = import_soul(self.db, export_path, source_type, user_approved=True)
        result.pop("parsed_data", None)
        return result

    async def _handle_sms_inbound(self, params: dict) -> dict:
        from windyfly.channels.sms import WindyFlySMS
        sms = WindyFlySMS(self.config, self.db, self.write_queue)
        response = sms.handle_inbound(
            params.get("From", ""),
            params.get("Body", ""),
        )
        return {"twiml": f"<Response><Message>{response}</Message></Response>"}

    async def _handle_sms_send(self, params: dict) -> dict:
        from windyfly.channels.sms import WindyFlySMS
        sms = WindyFlySMS(self.config, self.db, self.write_queue)
        result = sms.send_sms(params.get("to", ""), params.get("message", ""))
        return result

    async def _handle_email_inbound(self, params: dict) -> dict:
        from windyfly.channels.email import WindyFlyEmail
        email = WindyFlyEmail(self.config, self.db, self.write_queue)
        # SendGrid sends: from, subject, text (or html)
        # Also handle: envelope → from, subject, plain
        from_addr = params.get("from", params.get("sender", ""))
        if isinstance(from_addr, str) and "<" in from_addr:
            # Extract email from "Name <email>" format
            import re
            match = re.search(r"<(.+?)>", from_addr)
            from_addr = match.group(1) if match else from_addr
        response = email.handle_inbound(
            from_addr,
            params.get("subject", ""),
            params.get("text", params.get("plain", params.get("body", ""))),
        )
        return {"response": response}

    async def _handle_email_send(self, params: dict) -> dict:
        from windyfly.channels.email import WindyFlyEmail
        email = WindyFlyEmail(self.config, self.db, self.write_queue)
        result = email.send_email(
            params.get("to", ""),
            params.get("subject", ""),
            params.get("body", ""),
        )
        return result

    async def _handle_journal_list(self, params: dict) -> dict:
        from windyfly.memory.nodes import get_nodes_by_type
        import json
        entries = get_nodes_by_type(self.db, "journal_entry", limit=20)
        result = []
        for e in entries:
            meta = e.get("metadata", "{}")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            result.append({
                "entry": meta.get("entry", e.get("name", "")),
                "created_at": e.get("created_at", ""),
            })
        return {"journal": result}

    async def _handle_assessment_run(self, params: dict) -> dict:
        from windyfly.agent.self_assessment import run_self_assessment
        report = run_self_assessment(self.db)
        return {"assessment": report}

    async def _handle_shape_shift(self, params: dict) -> dict:
        from windyfly.agent.shape_shift import get_shift_announcement
        from windyfly.control_panel import apply_preset, get_sliders
        preset = params.get("preset", "buddy")
        autonomy = get_sliders(self.db).get("autonomy", 5)
        announcement = get_shift_announcement(autonomy, preset)
        saved = get_sliders(self.db)
        applied = apply_preset(self.db, preset)
        return {
            "shifted_to": preset,
            "announcement": announcement,
            "saved_sliders": saved,
            "applied": applied,
        }

    async def _handle_shape_shift_restore(self, params: dict) -> dict:
        from windyfly.control_panel import set_slider
        sliders = params.get("sliders", {})
        for k, v in sliders.items():
            set_slider(self.db, k, int(v))
        return {"restored": True}

    # ------------------------------------------------------------------
    # Group 1: Personality versioning
    # ------------------------------------------------------------------

    async def _handle_personality_history(self, params: dict) -> dict:
        from windyfly.personality.versioning import get_personality_history
        limit = params.get("limit", 20)
        history = get_personality_history(self.db, limit=limit)
        return {"history": history}

    async def _handle_personality_snapshot(self, params: dict) -> dict:
        from windyfly.personality.versioning import snapshot_personality
        user_id = params.get("user_id", "default")
        changed_by = params.get("changed_by", "user")
        batch_id = snapshot_personality(self.db, user_id=user_id, changed_by=changed_by)
        return {"batch_id": batch_id}

    async def _handle_personality_drift(self, params: dict) -> dict:
        from windyfly.personality.versioning import detect_and_log_drift
        user_id = params.get("user_id", "default")
        drift = detect_and_log_drift(self.db, self.write_queue, user_id=user_id)
        return {"drift": drift}

    async def _handle_personality_rollback(self, params: dict) -> dict:
        from windyfly.personality.versioning import rollback_personality
        snapshot_date = params.get("snapshot_date", "")
        user_id = params.get("user_id", "default")
        restored = rollback_personality(self.db, snapshot_date, user_id=user_id)
        return {"restored_count": restored}

    # ------------------------------------------------------------------
    # Group 2: Skills management
    # ------------------------------------------------------------------

    async def _handle_skills_list(self, params: dict) -> dict:
        from windyfly.memory.skills import list_skills
        promoted_only = params.get("promoted_only", False)
        skills = list_skills(self.db, promoted_only=promoted_only)
        return {"skills": skills}

    async def _handle_skills_create(self, params: dict) -> dict:
        from windyfly.skills.manager import create_skill
        skill_id = create_skill(
            self.db,
            name=params.get("name", ""),
            code=params.get("code", ""),
            language=params.get("language", "python"),
        )
        return {"skill_id": skill_id}

    async def _handle_skills_evaluate(self, params: dict) -> dict:
        from windyfly.skills.evaluator import evaluate_skill
        skill_id = params.get("skill_id", "")
        result = evaluate_skill(self.db, skill_id)
        return {"evaluation": result}

    async def _handle_skills_promote(self, params: dict) -> dict:
        from windyfly.skills.manager import promote_skill
        skill_id = params.get("skill_id", "")
        promote_skill(self.db, skill_id)
        return {"promoted": True, "skill_id": skill_id}

    async def _handle_skills_rollback(self, params: dict) -> dict:
        from windyfly.skills.manager import rollback_skill
        skill_id = params.get("skill_id", "")
        rollback_skill(self.db, skill_id)
        return {"rolled_back": True, "skill_id": skill_id}

    async def _handle_skills_golden_tests(self, params: dict) -> dict:
        from windyfly.skills.golden_tests import run_golden_tests
        skill_id = params.get("skill_id", "")
        result = run_golden_tests(self.db, skill_id)
        return {"golden_tests": result}

    async def _handle_skills_regression(self, params: dict) -> dict:
        from windyfly.skills.golden_tests import run_regression_suite
        result = run_regression_suite(self.db)
        return {"regression": result}

    # ------------------------------------------------------------------
    # Group 3: Decay, conflicts, moments, failures
    # ------------------------------------------------------------------

    async def _handle_decay_run(self, params: dict) -> dict:
        from windyfly.memory.decay import run_decay
        counts = run_decay(self.db, self.write_queue, config=self.config)
        return {"decay": counts}

    async def _handle_conflicts_list(self, params: dict) -> dict:
        from windyfly.memory.conflict_detector import get_unresolved_conflicts
        conflicts = get_unresolved_conflicts(self.db)
        return {"conflicts": conflicts}

    async def _handle_conflicts_resolve(self, params: dict) -> dict:
        from windyfly.memory.conflict_detector import resolve_conflict
        conflict_id = params.get("conflict_id", "")
        resolution = params.get("resolution", "")
        keep_new = params.get("keep_new", True)
        resolve_conflict(self.db, conflict_id, resolution, keep_new)
        return {"resolved": True, "conflict_id": conflict_id}

    async def _handle_moments_list(self, params: dict) -> dict:
        from windyfly.memory.nodes import get_nodes_by_type
        import json as _json
        limit = params.get("limit", 20)
        nodes = get_nodes_by_type(self.db, "relationship_moment", limit=limit)
        result = []
        for n in nodes:
            meta = n.get("metadata", "{}")
            if isinstance(meta, str):
                try:
                    meta = _json.loads(meta)
                except (ValueError, TypeError):
                    meta = {}
            result.append({
                "summary": meta.get("summary", n.get("name", "")),
                "emotional_context": meta.get("emotional_context", "neutral"),
                "session_id": meta.get("session_id", ""),
                "created_at": n.get("created_at", ""),
            })
        return {"moments": result}

    async def _handle_failures_list(self, params: dict) -> dict:
        limit = params.get("limit", 20)
        rows = self.db.fetchall(
            "SELECT * FROM failures ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return {"failures": rows}

    # ------------------------------------------------------------------
    # Group 4: Mode, offline, events
    # ------------------------------------------------------------------

    async def _handle_mode_get(self, params: dict) -> dict:
        from windyfly.memory.soul import get_soul
        user_id = params.get("user_id", "default")
        row = get_soul(self.db, "agent_mode", user_id=user_id)
        mode = row["value"] if row else "companion"
        return {"mode": mode}

    async def _handle_mode_set(self, params: dict) -> dict:
        from windyfly.personality.mode import validate_mode
        from windyfly.memory.soul import upsert_soul
        user_id = params.get("user_id", "default")
        mode = validate_mode(params.get("mode", "companion"))
        upsert_soul(self.db, key="agent_mode", value=mode, source="control_panel", user_id=user_id)
        return {"mode": mode}

    async def _handle_offline_status(self, params: dict) -> dict:
        from windyfly.agent.offline import is_online, is_ollama_available
        return {
            "online": is_online(),
            "ollama_available": is_ollama_available(),
        }

    async def _handle_events_list(self, params: dict) -> dict:
        from windyfly.observability.events import get_recent_events, get_event_counts
        event_type = params.get("event_type")
        limit = params.get("limit", 50)
        events = get_recent_events(self.db, event_type=event_type, limit=limit)
        counts = get_event_counts(self.db, since_hours=24)
        return {"events": events, "counts_24h": counts}

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        request_id: str,
        error: str,
    ) -> None:
        response = {"id": request_id, "result": None, "error": error}
        writer.write(json.dumps(response).encode("utf-8") + b"\n")
        await writer.drain()

    async def start(self) -> None:
        """Start the IPC server (UDS or TCP based on platform)."""
        if self.ipc.mode == "uds":
            # Remove stale socket file
            if os.path.exists(self.ipc.socket_path):
                os.unlink(self.ipc.socket_path)
            self._server = await asyncio.start_unix_server(
                self._handle_client,
                path=self.ipc.socket_path,
            )
            logger.info("IPC Bridge listening on UDS %s", self.ipc.socket_path)
        else:
            # TCP mode — works everywhere including Windows
            self._server = await asyncio.start_server(
                self._handle_client,
                host=self.ipc.tcp_host,
                port=self.ipc.tcp_port,
            )
            logger.info(
                "IPC Bridge listening on TCP %s:%d",
                self.ipc.tcp_host,
                self.ipc.tcp_port,
            )

    async def stop(self) -> None:
        """Stop the IPC server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        # Clean up socket file (UDS only)
        if self.ipc.mode == "uds" and os.path.exists(self.ipc.socket_path):
            os.unlink(self.ipc.socket_path)
        logger.info("IPC Bridge stopped")


async def _serve_forever() -> None:
    """Run the UDS bridge as a long-lived RPC server.

    This is the production brain entry point invoked by `windy start`
    via `python -m windyfly.bridge.uds_server`. Prior to 2026-05-20,
    this module exposed `UDSBridge` but had no module-level startup —
    so the Popen() in cli.py:cmd_start spawned a Python process that
    imported the module and exited immediately, leaving the Bun
    gateway connecting to a socket that never got created.

    Tests instantiate UDSBridge directly and don't exercise this
    function, so they continue to validate the bridge in isolation.

    Boot sequence:
      1. Load .env + config (mirrors main.py)
      2. Open the Database + start the WriteQueue
      3. Acquire the single-runtime claim slot via Mind (ADR-051 A.5).
         On CONFLICT, exit cleanly without listening on the socket.
         On DEGRADED/SKIPPED, proceed (fail-open).
      4. Start the bridge (listens on UDS or TCP per platform)
      5. Block on a shutdown signal (SIGTERM, SIGINT)
      6. Stop the bridge + flush the write queue + close the DB
    """
    import asyncio
    import signal
    import sys

    from dotenv import load_dotenv

    from windyfly import runtime_claim
    from windyfly.config import load_config

    load_dotenv()

    # Config is required — without windyfly.toml we don't know the DB
    # path, the channel config, etc. Fail loud if the caller forgot
    # to `windy init` first.
    try:
        config = load_config("windyfly.toml")
    except FileNotFoundError as e:
        # Use stderr-write rather than print() to satisfy the
        # production-print lint check (test_no_print_statements_in_production).
        sys.stderr.write(f"Error loading config: {e}\n")
        sys.exit(1)

    # Basic logging — main.py installs richer filters when run as a
    # standalone channel; the gateway-fronted brain just needs a
    # baseline.
    log_level = config.get("log_level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # ADR-051 Phase A.5 — claim the runtime slot before listening on
    # the socket. If another runtime already holds the slot for this
    # agent, exit cleanly so the gateway sees a clean failure rather
    # than a brain that's racing with the holder.
    outcome = runtime_claim.acquire_runtime_slot(source="cli")
    if outcome == runtime_claim.ClaimOutcome.CONFLICT:
        # stderr-write rather than print() per the production-print
        # lint check (test_no_print_statements_in_production).
        sys.stderr.write(
            f"Another Windy Fly runtime is already hosting this agent "
            f"({runtime_claim.conflict_holder_summary()}). Brain exiting.\n"
        )
        sys.exit(0)
    elif outcome == runtime_claim.ClaimOutcome.GRANTED:
        runtime_claim.start_heartbeat()
        runtime_claim.register_atexit_release()

    db_path = config.get("memory", {}).get("db_path", "data/windyfly.db")
    db = Database(db_path)
    write_queue = WriteQueue()
    write_queue.start()

    bridge = UDSBridge(config, db, write_queue)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # add_signal_handler isn't available on Windows; the brain
        # falls back to default behavior there (signal terminates
        # the loop, finally block still runs).
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await bridge.start()
        logger.info("Brain ready — awaiting shutdown signal")
        await stop_event.wait()
    finally:
        logger.info("Shutdown requested — stopping brain")
        await bridge.stop()
        write_queue.stop()
        db.close()
        logger.info("Brain stopped cleanly")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_serve_forever())
