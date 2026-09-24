/**
 * Test helpers: a locally generated RS256 keypair, a JWKS that serves it,
 * and a signer for hub-shaped JWTs. Nothing here touches the real hub.
 */
import { generateKeyPairSync, createSign, createHmac, type KeyObject } from "crypto";
import { HubJwtVerifier } from "../../src/hub-auth";

export interface TestKey {
  kid: string;
  privateKey: KeyObject;
  jwk: Record<string, unknown>;
}

export function makeKey(kid: string): TestKey {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const jwk = { ...(publicKey.export({ format: "jwk" }) as Record<string, unknown>), kid, use: "sig", alg: "RS256" };
  return { kid, privateKey, jwk };
}

const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");

export function signRs256(key: TestKey, claims: Record<string, unknown>, headerExtra: Record<string, unknown> = {}): string {
  const head = b64({ alg: "RS256", typ: "JWT", kid: key.kid, ...headerExtra });
  const body = b64(claims);
  const s = createSign("RSA-SHA256");
  s.update(`${head}.${body}`);
  return `${head}.${body}.${s.sign(key.privateKey).toString("base64url")}`;
}

export function signHs256(secret: string, claims: Record<string, unknown>, kid = "k1"): string {
  const head = b64({ alg: "HS256", typ: "JWT", kid });
  const body = b64(claims);
  const sig = createHmac("sha256", secret).update(`${head}.${body}`).digest("base64url");
  return `${head}.${body}.${sig}`;
}

export const NOW_MS = 1_790_000_000_000;

export function humanClaims(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const now = Math.floor(NOW_MS / 1000);
  return {
    iss: "windy-identity",
    sub: "sub-0000",
    windy_identity_id: "owner-identity-1",
    type: "human",
    iat: now,
    exp: now + 900,
    ...overrides,
  };
}

/** A JWKS endpoint whose key set can be swapped, counting fetches. */
export function jwksServer(initial: TestKey[]) {
  let keys = initial;
  let fetches = 0;
  return {
    setKeys(k: TestKey[]) { keys = k; },
    get fetches() { return fetches; },
    fetchImpl: async (_url: string) => {
      fetches++;
      return new Response(JSON.stringify({ keys: keys.map((k) => k.jwk) }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    },
  };
}

export function makeVerifier(
  jwks: ReturnType<typeof jwksServer>,
  opts: { enforceAudience?: boolean; now?: () => number; log?: (m: string) => void } = {},
): HubJwtVerifier {
  return new HubJwtVerifier({
    jwksUrl: "https://hub.test/.well-known/jwks.json",
    issuers: ["windy-identity", "https://account.windyword.ai"],
    enforceAudience: !!opts.enforceAudience,
    fetchImpl: jwks.fetchImpl,
    now: opts.now || (() => NOW_MS),
    log: opts.log || (() => {}),
  });
}
