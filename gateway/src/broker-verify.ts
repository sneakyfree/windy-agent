/**
 * Wave 12 — broker_token verification for POST /hatch/remote.
 *
 * windy-pro's credential-broker issues opaque `bk_live_<base64url>`
 * tokens (NOT JWTs — SHA-256 hash lookups against a DB row). The only
 * authoritative verifier is Pro itself. Before spawning the hatch
 * subprocess we POST the token to Pro's verify endpoint and reject the
 * whole request if Pro can't vouch for it.
 *
 * Contract — mirrors /api/v1/agent/credentials/issue:
 *
 *   POST <pro>/api/v1/agent/credentials/verify
 *   Headers:
 *     Content-Type:       application/json
 *     X-Windy-Timestamp:  <unix_seconds>
 *     X-Windy-Signature:  sha256=<hex>
 *   Canonical string signed:
 *     <timestamp>.POST./api/v1/agent/credentials/verify.<sha256(body)>
 *   Body (sort-keys JSON, minimal separators):
 *     { "broker_token": "bk_live_..." }
 *   Response:
 *     200 { ok: true,  token: { identity_id, passport_number, provider, model, scope, expires_at, usage_cap_tokens, usage_tokens } }
 *     200 { ok: false, reason: "not_found"|"revoked"|"expired"|"exhausted" }
 *     401                                                        (bad signature / missing secret)
 *     404                                                        (endpoint not deployed yet)
 *
 * The caller supplies `BROKER_HMAC_SECRET` (shared with Pro) and the
 * `windy_identity_id` + `passport_number` from the /hatch/remote body
 * so this helper can cross-check Pro's answer against what was
 * requested (prevents token-theft between identities).
 */

import crypto from "crypto";

export type BrokerVerifyOutcome =
  | { ok: true; token: BrokerTokenClaims }
  | { ok: false; status: number; reason: string };

export interface BrokerTokenClaims {
  identity_id: string;
  passport_number: string | null;
  provider: string;
  model: string;
  scope: string;
  expires_at: string;
  usage_cap_tokens: number;
  usage_tokens: number;
}

export interface BrokerVerifyInput {
  broker_token: string;
  windy_identity_id: string;
  passport_number: string;
}

export interface BrokerVerifyOptions {
  /** Defaults to process.env.WINDY_PRO_URL / WINDY_API_URL. */
  proBaseUrl?: string;
  /** Defaults to process.env.BROKER_HMAC_SECRET / WINDY_BROKER_SIGNING_SECRET. */
  hmacSecret?: string;
  /** Defaults to global fetch — tests inject a stub. */
  fetchImpl?: typeof fetch;
  /** Defaults to 3s; Pro should answer in <200ms. */
  timeoutMs?: number;
  /** Force skip — dev-only escape hatch. Never set in production. */
  disabled?: boolean;
}

/**
 * Canonical JSON — sort top-level keys, minimal separators. Matches
 * what Pro's `verifyBrokerSignature` expects, byte-for-byte.
 *
 * We deliberately keep this simple (only top-level sort — the verify
 * body has a single string field) rather than porting Pro's recursive
 * `canonicalJsonStringify`. If the verify body grows nested objects,
 * replace this with a mirror of Pro's impl.
 */
export function canonicalJsonStringify(payload: unknown): string {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    return JSON.stringify(payload);
  }
  const entries = Object.entries(payload as Record<string, unknown>)
    .filter(([, v]) => v !== undefined)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${JSON.stringify(k)}:${canonicalJsonStringify(v)}`);
  return `{${entries.join(",")}}`;
}

/**
 * Sign a canonical body with Pro's HMAC contract. Exported for tests.
 */
export function signVerifyRequest(
  canonicalBody: string,
  secret: string,
  timestamp: string,
  path = "/api/v1/agent/credentials/verify",
): { timestamp: string; signature: string } {
  const bodyHash = crypto.createHash("sha256").update(canonicalBody).digest("hex");
  const canonical = `${timestamp}.POST.${path}.${bodyHash}`;
  const hex = crypto.createHmac("sha256", secret).update(canonical).digest("hex");
  return { timestamp, signature: `sha256=${hex}` };
}

function resolveSecret(override?: string): string {
  return (
    override
    ?? process.env.BROKER_HMAC_SECRET
    ?? process.env.WINDY_BROKER_SIGNING_SECRET
    ?? ""
  );
}

function resolveProUrl(override?: string): string {
  const url = override
    ?? process.env.WINDY_PRO_URL
    ?? process.env.WINDY_API_URL
    ?? "";
  return url.replace(/\/+$/, "");
}

/**
 * Verify a broker_token against Pro before spawning the hatch subprocess.
 *
 * Fail-closed: any of {no secret configured, Pro unreachable, non-200
 * response, signature rejection, token not-ok, identity/passport
 * mismatch} returns `ok: false`. The caller should 401 the original
 * /hatch/remote request.
 *
 * The dev escape hatch (`disabled: true` or `WINDY_BROKER_VERIFY_DISABLED=1`)
 * logs a loud warning and returns a synthesized "ok" with a dev-only
 * identity. Never enable in production.
 */
export async function verifyBrokerToken(
  input: BrokerVerifyInput,
  opts: BrokerVerifyOptions = {},
): Promise<BrokerVerifyOutcome> {
  const disabled = opts.disabled
    ?? process.env.WINDY_BROKER_VERIFY_DISABLED === "1"
    ?? false;
  if (disabled) {
    console.warn(
      "[broker-verify] WINDY_BROKER_VERIFY_DISABLED is set — accepting the token " +
      "without talking to Pro. NEVER do this in production.",
    );
    return {
      ok: true,
      token: {
        identity_id: input.windy_identity_id,
        passport_number: input.passport_number || null,
        provider: "dev-bypass",
        model: "dev-bypass",
        scope: "llm:chat",
        expires_at: new Date(Date.now() + 3600_000).toISOString(),
        usage_cap_tokens: 1,
        usage_tokens: 0,
      },
    };
  }

  // Fast reject — if it doesn't start with `bk_`, Pro will reject
  // anyway. Saves the round-trip for the obvious attack shape
  // (broker_token="12345678").
  if (!input.broker_token.startsWith("bk_")) {
    return { ok: false, status: 401, reason: "bad_format" };
  }

  const secret = resolveSecret(opts.hmacSecret);
  if (!secret) {
    // Fail-closed: without the HMAC secret we can't even *call* Pro.
    // An attacker can't forge a request into Pro but they also can't
    // verify a legitimate one. 503 is semantically right but the
    // caller translates our !ok into 401 either way.
    return { ok: false, status: 503, reason: "broker_secret_not_configured" };
  }

  const base = resolveProUrl(opts.proBaseUrl);
  if (!base) {
    return { ok: false, status: 503, reason: "pro_url_not_configured" };
  }

  const body = { broker_token: input.broker_token };
  const canonicalBody = canonicalJsonStringify(body);
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const { signature } = signVerifyRequest(canonicalBody, secret, timestamp);

  const url = `${base}/api/v1/agent/credentials/verify`;
  const doFetch = opts.fetchImpl ?? fetch;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? 3000);

  let resp: Response;
  try {
    resp = await doFetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Windy-Timestamp": timestamp,
        "X-Windy-Signature": signature,
      },
      body: canonicalBody,
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timer);
    const msg = err instanceof Error ? err.message : String(err);
    return { ok: false, status: 502, reason: `pro_unreachable: ${msg}` };
  }
  clearTimeout(timer);

  if (resp.status === 404) {
    // Pro doesn't ship /credentials/verify yet — log loudly so this
    // can't silently pass. Fail-closed still: we refuse the hatch.
    console.warn(
      "[broker-verify] Pro returned 404 on /api/v1/agent/credentials/verify — " +
      "the endpoint is not deployed. See Wave 12 PR body for the required Pro-side route.",
    );
    return { ok: false, status: 502, reason: "pro_verify_endpoint_missing" };
  }

  if (resp.status !== 200) {
    return { ok: false, status: resp.status, reason: `pro_status_${resp.status}` };
  }

  let data: { ok?: boolean; reason?: string; token?: BrokerTokenClaims };
  try {
    data = await resp.json() as typeof data;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { ok: false, status: 502, reason: `pro_bad_json: ${msg}` };
  }

  if (!data?.ok || !data.token) {
    return { ok: false, status: 401, reason: `token_${data?.reason ?? "rejected"}` };
  }

  // Cross-check: the identity Pro returned must match the one the
  // caller claimed, otherwise a valid broker_token for identity A
  // could be replayed against a /hatch/remote for identity B. Same
  // for passport_number when one was specified.
  if (data.token.identity_id !== input.windy_identity_id) {
    return { ok: false, status: 401, reason: "identity_mismatch" };
  }
  if (
    input.passport_number
    && data.token.passport_number
    && data.token.passport_number !== input.passport_number
  ) {
    return { ok: false, status: 401, reason: "passport_mismatch" };
  }

  return { ok: true, token: data.token };
}
