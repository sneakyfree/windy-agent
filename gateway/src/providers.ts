/**
 * Local provider manager for the gateway.
 *
 * Reads/writes provider config directly so the Providers tab works
 * even when the Python brain is offline. This is the source of truth
 * for provider configuration — the Python brain reads the same files.
 */

import { resolve } from "path";

const DATA_DIR = resolve(import.meta.dir, "../../data");
const OVERRIDES_PATH = resolve(DATA_DIR, "providers.json");
const CONFIG_PATH = resolve(import.meta.dir, "../../windyfly.toml");

interface Provider {
  key: string;
  name: string;
  type: "openai" | "anthropic";
  base_url: string;
  api_key_env: string;
  models: string[];
  builtin: boolean;
  has_key: boolean;
  configured: boolean;
  active: boolean;
}

// Built-in providers — mirrors providers.py
const BUILTIN_PROVIDERS: Record<string, Omit<Provider, "key" | "builtin" | "has_key" | "configured" | "active">> = {
  openai: {
    name: "OpenAI",
    type: "openai",
    base_url: "https://api.openai.com/v1",
    api_key_env: "OPENAI_API_KEY",
    models: ["gpt-5.4", "gpt-5.4-pro", "codex", "o3", "o3-mini", "gpt-4o", "gpt-4o-mini"],
  },
  anthropic: {
    name: "Anthropic",
    type: "anthropic",
    base_url: "https://api.anthropic.com",
    api_key_env: "ANTHROPIC_API_KEY",
    models: ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
  },
  grok: {
    name: "xAI Grok",
    type: "openai",
    base_url: "https://api.x.ai/v1",
    api_key_env: "GROK_API_KEY",
    models: ["grok-3", "grok-3-mini", "grok-2"],
  },
  gemini: {
    name: "Google Gemini",
    type: "openai",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    api_key_env: "GEMINI_API_KEY",
    models: ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
  },
  deepseek: {
    name: "DeepSeek",
    type: "openai",
    base_url: "https://api.deepseek.com/v1",
    api_key_env: "DEEPSEEK_API_KEY",
    models: ["deepseek-chat", "deepseek-reasoner", "deepseek-r1"],
  },
  mistral: {
    name: "Mistral",
    type: "openai",
    base_url: "https://api.mistral.ai/v1",
    api_key_env: "MISTRAL_API_KEY",
    models: ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest", "codestral-latest"],
  },
  openrouter: {
    name: "OpenRouter",
    type: "openai",
    base_url: "https://openrouter.ai/api/v1",
    api_key_env: "OPENROUTER_API_KEY",
    models: ["openrouter/auto", "google/gemini-2.5-pro", "anthropic/claude-sonnet-4"],
  },
  together: {
    name: "Together AI",
    type: "openai",
    base_url: "https://api.together.xyz/v1",
    api_key_env: "TOGETHER_API_KEY",
    models: ["meta-llama/Llama-3-70b-chat-hf", "mistralai/Mixtral-8x7B-Instruct-v0.1"],
  },
  groq: {
    name: "Groq",
    type: "openai",
    base_url: "https://api.groq.com/openai/v1",
    api_key_env: "GROQ_API_KEY",
    models: ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
  },
  perplexity: {
    name: "Perplexity",
    type: "openai",
    base_url: "https://api.perplexity.ai",
    api_key_env: "PERPLEXITY_API_KEY",
    models: ["sonar-pro", "sonar"],
  },
  fireworks: {
    name: "Fireworks AI",
    type: "openai",
    base_url: "https://api.fireworks.ai/inference/v1",
    api_key_env: "FIREWORKS_API_KEY",
    models: ["accounts/fireworks/models/llama-v3p1-70b-instruct"],
  },
  ollama: {
    name: "Ollama (Local)",
    type: "openai",
    base_url: "http://localhost:11434/v1",
    api_key_env: "",
    models: ["llama3", "mistral", "codellama"],
  },
};

function readOverrides(): Record<string, Record<string, unknown>> {
  try {
    const file = Bun.file(OVERRIDES_PATH);
    // Bun.file().text() is async, use readFileSync approach
    const fs = require("fs");
    if (fs.existsSync(OVERRIDES_PATH)) {
      return JSON.parse(fs.readFileSync(OVERRIDES_PATH, "utf-8"));
    }
  } catch {}
  return {};
}

function writeOverrides(data: Record<string, Record<string, unknown>>): void {
  const fs = require("fs");
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
  }
  fs.writeFileSync(OVERRIDES_PATH, JSON.stringify(data, null, 2));
}

function getActiveModel(): string {
  // Check overrides for active_model
  const overrides = readOverrides();
  if (overrides._settings?.active_model) {
    return overrides._settings.active_model as string;
  }
  // Try reading from windyfly.toml
  try {
    const fs = require("fs");
    const toml = fs.readFileSync(CONFIG_PATH, "utf-8");
    const match = toml.match(/default_model\s*=\s*"([^"]+)"/);
    if (match) return match[1];
  } catch {}
  return "gpt-4o-mini";
}

function setActiveModel(model: string): void {
  const overrides = readOverrides();
  overrides._settings = { ...(overrides._settings || {}), active_model: model };
  writeOverrides(overrides);
}

export function listProviders(): { providers: Provider[]; active_model: string } {
  const overrides = readOverrides();
  const activeModel = getActiveModel();
  const providers: Provider[] = [];

  // Merge builtins with overrides
  const allKeys = new Set([...Object.keys(BUILTIN_PROVIDERS), ...Object.keys(overrides).filter(k => k !== "_settings")]);

  for (const key of allKeys) {
    const builtin = BUILTIN_PROVIDERS[key];
    const override = overrides[key] || {};
    const merged = { ...(builtin || {}), ...override };

    const envVar = (merged.api_key_env || "") as string;
    const hasKey = !!(merged.api_key || (envVar && process.env[envVar]));
    const baseUrl = (merged.base_url || "") as string;
    const models = (merged.models || []) as string[];

    providers.push({
      key,
      name: (merged.name || key) as string,
      type: (merged.type || "openai") as "openai" | "anthropic",
      base_url: baseUrl,
      api_key_env: envVar,
      models,
      builtin: !!builtin,
      has_key: hasKey || getProviderKeys(key).length > 0,
      configured: hasKey || baseUrl.includes("localhost") || getProviderKeys(key).length > 0,
      active: models.includes(activeModel),
      keys: getProviderKeys(key),
      note: getProviderNote(key),
    });
  }

  return { providers, active_model: activeModel };
}

export function updateProvider(key: string, data: Record<string, unknown>): void {
  const overrides = readOverrides();
  overrides[key] = { ...(overrides[key] || {}), ...data };
  writeOverrides(overrides);
}

export function addProvider(key: string, data: Record<string, unknown>): void {
  const overrides = readOverrides();
  overrides[key] = { type: "openai", name: key, ...data };
  writeOverrides(overrides);
}

export function removeProvider(key: string): boolean {
  if (key in BUILTIN_PROVIDERS) return false;
  const overrides = readOverrides();
  if (key in overrides) {
    delete overrides[key];
    writeOverrides(overrides);
    return true;
  }
  return false;
}

export function setProviderKey(key: string, apiKey: string, envVar?: string): void {
  // Store in overrides
  updateProvider(key, { api_key: apiKey });
  // Also set in process env so it's available immediately
  if (envVar) {
    process.env[envVar] = apiKey;
  }
}

// --- Multi-key support ---

interface StoredKey {
  id: string;
  label: string;
  key: string;       // masked for display, full in storage
  key_full: string;   // actual key value
  type: "api" | "oauth" | "other";
  active: boolean;
  created_at: string;
}

const KEYS_PATH = resolve(DATA_DIR, "provider_keys.json");

function readKeys(): Record<string, StoredKey[]> {
  try {
    const fs = require("fs");
    if (fs.existsSync(KEYS_PATH)) {
      return JSON.parse(fs.readFileSync(KEYS_PATH, "utf-8"));
    }
  } catch {}
  return {};
}

function writeKeys(data: Record<string, StoredKey[]>): void {
  const fs = require("fs");
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
  }
  fs.writeFileSync(KEYS_PATH, JSON.stringify(data, null, 2));
  // Restrict permissions
  try { fs.chmodSync(KEYS_PATH, 0o600); } catch {}
}

function maskKey(key: string): string {
  if (key.length <= 12) return "****";
  return key.slice(0, 8) + "..." + key.slice(-4);
}

export function getProviderKeys(providerKey: string): Omit<StoredKey, "key_full">[] {
  const all = readKeys();
  return (all[providerKey] || []).map(k => ({
    id: k.id,
    label: k.label,
    key: maskKey(k.key_full),
    type: k.type,
    active: k.active,
    created_at: k.created_at,
  }));
}

export function addProviderKey(providerKey: string, label: string, keyValue: string, keyType: "api" | "oauth" | "other"): void {
  const all = readKeys();
  if (!all[providerKey]) all[providerKey] = [];

  const id = crypto.randomUUID();
  const isFirst = all[providerKey].length === 0;

  all[providerKey].push({
    id,
    label,
    key: maskKey(keyValue),
    key_full: keyValue,
    type: keyType,
    active: isFirst, // first key is auto-active
    created_at: new Date().toISOString(),
  });

  writeKeys(all);

  // If first key, activate it
  if (isFirst) {
    activateProviderKey(providerKey, id);
  }
}

export function deleteProviderKey(providerKey: string, keyId: string): void {
  const all = readKeys();
  if (!all[providerKey]) return;
  const wasActive = all[providerKey].find(k => k.id === keyId)?.active;
  all[providerKey] = all[providerKey].filter(k => k.id !== keyId);
  // If deleted key was active and others remain, activate the first
  if (wasActive && all[providerKey].length > 0) {
    all[providerKey][0].active = true;
    applyActiveKey(providerKey, all[providerKey][0].key_full);
  }
  writeKeys(all);
}

export function activateProviderKey(providerKey: string, keyId: string): void {
  const all = readKeys();
  if (!all[providerKey]) return;
  for (const k of all[providerKey]) {
    k.active = k.id === keyId;
  }
  const active = all[providerKey].find(k => k.active);
  if (active) {
    applyActiveKey(providerKey, active.key_full);
  }
  writeKeys(all);
}

function applyActiveKey(providerKey: string, keyValue: string): void {
  // Update the provider override so the Python brain picks it up
  updateProvider(providerKey, { api_key: keyValue });
  // Set in env for immediate use
  const builtin = BUILTIN_PROVIDERS[providerKey];
  const envVar = builtin?.api_key_env;
  if (envVar) {
    process.env[envVar as string] = keyValue;
  }
}

// --- Notes support ---

const NOTES_PATH = resolve(DATA_DIR, "provider_notes.json");

function readNotes(): Record<string, string> {
  try {
    const fs = require("fs");
    if (fs.existsSync(NOTES_PATH)) {
      return JSON.parse(fs.readFileSync(NOTES_PATH, "utf-8"));
    }
  } catch {}
  return {};
}

function writeNotes(data: Record<string, string>): void {
  const fs = require("fs");
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
  }
  fs.writeFileSync(NOTES_PATH, JSON.stringify(data, null, 2));
}

export function getProviderNote(providerKey: string): string {
  return readNotes()[providerKey] || "";
}

export function setProviderNote(providerKey: string, note: string): void {
  const notes = readNotes();
  notes[providerKey] = note;
  writeNotes(notes);
}

export { setActiveModel };

// --- Dynamic Model Discovery ---

interface DiscoveredModel {
  id: string;
  name?: string;
  context_length?: number;
  max_output_tokens?: number;
  pricing?: { prompt?: string; completion?: string };
  capabilities?: string[];
  owned_by?: string;
}

interface DiscoveryResult {
  provider: string;
  models: DiscoveredModel[];
  fetched_at: string;
  error?: string;
}

// Cache: provider key → discovery result (TTL 5 minutes)
const discoveryCache = new Map<string, { result: DiscoveryResult; expires: number }>();
const CACHE_TTL = 5 * 60 * 1000;

/**
 * Resolve the active API key for a provider (from multi-key store, overrides, or env).
 */
function resolveApiKey(providerKey: string): string | null {
  // 1. Check multi-key store for active key
  const all = readKeys();
  const keys = all[providerKey] || [];
  const active = keys.find(k => k.active);
  if (active) return active.key_full;

  // 2. Check overrides
  const overrides = readOverrides();
  if (overrides[providerKey]?.api_key) return overrides[providerKey].api_key as string;

  // 3. Check environment
  const builtin = BUILTIN_PROVIDERS[providerKey];
  if (builtin?.api_key_env && process.env[builtin.api_key_env]) {
    return process.env[builtin.api_key_env]!;
  }

  return null;
}

/**
 * Fetch models from a provider's API dynamically.
 */
export async function discoverModels(providerKey: string): Promise<DiscoveryResult> {
  // Check cache
  const cached = discoveryCache.get(providerKey);
  if (cached && cached.expires > Date.now()) {
    return cached.result;
  }

  const apiKey = resolveApiKey(providerKey);
  const builtin = BUILTIN_PROVIDERS[providerKey];
  const overrides = readOverrides();
  const override = overrides[providerKey] || {};
  const baseUrl = (override.base_url || builtin?.base_url || "") as string;
  const providerType = (override.type || builtin?.type || "openai") as string;

  if (!apiKey && !baseUrl.includes("localhost")) {
    return { provider: providerKey, models: [], fetched_at: new Date().toISOString(), error: "No API key" };
  }

  try {
    let models: DiscoveredModel[] = [];

    if (providerKey === "anthropic") {
      models = await fetchAnthropicModels(apiKey!);
    } else if (providerKey === "gemini") {
      models = await fetchGeminiModels(apiKey!, baseUrl);
    } else if (providerKey === "openrouter") {
      models = await fetchOpenRouterModels(apiKey);
    } else {
      // Standard OpenAI-compatible /v1/models
      models = await fetchOpenAICompatibleModels(baseUrl, apiKey);
    }

    const result: DiscoveryResult = {
      provider: providerKey,
      models,
      fetched_at: new Date().toISOString(),
    };

    discoveryCache.set(providerKey, { result, expires: Date.now() + CACHE_TTL });

    // Persist discovered models to overrides so they survive restarts
    if (models.length > 0) {
      const modelIds = models.map(m => m.id);
      updateProvider(providerKey, { discovered_models: models, discovered_at: result.fetched_at });
    }

    return result;
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    return { provider: providerKey, models: [], fetched_at: new Date().toISOString(), error };
  }
}

async function fetchOpenAICompatibleModels(baseUrl: string, apiKey: string | null): Promise<DiscoveredModel[]> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;

  const resp = await fetch(`${baseUrl}/models`, { headers, signal: AbortSignal.timeout(10000) });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);

  const data = await resp.json();
  const rawModels = data.data || data || [];

  return rawModels.map((m: any) => ({
    id: m.id,
    name: m.display_name || m.name || m.id,
    context_length: m.context_length || m.context_window || undefined,
    max_output_tokens: m.max_completion_tokens || undefined,
    pricing: m.pricing || undefined,
    owned_by: m.owned_by || m.organization || undefined,
  }));
}

async function fetchAnthropicModels(apiKey: string): Promise<DiscoveredModel[]> {
  const resp = await fetch("https://api.anthropic.com/v1/models", {
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "Content-Type": "application/json",
    },
    signal: AbortSignal.timeout(10000),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);

  const data = await resp.json();
  const rawModels = data.data || [];

  return rawModels.map((m: any) => ({
    id: m.id,
    name: m.display_name || m.id,
    context_length: m.max_input_tokens || undefined,
    max_output_tokens: m.max_tokens || undefined,
    capabilities: Object.entries(m.capabilities || {})
      .filter(([_, v]) => v === true)
      .map(([k]) => k),
    owned_by: "anthropic",
  }));
}

async function fetchGeminiModels(apiKey: string, baseUrl: string): Promise<DiscoveredModel[]> {
  // Use the native Gemini API for richer data
  const resp = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models?key=${apiKey}`,
    { signal: AbortSignal.timeout(10000) }
  );
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);

  const data = await resp.json();
  const rawModels = data.models || [];

  return rawModels
    .filter((m: any) => m.supportedGenerationMethods?.includes("generateContent"))
    .map((m: any) => ({
      id: m.name?.replace("models/", "") || m.name,
      name: m.displayName || m.name,
      context_length: m.inputTokenLimit || undefined,
      max_output_tokens: m.outputTokenLimit || undefined,
      owned_by: "google",
    }));
}

async function fetchOpenRouterModels(apiKey: string | null): Promise<DiscoveredModel[]> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;

  const resp = await fetch("https://openrouter.ai/api/v1/models", {
    headers,
    signal: AbortSignal.timeout(15000),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);

  const data = await resp.json();
  const rawModels = data.data || [];

  return rawModels.map((m: any) => ({
    id: m.id,
    name: m.name || m.id,
    context_length: m.context_length || undefined,
    max_output_tokens: m.max_completion_tokens || m.top_provider?.max_completion_tokens || undefined,
    pricing: m.pricing ? {
      prompt: m.pricing.prompt,
      completion: m.pricing.completion,
    } : undefined,
    owned_by: m.id.split("/")[0],
  }));
}

// --- Key Validation ---

export interface ValidationResult {
  valid: boolean;
  provider: string;
  model_count: number;
  error?: string;
  account?: {
    balance?: string;
    usage_today?: string;
    plan?: string;
  };
}

export async function validateKey(providerKey: string): Promise<ValidationResult> {
  const apiKey = resolveApiKey(providerKey);
  if (!apiKey) {
    return { valid: false, provider: providerKey, model_count: 0, error: "No API key found" };
  }

  try {
    const discovery = await discoverModels(providerKey);
    if (discovery.error) {
      return { valid: false, provider: providerKey, model_count: 0, error: discovery.error };
    }

    const result: ValidationResult = {
      valid: true,
      provider: providerKey,
      model_count: discovery.models.length,
    };

    // Try to fetch account info for providers that support it
    try {
      if (providerKey === "openai") {
        result.account = await fetchOpenAIAccountInfo(apiKey);
      } else if (providerKey === "anthropic") {
        // Anthropic Admin API requires admin keys; skip for regular keys
      }
    } catch {}

    return result;
  } catch (e) {
    return { valid: false, provider: providerKey, model_count: 0, error: e instanceof Error ? e.message : String(e) };
  }
}

async function fetchOpenAIAccountInfo(apiKey: string): Promise<{ balance?: string; plan?: string }> {
  try {
    const resp = await fetch("https://api.openai.com/v1/dashboard/billing/credit_grants", {
      headers: { Authorization: `Bearer ${apiKey}` },
      signal: AbortSignal.timeout(5000),
    });
    if (resp.ok) {
      const data = await resp.json();
      return {
        balance: data.total_available ? `$${data.total_available.toFixed(2)}` : undefined,
      };
    }
  } catch {}
  return {};
}

// --- OpenRouter OAuth PKCE ---

const OAUTH_STATE_PATH = resolve(DATA_DIR, "oauth_state.json");

interface OAuthState {
  code_verifier: string;
  code_challenge: string;
  created_at: string;
}

function generatePKCE(): { verifier: string; challenge: string } {
  const { createHash, randomBytes } = require("crypto");

  // Generate a random code verifier (43-128 chars, URL-safe)
  const verifier = randomBytes(32).toString("base64url");

  // SHA-256 hash for S256 challenge
  const hashBuffer = createHash("sha256").update(verifier).digest();
  const challenge = Buffer.from(hashBuffer).toString("base64url");

  return { verifier, challenge };
}

export function startOpenRouterOAuth(callbackUrl: string): { authorization_url: string } {
  const { verifier, challenge } = generatePKCE();

  // Save state for the callback
  const fs = require("fs");
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
  const state: OAuthState = {
    code_verifier: verifier,
    code_challenge: challenge,
    created_at: new Date().toISOString(),
  };
  fs.writeFileSync(OAUTH_STATE_PATH, JSON.stringify(state));

  // Step 1: Request an auth code from OpenRouter
  // The user will be redirected to OpenRouter to authorize
  const authorization_url = `https://openrouter.ai/auth?callback_url=${encodeURIComponent(callbackUrl)}&code_challenge=${challenge}&code_challenge_method=S256`;

  return { authorization_url };
}

export async function completeOpenRouterOAuth(code: string): Promise<{ success: boolean; key?: string; error?: string }> {
  // Read saved state
  const fs = require("fs");
  let state: OAuthState;
  try {
    state = JSON.parse(fs.readFileSync(OAUTH_STATE_PATH, "utf-8"));
  } catch {
    return { success: false, error: "No OAuth session found. Start the flow again." };
  }

  try {
    // Exchange code for API key
    const resp = await fetch("https://openrouter.ai/api/v1/auth/keys", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code,
        code_verifier: state.code_verifier,
      }),
    });

    if (!resp.ok) {
      const text = await resp.text();
      return { success: false, error: `OpenRouter returned ${resp.status}: ${text}` };
    }

    const data = await resp.json();
    const apiKey = data.key;

    if (!apiKey) {
      return { success: false, error: "No key returned from OpenRouter" };
    }

    // Store the key
    addProviderKey("openrouter", "OpenRouter OAuth", apiKey, "oauth");

    // Clean up state file
    try { fs.unlinkSync(OAUTH_STATE_PATH); } catch {}

    // Auto-discover models now that we have a key
    await discoverModels("openrouter");

    return { success: true, key: maskKey(apiKey) };
  } catch (e) {
    return { success: false, error: e instanceof Error ? e.message : String(e) };
  }
}
