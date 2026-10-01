/**
 * POST /hatch/remote — retired (0.7.5, ADR-059: one hallway).
 *
 * This route used to spawn the Python terminal-hatch orchestrator
 * (`python -m windyfly.hatch_remote`) and relay its progress as SSE.
 * That hallway is gone: agents are hatched only in the Windy hatch
 * ceremony (`windy go` opens it, or the dashboard). The route stays
 * mounted so an old caller gets a clear, permanent answer instead of a
 * 404 that looks like a misconfigured gateway.
 */

export const HATCH_MOVED_BODY = {
  error: "hatch_moved",
  message: "Hatching happens in the Windy hatch ceremony. Run windy go or use the dashboard.",
} as const;

export function handleHatchRemote(_req?: Request): Response {
  return Response.json(HATCH_MOVED_BODY, { status: 410 });
}
