/**
 * Windy hub (account.windyword.ai) JWT verification + owner gate.
 *
 * The dashboard used to be guarded by a shared DASHBOARD_PASSWORD. It is
 * now guarded by the owner's own Windy login (SSO #13): a hub-issued
 * RS256 JWT whose identity matches the agent's recorded owner
 * (WINDY_IDENTITY_ID, written at hatch).
 *
 * Contract (hub token contract v1/v1.1, BOARD.md):
 *   - RS256 only. HS256 hub tokens are refused in prod, so we never
 *     accept them either; "none" and every other alg are rejected.
 *   - Keys come from the hub JWKS. kid rotates, so keys are cached and
 *     the JWKS is re-fetched once on an unknown kid (rate-limited so a
 *     stream of junk kids can't turn us into a JWKS amplifier).
 *   - iss must be in HUB_ISSUERS (default: both `windy-identity` and
 *     `https://account.windyword.ai`, stage (i) of the issuer switch).
 *   - aud is enforced only behind WINDY_FLY_ENFORCE_AUDIENCE (default
 *     OFF, flipped by the hub lane once the hub emits aud). When ON, aud
 *     must be a JSON array containing `windy_fly`; a missing aud is
 *     rejected. When OFF, a would-be rejection is logged, not enforced.
 *   - exp / nbf are checked with at most 60s of clock skew.
 *
 * Observed on a real hub login token (2026-09-23): header
 * {alg: RS256, kid}, iss "windy-identity", NO aud yet, type "human",
 * and `windy_identity_id` is a different value from `sub`.
 */

import { createPublicKey, verify as cryptoVerify, type KeyObject } from "crypto";

export const DEFAULT_JWKS_URL = "https://account.windyword.ai/.well-known/jwks.json";
export const DEFAULT_ISSUERS = ["windy-identity", "https://account.windyword.ai"];
export const FLY_AUDIENCE = "windy_fly";
const MAX_SKEW_S = 60;
const UNKNOWN_KID_REFETCH_MIN_MS = 30_000;
const JWKS_TTL_MS = 60 * 60 * 1000;

export type HubClaims = Record<string, unknown> & {
  iss?: string;
  sub?: string;
  aud?: unknown;
  exp?: number;
  nbf?: number;
  type?: string;
};

export type VerifyFailure =
  | "malformed"
  | "bad_alg"
  | "unknown_kid"
  | "jwks_unavailable"
  | "bad_signature"
  | "bad_issuer"
  | "expired"
  | "not_yet_valid"
  | "bad_audience";

export type VerifyResult =
  | { ok: true; claims: HubClaims }
  | { ok: false; reason: VerifyFailure };

type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

export interface HubAuthConfig {
  jwksUrl: string;
  issuers: string[];
  enforceAudience: boolean;
  fetchImpl: FetchLike;
  now: () => number; // ms
  log: (msg: string) => void;
}

function envList(name: string, fallback: string[]): string[] {
  const raw = (process.env[name] || "").trim();
  if (!raw) return fallback;
  return raw.split(",").map((s) => s.trim()).filter(Boolean);
}

function envFlag(name: string): boolean {
  return ["1", "true", "yes", "on"].includes((process.env[name] || "").trim().toLowerCase());
}

export function configFromEnv(): HubAuthConfig {
  return {
    jwksUrl: process.env.HUB_JWKS_URL || DEFAULT_JWKS_URL,
    issuers: envList("HUB_ISSUERS", DEFAULT_ISSUERS),
    enforceAudience: envFlag("WINDY_FLY_ENFORCE_AUDIENCE"),
    fetchImpl: (url, init) => fetch(url, init),
    now: () => Date.now(),
    log: (msg) => console.warn(`[hub-auth] ${msg}`),
  };
}

function b64urlJson(part: string): any {
  return JSON.parse(Buffer.from(part, "base64url").toString("utf8"));
}

/**
 * Verifier with its own JWKS cache. The module exports a default
 * instance built from env; tests construct their own with a mocked
 * fetch and a fixed clock.
 */
export class HubJwtVerifier {
  private keys = new Map<string, KeyObject>();
  private fetchedAt = 0;
  private lastUnknownKidFetch = 0;

  constructor(private cfg: HubAuthConfig) {}

  private async refreshJwks(): Promise<boolean> {
    try {
      const resp = await this.cfg.fetchImpl(this.cfg.jwksUrl, {
        signal: AbortSignal.timeout(10_000),
      });
      if (!resp.ok) return false;
      const body = (await resp.json()) as { keys?: any[] };
      const next = new Map<string, KeyObject>();
      for (const jwk of body.keys || []) {
        if (!jwk || jwk.kty !== "RSA" || !jwk.kid) continue;
        if (jwk.use && jwk.use !== "sig") continue;
        try {
          next.set(String(jwk.kid), createPublicKey({ key: jwk, format: "jwk" }));
        } catch {
          // Skip a key we can't parse; the others still work.
        }
      }
      if (next.size === 0) return false;
      this.keys = next;
      this.fetchedAt = this.cfg.now();
      return true;
    } catch {
      return false;
    }
  }

  private async keyFor(kid: string): Promise<KeyObject | "unavailable" | null> {
    const now = this.cfg.now();
    if (this.keys.size === 0 || now - this.fetchedAt > JWKS_TTL_MS) {
      const ok = await this.refreshJwks();
      if (!ok && this.keys.size === 0) return "unavailable";
    }
    const cached = this.keys.get(kid);
    if (cached) return cached;
    // Unknown kid: the hub may have rotated. Re-fetch at most once per
    // UNKNOWN_KID_REFETCH_MIN_MS so junk kids can't hammer the JWKS.
    if (now - this.lastUnknownKidFetch >= UNKNOWN_KID_REFETCH_MIN_MS) {
      this.lastUnknownKidFetch = now;
      await this.refreshJwks();
      return this.keys.get(kid) || null;
    }
    return null;
  }

  async verify(token: string): Promise<VerifyResult> {
    const parts = (token || "").split(".");
    if (parts.length !== 3 || parts.some((p) => !p)) return { ok: false, reason: "malformed" };

    let header: any;
    let claims: HubClaims;
    try {
      header = b64urlJson(parts[0]);
      claims = b64urlJson(parts[1]);
    } catch {
      return { ok: false, reason: "malformed" };
    }
    if (!header || typeof header !== "object" || !claims || typeof claims !== "object") {
      return { ok: false, reason: "malformed" };
    }
    if (header.alg !== "RS256") return { ok: false, reason: "bad_alg" };
    if (!header.kid || typeof header.kid !== "string") return { ok: false, reason: "unknown_kid" };

    const key = await this.keyFor(header.kid);
    if (key === "unavailable") return { ok: false, reason: "jwks_unavailable" };
    if (!key) return { ok: false, reason: "unknown_kid" };

    const signed = Buffer.from(`${parts[0]}.${parts[1]}`, "utf8");
    const sig = Buffer.from(parts[2], "base64url");
    let good = false;
    try {
      good = cryptoVerify("RSA-SHA256", signed, key, sig);
    } catch {
      good = false;
    }
    if (!good) return { ok: false, reason: "bad_signature" };

    if (typeof claims.iss !== "string" || !this.cfg.issuers.includes(claims.iss)) {
      return { ok: false, reason: "bad_issuer" };
    }

    const nowS = Math.floor(this.cfg.now() / 1000);
    if (typeof claims.exp !== "number" || nowS > claims.exp + MAX_SKEW_S) {
      return { ok: false, reason: "expired" };
    }
    if (typeof claims.nbf === "number" && nowS + MAX_SKEW_S < claims.nbf) {
      return { ok: false, reason: "not_yet_valid" };
    }

    const audOk = Array.isArray(claims.aud)
      && (claims.aud as unknown[]).some((a) => a === FLY_AUDIENCE);
    if (!audOk) {
      if (this.cfg.enforceAudience) return { ok: false, reason: "bad_audience" };
      this.cfg.log(
        `would reject (aud enforcement OFF): aud=${JSON.stringify(claims.aud ?? null)} lacks "${FLY_AUDIENCE}"`,
      );
    }

    return { ok: true, claims };
  }
}

let defaultVerifier: HubJwtVerifier | null = null;

/** Verify a hub JWT with the env-configured verifier. */
export function verifyHubJwt(token: string): Promise<VerifyResult> {
  if (!defaultVerifier) defaultVerifier = new HubJwtVerifier(configFromEnv());
  return defaultVerifier.verify(token);
}

/** Test hook: swap the default verifier. */
export function _setDefaultVerifier(v: HubJwtVerifier | null): void {
  defaultVerifier = v;
}

// ── Owner gate ─────────────────────────────────────────────────────

/**
 * The identity a token speaks for: the `windy_identity_id` claim
 * (overridable via HUB_OWNER_CLAIM). There is deliberately NO fallback to
 * `sub`: on hub access tokens `sub` is the hub userId, while on id_tokens
 * it is the identity id, so matching on it would be inconsistent. A token
 * without the identity claim identifies nobody.
 */
export function identityOf(
  claims: HubClaims,
  ownerClaim: string = process.env.HUB_OWNER_CLAIM || "windy_identity_id",
): string {
  const v = claims[ownerClaim];
  return typeof v === "string" ? v : "";
}

/**
 * email_verified policy. The hub issues full tokens for UNVERIFIED emails
 * during a 24h grace window, so once it emits `email_verified` we require
 * it to be true. HUB_REQUIRE_EMAIL_VERIFIED defaults ON; an absent claim
 * is still accepted until the hub lane confirms it is emitted everywhere.
 */
function emailVerificationOk(claims: HubClaims): boolean {
  const flag = (process.env.HUB_REQUIRE_EMAIL_VERIFIED || "").trim().toLowerCase();
  if (["0", "false", "no", "off"].includes(flag)) return true;
  if (!("email_verified" in claims) || claims.email_verified === undefined) return true;
  return claims.email_verified === true;
}

/**
 * True iff the verified claims belong to the agent's owner.
 *
 * Matches ONLY on the identity claim (`windy_identity_id`) — never on
 * `email` (unverified emails get full tokens during the hub's grace
 * window, so an email match proves nothing) and never on `sub`.
 * Agent/bot tokens never pass, even with a matching identity: only a
 * human signs in to the dashboard.
 */
export function isOwner(
  claims: HubClaims,
  ownerId: string = process.env.WINDY_IDENTITY_ID || "",
): boolean {
  if (!ownerId) return false;
  if (typeof claims.type === "string" && claims.type !== "human") return false;
  if (!emailVerificationOk(claims)) return false;
  const id = identityOf(claims);
  if (!id || id.length !== ownerId.length) return false;
  const { timingSafeEqual } = require("crypto");
  return timingSafeEqual(Buffer.from(id), Buffer.from(ownerId));
}
