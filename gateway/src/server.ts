/**
 * Bun HTTP + WebSocket server for Windy Fly gateway.
 *
 * Routes:
 *   GET  /api/health        → { status: "ok" }
 *   GET  /api/sliders       → proxy to UDS sliders.get
 *   PUT  /api/sliders/:name → proxy to UDS sliders.set
 *   GET  /api/cost/daily    → proxy to UDS cost.daily
 *   GET  /api/intents       → proxy to UDS intents.list
 *   WS   /ws/chat           → WebSocket chat
 */

import { bridge } from "./bridge";
import { handleClose, handleMessage, handleWebSocket } from "./websocket";

const PORT = 3000;

async function handleRequest(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const path = url.pathname;

  // CORS headers
  const headers = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, PUT, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };

  // Preflight
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers });
  }

  try {
    // Health check
    if (path === "/api/health") {
      return Response.json(
        {
          status: "ok",
          brain_connected: bridge.isConnected(),
          timestamp: new Date().toISOString(),
        },
        { headers }
      );
    }

    // Sliders GET
    if (path === "/api/sliders" && req.method === "GET") {
      const result = await bridge.call("sliders.get");
      return Response.json(result, { headers });
    }

    // Sliders SET
    const sliderMatch = path.match(/^\/api\/sliders\/(.+)$/);
    if (sliderMatch && req.method === "PUT") {
      const name = sliderMatch[1];
      const body = (await req.json()) as { value: number };
      const result = await bridge.call("sliders.set", {
        name,
        value: body.value,
      });
      return Response.json(result, { headers });
    }

    // Cost daily
    if (path === "/api/cost/daily" && req.method === "GET") {
      const result = await bridge.call("cost.daily");
      return Response.json(result, { headers });
    }

    // Intents list
    if (path === "/api/intents" && req.method === "GET") {
      const result = await bridge.call("intents.list");
      return Response.json(result, { headers });
    }

    // Memory search
    if (path === "/api/memory/search" && req.method === "GET") {
      const query = url.searchParams.get("q") || "";
      const limit = parseInt(url.searchParams.get("limit") || "10");
      const result = await bridge.call("memory.search", { query, limit });
      return Response.json(result, { headers });
    }

    return Response.json({ error: "Not found" }, { status: 404, headers });
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    return Response.json({ error }, { status: 500, headers });
  }
}

// Start server
async function main() {
  // Try to connect to Python brain
  try {
    await bridge.connect();
    console.log("[gateway] Connected to Python brain");
  } catch (e) {
    console.warn(
      "[gateway] Brain not available — starting without bridge connection"
    );
  }

  const server = Bun.serve({
    port: PORT,
    fetch(req, server) {
      // Upgrade WebSocket requests
      if (new URL(req.url).pathname === "/ws/chat") {
        if (server.upgrade(req)) {
          return; // Bun handles the response
        }
        return new Response("WebSocket upgrade failed", { status: 400 });
      }
      return handleRequest(req);
    },
    websocket: {
      open: handleWebSocket,
      message: handleMessage,
      close: handleClose,
    },
  });

  console.log(`[gateway] 🪰 Windy Fly Gateway running on http://localhost:${PORT}`);
  console.log(`[gateway] WebSocket chat at ws://localhost:${PORT}/ws/chat`);
}

main();
