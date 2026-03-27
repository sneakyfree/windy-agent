/**
 * Bun HTTP + WebSocket server for Windy Fly gateway.
 *
 * Routes:
 *   GET  /                   → Trust Dashboard (index.html)
 *   GET  /api/health         → { status: "ok" }
 *   GET  /api/sliders        → proxy to UDS sliders.get
 *   GET  /api/sliders/info   → proxy to UDS sliders.info
 *   PUT  /api/sliders/:name  → proxy to UDS sliders.set
 *   GET  /api/cost/daily     → proxy to UDS cost.daily
 *   GET  /api/intents        → proxy to UDS intents.list
 *   GET  /api/dashboard      → proxy to UDS dashboard.summary
 *   GET  /api/memory/search  → proxy to UDS memory.search
 *   WS   /ws/chat            → WebSocket chat
 */

import { resolve } from "path";
import { bridge } from "./bridge";
import { handleClose, handleMessage, handleWebSocket } from "./websocket";

const PORT = 3000;
const PUBLIC_DIR = resolve(import.meta.dir, "../public");

async function handleRequest(req: Request): Promise<Response> {
  const url = new URL(req.url);
  const path = url.pathname;

  // CORS headers
  const headers = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, PUT, POST, OPTIONS",
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

    // Sliders INFO (metadata with descriptions, impact, cost)
    if (path === "/api/sliders/info" && req.method === "GET") {
      const result = await bridge.call("sliders.info");
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

    // Dashboard summary (G3)
    if (path === "/api/dashboard" && req.method === "GET") {
      const result = await bridge.call("dashboard.summary");
      return Response.json(result, { headers });
    }

    // Memory search
    if (path === "/api/memory/search" && req.method === "GET") {
      const query = url.searchParams.get("q") || "";
      const limit = parseInt(url.searchParams.get("limit") || "10");
      const result = await bridge.call("memory.search", { query, limit });
      return Response.json(result, { headers });
    }

    // Soul Passport preview
    if (path === "/api/soul/preview" && req.method === "POST") {
      const body = (await req.json()) as { export_path: string; source_type?: string };
      const result = await bridge.call("soul.preview", body);
      return Response.json(result, { headers });
    }

    // Soul Passport import
    if (path === "/api/soul/import" && req.method === "POST") {
      const body = (await req.json()) as { export_path: string; source_type?: string };
      const result = await bridge.call("soul.import", body);
      return Response.json(result, { headers });
    }

    // SMS webhook (Twilio inbound)
    if (path === "/api/sms/webhook" && req.method === "POST") {
      const body = await req.json();
      const result = await bridge.call("sms.inbound", body);
      return new Response(result.twiml || "", {
        headers: { "Content-Type": "text/xml", ...headers },
      });
    }

    // SMS send (outbound)
    if (path === "/api/sms/send" && req.method === "POST") {
      const body = (await req.json()) as { to: string; message: string };
      const result = await bridge.call("sms.send", body);
      return Response.json(result, { headers });
    }

    // Email webhook (SendGrid Inbound Parse)
    if (path === "/api/email/webhook" && req.method === "POST") {
      const body = await req.json();
      const result = await bridge.call("email.inbound", body);
      return Response.json(result, { headers });
    }

    // Email send (outbound)
    if (path === "/api/email/send" && req.method === "POST") {
      const body = (await req.json()) as { to: string; subject: string; body: string };
      const result = await bridge.call("email.send", body);
      return Response.json(result, { headers });
    }

    // Journal entries
    if (path === "/api/journal" && req.method === "GET") {
      const result = await bridge.call("journal.list");
      return Response.json(result, { headers });
    }

    // Self-assessment
    if (path === "/api/assessment" && req.method === "POST") {
      const result = await bridge.call("assessment.run");
      return Response.json(result, { headers });
    }

    // Shape-shift execute
    if (path === "/api/shape-shift" && req.method === "POST") {
      const body = (await req.json()) as { preset: string };
      const result = await bridge.call("shape_shift.execute", body);
      return Response.json(result, { headers });
    }

    // Shape-shift restore
    if (path === "/api/shape-shift/restore" && req.method === "POST") {
      const body = (await req.json()) as { sliders?: Record<string, number> };
      const result = await bridge.call("shape_shift.restore", body);
      return Response.json(result, { headers });
    }

    // Static files — serve from public/
    if (!path.startsWith("/api/")) {
      const filePath = path === "/" ? "index.html" : path.slice(1);
      const file = Bun.file(resolve(PUBLIC_DIR, filePath));
      if (await file.exists()) {
        return new Response(file);
      }
      // SPA fallback — serve index.html for unknown routes
      const index = Bun.file(resolve(PUBLIC_DIR, "index.html"));
      if (await index.exists()) {
        return new Response(index);
      }
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
