"""UDS Bridge — Unix Domain Socket server for Bun gateway communication.

Exposes the Python brain's capabilities over a JSON protocol on a
Unix Domain Socket at /tmp/windyfly.sock.
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
from windyfly.memory.intents import surface_pending_intents
from windyfly.memory.nodes import search_nodes
from windyfly.memory.write_queue import WriteQueue

logger = logging.getLogger(__name__)

DEFAULT_SOCKET_PATH = "/tmp/windyfly.sock"


class UDSBridge:
    """JSON-over-UDS server for gateway communication."""

    def __init__(
        self,
        config: dict[str, Any],
        db: Database,
        write_queue: WriteQueue,
        socket_path: str = DEFAULT_SOCKET_PATH,
    ) -> None:
        self.config = config
        self.db = db
        self.write_queue = write_queue
        self.socket_path = socket_path
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
            "sliders.get": self._handle_sliders_get,
            "sliders.set": self._handle_sliders_set,
            "sliders.info": self._handle_sliders_info,
            "cost.daily": self._handle_cost_daily,
            "intents.list": self._handle_intents_list,
            "dashboard.summary": self._handle_dashboard_summary,
            "soul.preview": self._handle_soul_preview,
            "soul.import": self._handle_soul_import,
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

    async def _handle_intents_list(self, params: dict) -> dict:
        intents = surface_pending_intents(self.db)
        return {"intents": intents}

    async def _handle_dashboard_summary(self, params: dict) -> dict:
        user_id = params.get("user_id", "default")
        summary = get_dashboard_summary(self.db, user_id=user_id)
        return {"dashboard": summary}

    async def _handle_soul_preview(self, params: dict) -> dict:
        from windyfly.soul_import.orchestrator import import_soul
        export_path = params.get("export_path", "")
        source_type = params.get("source_type")
        result = import_soul(self.db, export_path, source_type, user_approved=False)
        # Don't send parsed_data over the wire — just the preview text
        result.pop("parsed_data", None)
        return result

    async def _handle_soul_import(self, params: dict) -> dict:
        from windyfly.soul_import.orchestrator import import_soul
        export_path = params.get("export_path", "")
        source_type = params.get("source_type")
        result = import_soul(self.db, export_path, source_type, user_approved=True)
        result.pop("parsed_data", None)
        return result

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
        """Start the UDS server."""
        # Remove existing socket file
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=self.socket_path,
        )
        logger.info("UDS Bridge listening on %s", self.socket_path)

    async def stop(self) -> None:
        """Stop the UDS server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        logger.info("UDS Bridge stopped")
