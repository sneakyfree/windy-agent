# Gateway Route Audit

## Fully Connected Chains (38 routes)

All gateway -> IPC bridge -> Python handler chains verified working.

| Route | IPC Method | Python Handler | Offline Fallback |
|-------|-----------|----------------|------------------|
| GET /api/sliders | sliders.get | _handle_sliders_get | Yes |
| PUT /api/sliders/:name | sliders.set | _handle_sliders_set | Yes |
| GET /api/sliders/info | sliders.info | _handle_sliders_info | Yes |
| GET /api/cost/daily | cost.daily | _handle_cost_daily | Yes |
| GET /api/intents | intents.list | _handle_intents_list | Yes |
| GET /api/dashboard | dashboard.summary | _handle_dashboard_summary | Yes |
| GET /api/memory/search | memory.search | _handle_search | **No** |
| POST /api/soul/preview | soul.preview | _handle_soul_preview | No |
| POST /api/soul/import | soul.import | _handle_soul_import | No |
| POST /api/sms/webhook | sms.inbound | _handle_sms_inbound | No |
| POST /api/sms/send | sms.send | _handle_sms_send | No |
| POST /api/email/webhook | email.inbound | _handle_email_inbound | No |
| POST /api/email/send | email.send | _handle_email_send | No |
| GET /api/journal | journal.list | _handle_journal_list | No |
| POST /api/assessment | assessment.run | _handle_assessment_run | No |
| POST /api/shape-shift | shape_shift.execute | _handle_shape_shift | No |
| POST /api/shape-shift/restore | shape_shift.restore | _handle_shape_shift_restore | No |
| GET /api/personality/history | personality.history | _handle_personality_history | No |
| POST /api/personality/snapshot | personality.snapshot | _handle_personality_snapshot | No |
| GET /api/personality/drift | personality.drift | _handle_personality_drift | No |
| POST /api/personality/rollback | personality.rollback | _handle_personality_rollback | No |
| GET /api/skills | skills.list | _handle_skills_list | No |
| POST /api/skills | skills.create | _handle_skills_create | No |
| POST /api/skills/:id/evaluate | skills.evaluate | _handle_skills_evaluate | No |
| POST /api/skills/:id/promote | skills.promote | _handle_skills_promote | No |
| POST /api/skills/:id/rollback | skills.rollback | _handle_skills_rollback | No |
| POST /api/skills/:id/golden-tests | skills.golden_tests | _handle_skills_golden_tests | No |
| POST /api/skills/regression | skills.regression | _handle_skills_regression | No |
| POST /api/decay/run | decay.run | _handle_decay_run | No |
| GET /api/conflicts | conflicts.list | _handle_conflicts_list | No |
| POST /api/conflicts/:id/resolve | conflicts.resolve | _handle_conflicts_resolve | No |
| GET /api/moments | moments.list | _handle_moments_list | No |
| GET /api/failures | failures.list | _handle_failures_list | No |
| GET /api/mode | mode.get | _handle_mode_get | No |
| PUT /api/mode | mode.set | _handle_mode_set | No |
| GET /api/offline/status | offline.status | _handle_offline_status | No |
| GET /api/events | events.list | _handle_events_list | No |
| WS /ws/chat | agent.respond | _handle_respond | No |

## Broken Chain (1)

| Route | IPC Method | Issue |
|-------|-----------|-------|
| POST /api/setup/launch | config.reload | **No Python handler exists.** Returns "Unknown method: config.reload". Caught silently by gateway. |

## Missing Route From Spec

**GET /api/cost/monthly** — does not exist in gateway, IPC, or Python. Only daily cost is implemented.

## Orphaned Python Handlers (6)

These exist in Python's dispatch table but the gateway handles them locally in `providers.ts`:

| Python Method | Notes |
|---------------|-------|
| providers.list | Handled gateway-side |
| providers.update | Handled gateway-side |
| providers.add | Handled gateway-side |
| providers.remove | Handled gateway-side |
| providers.set_model | Handled gateway-side |
| providers.set_key | Handled gateway-side |

## WebSocket Chat Assessment

Complete chain: `/ws/chat` -> `handleMessage` -> `bridge.call("agent.respond")` -> `_handle_respond` -> `agent_respond()`.

**Concern:** No automatic reconnection to brain. If brain disconnects/reconnects, WebSocket clients get permanent "Brain not connected" errors until gateway restart.

## Offline Handling Inconsistency

Routes WITH offline fallback: sliders, cost/daily, intents, dashboard, sliders/info.

Routes WITHOUT offline fallback (will 500 if brain is down): memory/search, journal, assessment, soul, sms, email, all skills, all personality, decay, conflicts, moments, failures, mode, events.
