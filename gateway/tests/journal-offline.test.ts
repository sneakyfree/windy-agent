/**
 * GET /api/journal answers with the bridge's own key ("journal") even while the brain is down
 * (P5.93.5): the offline fallback used to say "entries", so the page read nothing.
 */
import { describe, expect, test } from "bun:test";
import { JOURNAL_OFFLINE } from "../src/server";

describe("journal offline fallback", () => {
  test("uses the bridge's key, empty, and says it is offline", () => {
    expect(Object.keys(JOURNAL_OFFLINE).sort()).toEqual(["_offline", "journal"]);
    expect(JOURNAL_OFFLINE.journal).toEqual([]);
    expect(JOURNAL_OFFLINE._offline).toBe(true);
  });
});
