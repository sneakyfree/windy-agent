/**
 * Bun HTTP + WebSocket server for Windy Fly gateway.
 *
 * Routes:
 *   GET  /                             → Trust Dashboard (index.html)
 *   GET  /api/health                   → { status: "ok" }
 *   GET  /api/sliders                  → proxy to UDS sliders.get
 *   GET  /api/sliders/info             → proxy to UDS sliders.info
 *   PUT  /api/sliders/:name            → proxy to UDS sliders.set
 *   GET  /api/cost/daily               → proxy to UDS cost.daily
 *   GET  /api/intents                  → proxy to UDS intents.list
 *   GET  /api/dashboard                → proxy to UDS dashboard.summary
 *   GET  /api/memory/search            → proxy to UDS memory.search
 *   GET  /api/personality/history      → proxy to UDS personality.history
 *   POST /api/personality/snapshot     → proxy to UDS personality.snapshot
 *   GET  /api/personality/drift        → proxy to UDS personality.drift
 *   POST /api/personality/rollback     → proxy to UDS personality.rollback
 *   GET  /api/skills                   → proxy to UDS skills.list
 *   POST /api/skills                   → proxy to UDS skills.create
 *   POST /api/skills/:id/evaluate      → proxy to UDS skills.evaluate
 *   POST /api/skills/:id/promote       → proxy to UDS skills.promote
 *   POST /api/skills/:id/rollback      → proxy to UDS skills.rollback
 *   POST /api/skills/:id/golden-tests  → proxy to UDS skills.golden_tests
 *   POST /api/skills/regression        → proxy to UDS skills.regression
 *   POST /api/decay/run               → proxy to UDS decay.run
 *   GET  /api/conflicts               → proxy to UDS conflicts.list
 *   POST /api/conflicts/:id/resolve   → proxy to UDS conflicts.resolve
 *   GET  /api/moments                 → proxy to UDS moments.list
 *   GET  /api/failures                → proxy to UDS failures.list
 *   GET  /api/mode                    → proxy to UDS mode.get
 *   PUT  /api/mode                    → proxy to UDS mode.set
 *   GET  /api/offline/status          → proxy to UDS offline.status
 *   GET  /api/events                  → proxy to UDS events.list
 *   WS   /ws/chat                     → WebSocket chat
 */

import { resolve } from "path";
import { bridge } from "./bridge";
import { handleClose, handleMessage, handleWebSocket } from "./websocket";
import * as providers from "./providers";
import * as machines from "./machines";

const PORT = 3000;
const PUBLIC_DIR = resolve(import.meta.dir, "../public");

// ── Security: Rate limiting for setup routes ──────────────────────
const rateLimitMap = new Map<string, { count: number; resetAt: number }>();
const RATE_LIMIT_WINDOW_MS = 60_000; // 1 minute
const RATE_LIMIT_MAX = 10; // 10 requests per minute per IP

function isRateLimited(ip: string): boolean {
  const now = Date.now();
  const entry = rateLimitMap.get(ip);
  if (!entry || now > entry.resetAt) {
    rateLimitMap.set(ip, { count: 1, resetAt: now + RATE_LIMIT_WINDOW_MS });
    return false;
  }
  entry.count++;
  return entry.count > RATE_LIMIT_MAX;
}

// ── Security: Input sanitization ──────────────────────────────────
const VALID_PRESETS = [
  "buddy", "engineer", "powerhouse", "coder",
  "friend", "writer", "researcher", "silent",
];
const VALID_KEY_NAMES = [
  "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY",
  "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY",
];

function sanitizeForToml(s: string): string {
  // Strip characters that could escape TOML string literals
  return s.replace(/[\\"/\n\r\t\x00-\x1f]/g, "");
}

function isLocalhostRequest(req: Request): boolean {
  const url = new URL(req.url);
  const host = url.hostname;
  return host === "localhost" || host === "127.0.0.1" || host === "::1";
}

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

    // Sliders GET — fallback to defaults if brain offline
    if (path === "/api/sliders" && req.method === "GET") {
      try {
        const result = await bridge.call("sliders.get");
        return Response.json(result, { headers });
      } catch {
        // Brain offline — return defaults (all sliders at 5)
        const defaults: Record<string, number> = {};
        const names = [
          "personality", "humor", "formality", "reasoning_depth",
          "creativity", "memory_depth", "context_window", "proactivity",
          "autonomy", "verbosity", "response_length", "epistemic_strictness",
          "tool_reloop_rounds", "emotional_sensitivity", "memory_retention",
          "warmth", "adaptive_mode", "shape_shift_bias",
        ];
        for (const n of names) defaults[n] = 5;
        return Response.json({ sliders: defaults, _offline: true }, { headers });
      }
    }

    // Sliders SET
    const sliderMatch = path.match(/^\/api\/sliders\/(.+)$/);
    if (sliderMatch && req.method === "PUT") {
      const name = sliderMatch[1];
      const body = (await req.json()) as { value: number };
      try {
        const result = await bridge.call("sliders.set", {
          name,
          value: body.value,
        });
        return Response.json(result, { headers });
      } catch {
        return Response.json(
          { error: "Brain offline — slider change not saved", _offline: true },
          { status: 503, headers }
        );
      }
    }

    // Sliders INFO — fallback to static metadata if brain offline
    if (path === "/api/sliders/info" && req.method === "GET") {
      try {
        const result = await bridge.call("sliders.info");
        return Response.json(result, { headers });
      } catch {
        // Return static metadata so the UI can render slider controls
        const info: Record<string, Record<string, string | number>> = {
          personality: { label: "Personality", description: "How much warmth, character, and soul the agent puts into responses.", impact_low: "Robotic, clinical responses. Zero flair. Saves ~3% of tokens.", impact_high: "Full SOUL.md personality, warm, human-like. Costs ~3% more tokens.", cost_per_point: 0.3 },
          humor: { label: "Humor", description: "How much humor, wit, and playfulness the agent brings.", impact_low: "Stick-in-the-mud. No jokes, no riffing. Pure business.", impact_high: "Jim Carrey energy. Cracks jokes, riffs on your style, keeps it fun.", cost_per_point: 0.1 },
          formality: { label: "Formality", description: "Tone register — from casual texting to boardroom professional.", impact_low: "\"yo what's good\" — relaxed, slang-friendly.", impact_high: "\"Dear esteemed colleague\" — proper grammar, corporate-ready.", cost_per_point: 0 },
          reasoning_depth: { label: "Reasoning Depth", description: "How much the agent shows its thinking process.", impact_low: "Quick gut-reaction answers. Fast but no explanation.", impact_high: "Full chain-of-thought reasoning. ~20% more tokens.", cost_per_point: 2.0 },
          creativity: { label: "Creativity", description: "Controls LLM temperature — how predictable vs. imaginative responses are.", impact_low: "Precise, deterministic. Same question = same answer.", impact_high: "Wild, varied, surprising responses. Great for brainstorming.", cost_per_point: 0 },
          memory_depth: { label: "Memory Depth", description: "How many knowledge facts are injected into every conversation.", impact_low: "Remembers almost nothing about you. Light and fast.", impact_high: "Full life-graph recall. Costs ~5-10% of your token budget.", cost_per_point: 1.0 },
          context_window: { label: "Context Window", description: "How many past messages the agent carries in the conversation.", impact_low: "5 recent messages. Short memory. Very cheap.", impact_high: "50 recent messages. Full conversation recall. Burns ~15% tokens.", cost_per_point: 1.5 },
          proactivity: { label: "Proactivity", description: "Whether the agent volunteers ideas or only answers what's asked.", impact_low: "Only answers your exact question. Never suggests.", impact_high: "Actively suggests ideas, flags things you might have missed.", cost_per_point: 0.5 },
          autonomy: { label: "Autonomy", description: "How much the agent acts on its own vs. asking permission first.", impact_low: "Always asks before doing anything. Maximum control.", impact_high: "Takes initiative — executes tasks independently. Use with caution.", cost_per_point: 0.5 },
          verbosity: { label: "Verbosity", description: "Response style — from terse one-liners to thorough explanations.", impact_low: "Bullet points and one-liners. Maximum density.", impact_high: "Rich, detailed responses with examples. ~30% more tokens.", cost_per_point: 3.0 },
          response_length: { label: "Response Length", description: "Hard cap on how long each response can be (token limit).", impact_low: "250 token cap (~2 paragraphs max). Fast and cheap.", impact_high: "4,000 token cap (~3 pages). Directly scales cost.", cost_per_point: 4.0 },
          epistemic_strictness: { label: "Epistemic Strictness", description: "How much the agent trusts its own memory vs. only citing verified facts.", impact_low: "Uses everything it remembers, even hunches.", impact_high: "Only cites verified facts. Refuses to guess.", cost_per_point: 0 },
          tool_reloop_rounds: { label: "Tool Use Depth", description: "Max rounds of tool execution per response.", impact_low: "1 round — one tool call, then answers. Fast.", impact_high: "10 rounds — deep research, chaining tool calls. Burns 2-10x tokens.", cost_per_point: 5.0 },
          emotional_sensitivity: { label: "Emotional Sensitivity", description: "How attuned the agent is to your emotional state.", impact_low: "Ignores your mood entirely. Pure information.", impact_high: "Detects frustration, adjusts tone, offers support.", cost_per_point: 0.1 },
          memory_retention: { label: "Memory Retention", description: "How long the agent holds onto old memories before they fade.", impact_low: "Goldfish 🐠 — aggressive forgetting, old facts decay fast.", impact_high: "Elephant 🐘 — never forgets. Old memories maintained indefinitely.", cost_per_point: 1.0 },
          warmth: { label: "Warmth", description: "How emotionally warm and supportive the agent is.", impact_low: "Clinical, detached. Facts only.", impact_high: "Warm, caring, empathetic. Like a close friend.", cost_per_point: 0.1 },
          adaptive_mode: { label: "Adaptive Mode", description: "When ON, the agent reads your mood and temporarily adjusts its personality.", impact_low: "Sliders stay exactly where you set them.", impact_high: "Agent 'reads the room' — softens when you're stressed.", cost_per_point: 0.2 },
          shape_shift_bias: { label: "Shape-Shift Bias", description: "Controls whether the agent reconfigures itself or spawns a sub-agent.", impact_low: "Always spawns a separate sub-agent. Clean slate, 2x tokens.", impact_high: "Always shape-shifts in place. Keeps all memory, half the cost.", cost_per_point: -2.0 },
        };
        return Response.json({ sliders: info, _offline: true }, { headers });
      }
    }

    // Cost daily
    if (path === "/api/cost/daily" && req.method === "GET") {
      try {
        const result = await bridge.call("cost.daily");
        return Response.json(result, { headers });
      } catch {
        return Response.json({ daily_spend: 0, _offline: true }, { headers });
      }
    }

    // Intents list
    if (path === "/api/intents" && req.method === "GET") {
      try {
        const result = await bridge.call("intents.list");
        return Response.json(result, { headers });
      } catch {
        return Response.json({ intents: [], _offline: true }, { headers });
      }
    }

    // Dashboard summary (G3)
    if (path === "/api/dashboard" && req.method === "GET") {
      try {
        const result = await bridge.call("dashboard.summary");
        return Response.json(result, { headers });
      } catch {
        return Response.json({
          dashboard: {
            memory: { total_nodes: 0, total_episodes: 0, by_scope: {} },
            costs: { today_usd: 0, this_week_usd: 0, this_month_usd: 0 },
            failures: { unresolved: 0, resolved: 0, improvement_rate: 1 },
            skills: { total: 0, promoted: 0, top_5_by_usage: [] },
            intents: { active: 0, completed: 0, abandoned: 0 },
            personality: { sliders: {}, preset: "offline", estimated_monthly_cost: 0 },
          },
          _offline: true,
        }, { headers });
      }
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

    // Providers list — works locally, no brain needed
    if (path === "/api/providers" && req.method === "GET") {
      return Response.json(providers.listProviders(), { headers });
    }

    // Discover models from a provider's API
    if (path === "/api/providers/discover" && req.method === "POST") {
      const body = await req.json() as { provider: string };
      const result = await providers.discoverModels(body.provider);
      return Response.json(result, { headers });
    }

    // Discover models for ALL configured providers
    if (path === "/api/providers/discover-all" && req.method === "POST") {
      const { providers: list } = providers.listProviders();
      const configured = list.filter(p => p.configured);
      const results = await Promise.allSettled(
        configured.map(p => providers.discoverModels(p.key))
      );
      const discoveries = results.map((r, i) =>
        r.status === "fulfilled" ? r.value : { provider: configured[i].key, models: [], error: String(r.reason), fetched_at: new Date().toISOString() }
      );
      return Response.json({ discoveries }, { headers });
    }

    // Validate a provider key
    if (path === "/api/providers/validate" && req.method === "POST") {
      const body = await req.json() as { provider: string };
      const result = await providers.validateKey(body.provider);
      return Response.json(result, { headers });
    }

    // OpenRouter OAuth: start
    if (path === "/api/providers/oauth/openrouter/start" && req.method === "POST") {
      const body = await req.json() as { callback_url: string };
      const result = providers.startOpenRouterOAuth(body.callback_url);
      return Response.json(result, { headers });
    }

    // OpenRouter OAuth: callback
    if (path === "/api/providers/oauth/openrouter/callback" && req.method === "POST") {
      const body = await req.json() as { code: string };
      const result = await providers.completeOpenRouterOAuth(body.code);
      return Response.json(result, { headers });
    }

    // OpenRouter OAuth: handle GET callback (redirect from OpenRouter)
    if (path === "/oauth/callback" && req.method === "GET") {
      const code = url.searchParams.get("code");
      if (code) {
        const result = await providers.completeOpenRouterOAuth(code);
        // Redirect back to providers page with result
        const status = result.success ? "success" : "error";
        const msg = result.success ? "Connected to OpenRouter!" : (result.error || "Failed");
        return new Response(null, {
          status: 302,
          headers: { ...headers, "Location": `/?page=providers&oauth=${status}&msg=${encodeURIComponent(msg)}` },
        });
      }
      return new Response(null, {
        status: 302,
        headers: { ...headers, "Location": "/?page=providers&oauth=error&msg=No+code+received" },
      });
    }

    // Set active model — must match before the generic /api/providers/:key
    if (path === "/api/providers/active-model" && req.method === "PUT") {
      const body = await req.json() as { model: string };
      providers.setActiveModel(body.model);
      return Response.json({ success: true, active_model: body.model }, { headers });
    }

    // Set provider API key (legacy single-key)
    if (path === "/api/providers/set-key" && req.method === "PUT") {
      const body = await req.json() as { key: string; api_key: string; api_key_env?: string };
      providers.setProviderKey(body.key, body.api_key, body.api_key_env);
      return Response.json({ success: true }, { headers });
    }

    // Multi-key: add key
    if (path === "/api/providers/keys" && req.method === "POST") {
      const body = await req.json() as { provider: string; label: string; key: string; type: "api" | "oauth" | "other" };
      providers.addProviderKey(body.provider, body.label, body.key, body.type);
      return Response.json({ success: true }, { headers });
    }

    // Multi-key: delete key
    if (path === "/api/providers/keys" && req.method === "DELETE") {
      const body = await req.json() as { provider: string; key_id: string };
      providers.deleteProviderKey(body.provider, body.key_id);
      return Response.json({ success: true }, { headers });
    }

    // Multi-key: activate key
    if (path === "/api/providers/keys/activate" && req.method === "PUT") {
      const body = await req.json() as { provider: string; key_id: string };
      providers.activateProviderKey(body.provider, body.key_id);
      return Response.json({ success: true }, { headers });
    }

    // Notes: save
    if (path === "/api/providers/notes" && req.method === "PUT") {
      const body = await req.json() as { provider: string; note: string };
      providers.setProviderNote(body.provider, body.note);
      return Response.json({ success: true }, { headers });
    }

    // Provider CRUD by key
    const providerMatch = path.match(/^\/api\/providers\/(.+)$/);
    if (providerMatch && req.method === "PUT") {
      const key = providerMatch[1];
      const body = await req.json() as Record<string, unknown>;
      providers.updateProvider(key, body);
      return Response.json({ success: true }, { headers });
    }

    // Add custom provider
    if (path === "/api/providers" && req.method === "POST") {
      const body = await req.json() as { key: string; [k: string]: unknown };
      const key = body.key;
      delete body.key;
      providers.addProvider(key, body);
      return Response.json({ success: true }, { headers });
    }

    // Remove custom provider
    if (providerMatch && req.method === "DELETE") {
      const key = providerMatch[1];
      const removed = providers.removeProvider(key);
      return Response.json({ success: removed }, { headers });
    }

    // ===== MISSION CONTROL: Machines =====

    // Sync providers to remote machines (must be before generic POST /api/machines)
    if (path === "/api/machines/sync-providers" && req.method === "POST") {
      const body = await req.json() as { machine_id?: string };
      const results = await machines.syncProviders(body.machine_id);
      return Response.json(results, { headers });
    }

    // List all machines with status
    if (path === "/api/machines" && req.method === "GET") {
      try {
        return Response.json(machines.listMachines(), { headers });
      } catch (e) {
        return Response.json([], { headers });
      }
    }

    // Add a new machine
    if (path === "/api/machines" && req.method === "POST") {
      const body = await req.json() as { name: string; host: string; port?: number; token?: string; tags?: string[]; notes?: string };
      const machine = machines.addMachine({
        name: body.name,
        host: body.host,
        port: body.port || 3100,
        token: body.token || "",
        tags: body.tags || [],
        notes: body.notes || "",
      });
      return Response.json(machine, { headers });
    }

    // Get single machine status
    const machineMatch = path.match(/^\/api\/machines\/([^/]+)$/);
    if (machineMatch && req.method === "GET") {
      const status = machines.getMachine(machineMatch[1]);
      if (!status) return Response.json({ error: "Machine not found" }, { status: 404, headers });
      return Response.json(status, { headers });
    }

    // Update machine config
    if (machineMatch && req.method === "PUT") {
      const body = await req.json() as Partial<machines.MachineConfig>;
      const updated = machines.updateMachine(machineMatch[1], body);
      if (!updated) return Response.json({ error: "Machine not found" }, { status: 404, headers });
      return Response.json(updated, { headers });
    }

    // Remove machine
    if (machineMatch && req.method === "DELETE") {
      const removed = machines.removeMachine(machineMatch[1]);
      return Response.json({ success: removed }, { headers });
    }

    // Restart a service on a remote machine
    const restartMatch = path.match(/^\/api\/machines\/([^/]+)\/restart-(gateway|brain)$/);
    if (restartMatch && req.method === "POST") {
      try {
        const result = await machines.sendToMachine(restartMatch[1], {
          type: "restart",
          service: restartMatch[2],
        });
        return Response.json(result, { headers });
      } catch (e) {
        return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status: 502, headers });
      }
    }

    // Get remote machine health
    const healthMatch = path.match(/^\/api\/machines\/([^/]+)\/health$/);
    if (healthMatch && req.method === "GET") {
      try {
        const result = await machines.sendToMachine(healthMatch[1], { type: "health" });
        return Response.json(result, { headers });
      } catch (e) {
        return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status: 502, headers });
      }
    }

    // Execute command on remote machine
    const execMatch = path.match(/^\/api\/machines\/([^/]+)\/exec$/);
    if (execMatch && req.method === "POST") {
      const body = await req.json() as { command: string };
      try {
        const result = await machines.sendToMachine(execMatch[1], {
          type: "exec",
          command: body.command,
        });
        return Response.json(result, { headers });
      } catch (e) {
        return Response.json({ error: e instanceof Error ? e.message : String(e) }, { status: 502, headers });
      }
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

    // ===== PERSONALITY VERSIONING =====

    // Personality history
    if (path === "/api/personality/history" && req.method === "GET") {
      const limit = parseInt(url.searchParams.get("limit") || "20");
      const result = await bridge.call("personality.history", { limit });
      return Response.json(result, { headers });
    }

    // Personality snapshot
    if (path === "/api/personality/snapshot" && req.method === "POST") {
      const body = (await req.json()) as { changed_by?: string };
      const result = await bridge.call("personality.snapshot", body);
      return Response.json(result, { headers });
    }

    // Personality drift detection
    if (path === "/api/personality/drift" && req.method === "GET") {
      const result = await bridge.call("personality.drift", {});
      return Response.json(result, { headers });
    }

    // Personality rollback
    if (path === "/api/personality/rollback" && req.method === "POST") {
      const body = (await req.json()) as { snapshot_date: string };
      const result = await bridge.call("personality.rollback", body);
      return Response.json(result, { headers });
    }

    // ===== SKILLS MANAGEMENT =====

    // Skills list
    if (path === "/api/skills" && req.method === "GET") {
      const promoted = url.searchParams.get("promoted") === "true";
      const result = await bridge.call("skills.list", { promoted_only: promoted });
      return Response.json(result, { headers });
    }

    // Skills create
    if (path === "/api/skills" && req.method === "POST") {
      const body = (await req.json()) as { name: string; code: string; language?: string };
      const result = await bridge.call("skills.create", body);
      return Response.json(result, { headers });
    }

    // Skills evaluate
    const skillEvalMatch = path.match(/^\/api\/skills\/([^/]+)\/evaluate$/);
    if (skillEvalMatch && req.method === "POST") {
      const result = await bridge.call("skills.evaluate", { skill_id: skillEvalMatch[1] });
      return Response.json(result, { headers });
    }

    // Skills promote
    const skillPromoteMatch = path.match(/^\/api\/skills\/([^/]+)\/promote$/);
    if (skillPromoteMatch && req.method === "POST") {
      const result = await bridge.call("skills.promote", { skill_id: skillPromoteMatch[1] });
      return Response.json(result, { headers });
    }

    // Skills rollback
    const skillRollbackMatch = path.match(/^\/api\/skills\/([^/]+)\/rollback$/);
    if (skillRollbackMatch && req.method === "POST") {
      const result = await bridge.call("skills.rollback", { skill_id: skillRollbackMatch[1] });
      return Response.json(result, { headers });
    }

    // Skills golden tests
    const skillGoldenMatch = path.match(/^\/api\/skills\/([^/]+)\/golden-tests$/);
    if (skillGoldenMatch && req.method === "POST") {
      const result = await bridge.call("skills.golden_tests", { skill_id: skillGoldenMatch[1] });
      return Response.json(result, { headers });
    }

    // Skills regression suite
    if (path === "/api/skills/regression" && req.method === "POST") {
      const result = await bridge.call("skills.regression", {});
      return Response.json(result, { headers });
    }

    // ===== DECAY, CONFLICTS, MOMENTS, FAILURES =====

    // Cognitive decay trigger
    if (path === "/api/decay/run" && req.method === "POST") {
      const result = await bridge.call("decay.run", {});
      return Response.json(result, { headers });
    }

    // Conflicts list
    if (path === "/api/conflicts" && req.method === "GET") {
      const result = await bridge.call("conflicts.list", {});
      return Response.json(result, { headers });
    }

    // Conflicts resolve
    const conflictMatch = path.match(/^\/api\/conflicts\/([^/]+)\/resolve$/);
    if (conflictMatch && req.method === "POST") {
      const body = (await req.json()) as { resolution: string; keep_new: boolean };
      const result = await bridge.call("conflicts.resolve", {
        conflict_id: conflictMatch[1],
        ...body,
      });
      return Response.json(result, { headers });
    }

    // Relationship moments
    if (path === "/api/moments" && req.method === "GET") {
      const limit = parseInt(url.searchParams.get("limit") || "20");
      const result = await bridge.call("moments.list", { limit });
      return Response.json(result, { headers });
    }

    // Failures list
    if (path === "/api/failures" && req.method === "GET") {
      const limit = parseInt(url.searchParams.get("limit") || "20");
      const result = await bridge.call("failures.list", { limit });
      return Response.json(result, { headers });
    }

    // ===== MODE, OFFLINE, EVENTS =====

    // Mode get
    if (path === "/api/mode" && req.method === "GET") {
      const result = await bridge.call("mode.get", {});
      return Response.json(result, { headers });
    }

    // Mode set
    if (path === "/api/mode" && req.method === "PUT") {
      const body = (await req.json()) as { mode: string };
      const result = await bridge.call("mode.set", body);
      return Response.json(result, { headers });
    }

    // Offline status
    if (path === "/api/offline/status" && req.method === "GET") {
      const result = await bridge.call("offline.status", {});
      return Response.json(result, { headers });
    }

    // Events list
    if (path === "/api/events" && req.method === "GET") {
      const eventType = url.searchParams.get("type") || undefined;
      const limit = parseInt(url.searchParams.get("limit") || "50");
      const result = await bridge.call("events.list", { event_type: eventType, limit });
      return Response.json(result, { headers });
    }

    // ── Setup Wizard API ───────────────────────────────────────
    // Security: All setup routes are restricted to localhost only.
    // These routes write configuration files to disk — allowing remote
    // access would enable unauthorized environment overwrites.
    if (path.startsWith("/api/setup/") && !isLocalhostRequest(req)) {
      return Response.json(
        { error: "Setup routes are only accessible from localhost" },
        { status: 403, headers }
      );
    }

    if (path === "/api/setup/status" && req.method === "GET") {
      const fs = await import("fs");
      const pathMod = await import("path");
      const projectRoot = pathMod.resolve(import.meta.dir, "../..");
      const envExists = fs.existsSync(pathMod.join(projectRoot, ".env"));
      const tomlExists = fs.existsSync(pathMod.join(projectRoot, "windyfly.toml"));

      // Detect which keys are already set in env
      const knownKeys = [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY",
        "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY",
      ];
      const existingKeys = knownKeys.filter(k => {
        const v = process.env[k];
        return v && v.length > 8 && !v.startsWith("sk-xxx");
      });

      return Response.json({
        configured: envExists && tomlExists,
        existing_keys: existingKeys,
      }, { headers });
    }

    if (path === "/api/setup/validate-key" && req.method === "POST") {
      // Rate limit: prevent brute-force key validation abuse
      const clientIP = req.headers.get("x-forwarded-for") || "127.0.0.1";
      if (isRateLimited(clientIP)) {
        return Response.json(
          { error: "Rate limited. Try again in 1 minute." },
          { status: 429, headers }
        );
      }

      const body = (await req.json()) as { key_name: string; key_value: string };

      // Input validation: only accept known key names
      if (!VALID_KEY_NAMES.includes(body.key_name)) {
        return Response.json(
          { error: `Invalid key name: ${body.key_name}` },
          { status: 400, headers }
        );
      }

      // Input validation: key values must be reasonable length
      if (!body.key_value || body.key_value.length < 5 || body.key_value.length > 500) {
        return Response.json(
          { error: "Key value must be between 5 and 500 characters" },
          { status: 400, headers }
        );
      }

      let valid = false;
      try {
        if (body.key_name === "OPENAI_API_KEY") {
          const r = await fetch("https://api.openai.com/v1/models", {
            headers: { Authorization: `Bearer ${body.key_value}` },
          });
          valid = r.status === 200;
        } else if (body.key_name === "ANTHROPIC_API_KEY") {
          const r = await fetch("https://api.anthropic.com/v1/messages", {
            method: "POST",
            headers: {
              "x-api-key": body.key_value,
              "anthropic-version": "2023-06-01",
              "content-type": "application/json",
            },
            body: JSON.stringify({
              model: "claude-3-5-haiku-latest",
              max_tokens: 1,
              messages: [{ role: "user", content: "hi" }],
            }),
          });
          valid = r.status === 200 || r.status === 400;
        } else if (body.key_name === "GROK_API_KEY") {
          const r = await fetch("https://api.x.ai/v1/models", {
            headers: { Authorization: `Bearer ${body.key_value}` },
          });
          valid = r.status === 200;
        } else {
          valid = body.key_value.length > 10;
        }
      } catch {
        valid = false;
      }
      return Response.json({ valid }, { headers });
    }

    if (path === "/api/setup/finalize" && req.method === "POST") {
      const fs = await import("fs");
      const pathMod = await import("path");
      const projectRoot = pathMod.resolve(import.meta.dir, "../..");

      const body = (await req.json()) as {
        api_keys: Record<string, string>;
        model: string;
        preset: string;
      };

      // ── Input Validation ──────────────────────────────────────
      // Prevent TOML injection and invalid preset selection
      if (!body.model || typeof body.model !== "string" || body.model.length > 100) {
        return Response.json(
          { ok: false, error: "Invalid model name" },
          { status: 400, headers }
        );
      }
      if (!body.preset || !VALID_PRESETS.includes(body.preset)) {
        return Response.json(
          { ok: false, error: `Invalid preset. Must be one of: ${VALID_PRESETS.join(", ")}` },
          { status: 400, headers }
        );
      }
      if (!body.api_keys || typeof body.api_keys !== "object") {
        return Response.json(
          { ok: false, error: "api_keys must be an object" },
          { status: 400, headers }
        );
      }
      // Reject unknown key names
      for (const keyName of Object.keys(body.api_keys)) {
        if (!VALID_KEY_NAMES.includes(keyName)) {
          return Response.json(
            { ok: false, error: `Unknown API key: ${keyName}` },
            { status: 400, headers }
          );
        }
      }
      // Reject overly long key values (possible injection)
      for (const [k, v] of Object.entries(body.api_keys)) {
        if (typeof v !== "string" || (v.length > 500 && v !== "__existing__")) {
          return Response.json(
            { ok: false, error: `Key value for ${k} is too long` },
            { status: 400, headers }
          );
        }
      }

      // Sanitize model and preset for TOML string interpolation
      const safeModel = sanitizeForToml(body.model);
      const safePreset = body.preset; // Already validated against whitelist

      try {
        // Ensure data directory exists
        const dataDir = pathMod.join(projectRoot, "data");
        if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });

        // Personality preset data
        const presets: Record<string, { humor: number; warmth: number; formality: number }> = {
          buddy:      { humor: 7, warmth: 9, formality: 3 },
          engineer:   { humor: 2, warmth: 4, formality: 7 },
          powerhouse: { humor: 5, warmth: 6, formality: 5 },
          coder:      { humor: 2, warmth: 3, formality: 5 },
          friend:     { humor: 5, warmth: 10, formality: 2 },
          writer:     { humor: 6, warmth: 7, formality: 6 },
          researcher: { humor: 1, warmth: 3, formality: 8 },
          silent:     { humor: 0, warmth: 2, formality: 5 },
        };
        const p = presets[body.preset] || presets.buddy;

        // Build .env content
        const providerKeys = [
          "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY",
          "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "MISTRAL_API_KEY",
        ];
        const envLines = [
          "# Windy Fly — generated by setup wizard",
          `DEFAULT_MODEL=${safeModel}`,
          "",
          "# LLM Providers",
        ];
        for (const k of providerKeys) {
          const val = body.api_keys[k] || "";
          // Don't overwrite existing keys with the placeholder
          if (val === "__existing__") {
            envLines.push(`${k}=${process.env[k] || ""}`);
          } else {
            envLines.push(`${k}=${val}`);
          }
        }
        envLines.push(
          "",
          "# Database",
          "WINDYFLY_DB_PATH=data/windyfly.db",
          "",
          "# Logging",
          "LOG_LEVEL=INFO",
          "",
          "# Matrix / Windy Chat (optional)",
          "MATRIX_HOMESERVER=https://chat.windypro.com",
          "MATRIX_BOT_USER=@windyfly:chat.windypro.com",
          "MATRIX_BOT_TOKEN=",
          "MATRIX_BOT_PASSWORD=",
          "",
          "# Windy Pro API (optional)",
          "WINDY_API_URL=http://localhost:8098",
          "WINDY_JWT=",
        );
        fs.writeFileSync(pathMod.join(projectRoot, ".env"), envLines.join("\n") + "\n");

        // Build windyfly.toml
        const toml = `[agent]
name = "Windy Fly"
default_model = "${safeModel}"
max_context_tokens = 8000
max_response_tokens = 2000
temperature = 0.7

[memory]
db_path = "data/windyfly.db"
max_episodes_per_context = 20
max_nodes_per_context = 10

[personality]
soul_path = "SOUL.md"
preset = "${safePreset}"
humor_level = ${p.humor}
formality = ${p.formality}
proactivity = 5
verbosity = 5
reasoning_depth = 6
autonomy = 3
epistemic_strictness = 5
warmth = ${p.warmth}

[costs]
daily_budget_usd = 5.0
warn_at_usd = 0.50

[matrix]
homeserver = "https://chat.windypro.com"
bot_user = "@windyfly:chat.windypro.com"

[windy_api]
base_url = "http://localhost:8098"
`;
        fs.writeFileSync(pathMod.join(projectRoot, "windyfly.toml"), toml);

        return Response.json({ ok: true }, { headers });
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        return Response.json({ ok: false, error }, { status: 500, headers });
      }
    }

    if (path === "/api/setup/launch" && req.method === "POST") {
      // The gateway is already running. Notify the brain to reload config.
      // If the brain isn't connected, that's fine — it'll pick up the new
      // config on next start.
      try {
        await bridge.call("config.reload", {});
      } catch { /* brain offline — expected during initial setup */ }
      return Response.json({ ok: true, dashboard: "http://localhost:3000" }, { headers });
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

  // Initialize Mission Control machine connections
  machines.initMachines();

  const server = Bun.serve({
    port: PORT,
    fetch(req, server) {
      const pathname = new URL(req.url).pathname;

      // Upgrade WebSocket requests — chat
      if (pathname === "/ws/chat") {
        if (server.upgrade(req, { data: { type: "chat" } })) return;
        return new Response("WebSocket upgrade failed", { status: 400 });
      }

      // Upgrade WebSocket requests — terminal relay to remote machine
      const termMatch = pathname.match(/^\/ws\/terminal\/(.+)$/);
      if (termMatch) {
        if (server.upgrade(req, { data: { type: "terminal", machineId: termMatch[1] } })) return;
        return new Response("WebSocket upgrade failed", { status: 400 });
      }

      // Upgrade WebSocket requests — machine event stream
      const machineWsMatch = pathname.match(/^\/ws\/machine\/(.+)$/);
      if (machineWsMatch) {
        if (server.upgrade(req, { data: { type: "machine", machineId: machineWsMatch[1] } })) return;
        return new Response("WebSocket upgrade failed", { status: 400 });
      }

      return handleRequest(req);
    },
    websocket: {
      open(ws) {
        const data = (ws as any).data as { type: string; machineId?: string };
        if (data?.type === "terminal" || data?.type === "machine") {
          console.log(`[ws] ${data.type} client connected for machine ${data.machineId}`);
          if (data.machineId) {
            // Subscribe to machine events
            const unsub = machines.subscribeTo(data.machineId, {
              send: (msg: string) => ws.send(msg),
            });
            (ws as any)._unsub = unsub;

            // For terminal: auto-create a PTY session on the remote
            if (data.type === "terminal") {
              machines.relayToMachine(data.machineId, JSON.stringify({ type: "pty:create" }));
            }
          }
        } else {
          handleWebSocket(ws);
        }
      },
      message(ws, message) {
        const data = (ws as any).data as { type: string; machineId?: string };
        if ((data?.type === "terminal" || data?.type === "machine") && data.machineId) {
          // Relay to remote machine
          const raw = typeof message === "string" ? message : message.toString();
          machines.relayToMachine(data.machineId, raw);
        } else {
          handleMessage(ws, message as string);
        }
      },
      close(ws) {
        const data = (ws as any).data as { type: string; machineId?: string };
        if (data?.type === "terminal" || data?.type === "machine") {
          const unsub = (ws as any)._unsub;
          if (typeof unsub === "function") unsub();
        } else {
          handleClose(ws);
        }
      },
    },
  });

  console.log(`[gateway] 🪰 Windy Fly Gateway running on http://localhost:${PORT}`);
  console.log(`[gateway] WebSocket chat at ws://localhost:${PORT}/ws/chat`);
}

main();
