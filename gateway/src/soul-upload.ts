/**
 * Soul import from the dashboard (strand P2.21.13, soul-import PR 4).
 *
 * The page sends the export as the raw request body (application/octet-stream) with an
 * `X-Filename` header that is only ever a hint: `.zip` or not. This module streams the body to
 * `<dir>/windy-soul-upload-<32 hex>`, where <dir> is $XDG_RUNTIME_DIR (per user, 0700) or else
 * /dev/shm. The file is created O_EXCL with mode 0600, and the bytes are counted as they arrive.
 *
 * Limits:
 * - Content-Length is required and checked first: over 90 MB answers 413 before anything is written.
 * - 90 MB stays under common edge-proxy request ceilings, so the 413 is ours.
 *
 * The brain gets only the path (bridge `soul.upload`). It opens the file O_NOFOLLOW, checks the open
 * descriptor, reads it and wipes it. Whatever happens (a refusal, a client that disconnects
 * mid-upload, the brain offline), the `finally` here overwrites and removes the file, so nothing is
 * left behind. Files left by a crash are swept at start.
 */
import { randomBytes } from "crypto";
import { closeSync, constants, fstatSync, lstatSync, openSync, readdirSync, statSync, unlinkSync, writeSync } from "fs";
import { join } from "path";

export const SOUL_UPLOAD_MAX_BYTES = 90 * 1024 * 1024;
export const SOUL_UPLOAD_PREFIX = "windy-soul-upload-";
/** How long the gateway waits for the brain to parse (soul.upload) or write (soul.import) an export. */
export const SOUL_PARSE_TIMEOUT_MS = 5 * 60 * 1000;
const MAX_AGE_MS = 30 * 60 * 1000;

function candidateDirs(): string[] {
  const out: string[] = [];
  for (const d of [process.env.XDG_RUNTIME_DIR ?? "", "/dev/shm"]) {
    if (!d) continue;
    try {
      if (statSync(d).isDirectory()) out.push(d);
    } catch {
      // not there
    }
  }
  return out;
}

/** `.zip` or not, from an encoded file name. Never used as a path. */
export function kindFromHeader(raw: string | null): "zip" | "json" {
  if (!raw || raw.length > 1024) return "json";
  let name = raw;
  try {
    name = decodeURIComponent(raw);
  } catch {
    return "json";
  }
  return name.trim().toLowerCase().endsWith(".zip") ? "zip" : "json";
}

function wipe(path: string): void {
  let fd = -1;
  try {
    const st = lstatSync(path);
    if (!st.isFile()) return;
    fd = openSync(path, constants.O_WRONLY | constants.O_NOFOLLOW);
    const size = fstatSync(fd).size;
    const zeros = Buffer.alloc(Math.min(size, 1024 * 1024));
    for (let done = 0; done < size; done += zeros.length) {
      writeSync(fd, zeros, 0, Math.min(zeros.length, size - done), done);
    }
  } catch {
    // gone already (the brain wiped it) or not ours
  } finally {
    if (fd >= 0) closeSync(fd);
  }
  try {
    unlinkSync(path);
  } catch {
    // gone already
  }
}

export type Upload = { status: number; body: Record<string, unknown> };

/** Stream the request body to a private file and hand it to `handoff` (the bridge call). */
export async function receiveSoulUpload(
  req: Request,
  handoff: (file: string, kind: "zip" | "json") => Promise<Record<string, unknown>>,
): Promise<Upload> {
  const declared = Number(req.headers.get("content-length") ?? NaN);
  if (!Number.isFinite(declared) || declared < 0) {
    return { status: 411, body: { error: "Send the file with its length." } };
  }
  if (declared > SOUL_UPLOAD_MAX_BYTES) {
    return { status: 413, body: { error: "The file is larger than 90 MB. Upload only the conversations.json from the export." } };
  }
  const dir = candidateDirs()[0];
  if (!dir || !req.body) {
    return { status: 503, body: { error: "No private place to hold the upload on this host." } };
  }
  const path = join(dir, SOUL_UPLOAD_PREFIX + randomBytes(16).toString("hex"));
  const kind = kindFromHeader(req.headers.get("x-filename"));
  let fd = -1;
  try {
    fd = openSync(path, constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW, 0o600);
    let total = 0;
    const reader = req.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > SOUL_UPLOAD_MAX_BYTES) {
        await reader.cancel().catch(() => {});
        return { status: 413, body: { error: "The file is larger than 90 MB." } };
      }
      writeSync(fd, value);
    }
    closeSync(fd);
    fd = -1;
    try {
      return { status: 200, body: await handoff(path, kind) };
    } catch {
      return { status: 503, body: { error: "Brain offline", _offline: true } };
    }
  } catch {
    return { status: 400, body: { error: "The upload did not complete. Nothing was kept." } };
  } finally {
    if (fd >= 0) closeSync(fd);
    wipe(path);
  }
}

/** Remove uploads older than 30 minutes (a crash between the write and the handoff). */
export function sweepSoulUploads(now = Date.now(), dirs: string[] = candidateDirs()): number {
  let removed = 0;
  for (const dir of dirs) {
    let names: string[] = [];
    try {
      names = readdirSync(dir);
    } catch {
      continue;
    }
    for (const name of names) {
      if (!name.startsWith(SOUL_UPLOAD_PREFIX)) continue;
      const path = join(dir, name);
      try {
        const st = lstatSync(path);
        if (now - st.mtimeMs <= MAX_AGE_MS) continue;
        if (st.isSymbolicLink()) unlinkSync(path);
        else if (st.isFile()) wipe(path);
        else continue;
        removed++;
      } catch {
        // raced with another sweep
      }
    }
  }
  return removed;
}
