/**
 * Files that retired features left behind in data/ on existing installs.
 *
 * Each is overwritten with zeros in place, then deleted, once at gateway
 * start. Best effort: on SSDs and copy-on-write filesystems old blocks can
 * survive an overwrite, so this is cleanup, not a secure erase.
 *
 * - machines.json: Mission Control (retired 2026-10-10) kept the tokens
 *   people typed for their remote machines here.
 */
import { closeSync, existsSync, fstatSync, openSync, unlinkSync, writeSync } from "fs";
import { resolve } from "path";

const DATA_DIR = resolve(import.meta.dir, "../../data");

export const RETIRED_DATA_FILES = ["machines.json"];

export function removeRetiredDataFiles(
  dir: string = DATA_DIR,
  names: string[] = RETIRED_DATA_FILES,
): string[] {
  const removed: string[] = [];
  for (const name of names) {
    const path = resolve(dir, name);
    if (!existsSync(path)) continue;
    try {
      const fd = openSync(path, "r+");
      try {
        const size = fstatSync(fd).size;
        if (size > 0) writeSync(fd, Buffer.alloc(size), 0, size, 0);
      } finally {
        closeSync(fd);
      }
      unlinkSync(path);
      removed.push(name);
    } catch (e) {
      const why = e instanceof Error ? e.message : String(e);
      console.warn(`[gateway] could not remove retired data/${name}: ${why}`);
    }
  }
  return removed;
}
