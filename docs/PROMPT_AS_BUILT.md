# PROMPT_AS_BUILT.md — Windy Fly Agent System Prompt

**Phase 2.3.1 of the launch gauntlet.** Read-only documentation of `src/windyfly/agent/prompt.py` as it exists at HEAD (b6042fa). Refactor target in §6 is a *proposal* — do not implement before answering the 5 design questions.

> **Audit correction up front:** The gauntlet plan said "PR #200 silently broke a contract test that PR #201 had to retroactively repair." That is off-by-one. The git log shows the actual sequence was **PR #199 (`fix(gas-tank)`) silently broke a conftest stub** by adding a `max_tokens=` kwarg to `maybe_prepend_header` without updating the test-only stubs in `tests/conftest.py:117-125`. **PR #200 was forced to ship the conftest repair alongside its prompt change** (commit 52ee76c — the diff includes the conftest hunk: `side_effect=lambda text, tokens: text` → `side_effect=lambda text, tokens, max_tokens=200_000: text`). PR #201 was unrelated (`harden(lifeboat)`). The smell the audit was pattern-matching is real — it just happened one PR earlier than logged.

---

## 1. Sections enumerated

Every section is appended to `system_parts: list[str]` inside `assemble_prompt()` and then joined with `"\n\n"` into a single system message (`prompt.py:471-474`). Some additional sections become *separate* system messages later in the function.

| # | Section name | Line range | PR that added it | Conditional? | What it enforces |
|---|---|---|---|---|---|
| 1 | Personality block (SOUL.md + slider modifiers) | `prompt.py:83-97` | pre-launch (commit 2842cd3) | always | Persona + tone; slider modifiers wired post-launch by PR #202 |
| 2 | Mode override (companion/focused/neutral) | `prompt.py:99-101` | pre-launch | iff `mode != "companion"` | Mode-specific tone shift |
| 3 | 🎯 ACTIVE GOAL block | `prompt.py:103-133` | **PR #204** | iff `get_active_goal(db, session_id)` returns a row | "Orient every turn around concrete progress on it"; 4 sub-rules |
| 4 | Epistemic instruction (1-liner) | `prompt.py:135-139` | pre-launch | always | "indicate confidence; INFERRED nodes must say so" |
| 5 | RUNTIME GUARDRAIL (NETWORK / TOOLS / IDENTITY / HOST) | `prompt.py:141-200` | **PR #162** (3 pillars) + **PR #188** (HOST) | always | Anti-confabulation. Bans "Docker sandbox", Kit 0 impersonation, fake `ssh root@<vps>` |
| 6 | RUNTIME CONTEXT (positive truth — model / auth / supervisor / CWD) | `prompt.py:202-277` | **PR #192** + **PR #202** (CWD) | always | Positive facts model can QUOTE instead of confabulating |
| 7 | BIAS TO ACTION (7 numbered rules) | `prompt.py:279-364` | **PR #200** (1-6) + **PR #202** (rule 7) | **always — NOT gated on autonomy slider** | TRY FIRST, INVESTIGATE WITH TOOLS, SISTER AGENTS NOT GATEKEEPERS, RECOVERY > REFUSAL, SAFETY CARVE-OUT, WHEN PUSHED BACK, ASK AT MOST ONE QUESTION |
| 8 | FIRST CONTACT guard | `prompt.py:371-380` | PR #85 (`af755d4`) | iff `_is_first_contact(db)` (episodes=0 AND nodes=0) | Bans "welcome back" / "as we discussed" on virgin DB |
| 9 | LOW WORKING MEMORY hint | `prompt.py:392-403` | PR #117 (`e060f22`) | iff `pct_remaining < 10` | Grandma-friendly /new suggestion; bans "context window" / "tokens" jargon |
| 10 | GRANDMA MODE — STRICT | `prompt.py:416-469` | PR #118 + #121 + #123 | iff `band < 2` (USER=1 or SANDBOX=0) | 25+ banned vocab terms, plain-English substitutions |

Sections added as **separate system messages** later:

| # | Section | Line range | Conditional? |
|---|---|---|---|
| 11 | Last Session Handoff (turnover letter summary) | `prompt.py:476-491` | iff `get_nodes_by_type(db, "turnover_letter")` non-empty |
| 12 | Relevant Knowledge (keyword node search, epistemic-filtered) | `prompt.py:493-530` | iff keywords + `search_nodes` non-empty |
| 13 | Shared Experiences (relationship_moment nodes) | `prompt.py:532-550` | iff `get_nodes_by_type(db, "relationship_moment")` non-empty |
| 14 | Relevant earlier context (anti-amnesia keyword episode search) | `prompt.py:569-594` | iff keywords AND `search_episodes(exclude_ids=recent_ids)` non-empty |

`loop.py` performs **further per-call system-message injections** *after* `assemble_prompt`:

| # | Section | Where | Conditional |
|---|---|---|---|
| 15 | OS-state nudge (shell.exec discoverability) | `loop.py:663-683` | iff `capability_registry.get("shell.exec")` AND `_user_message_asks_os_state` |
| 16 | Local FS nudge (fs.* over web_search) | `loop.py:685-714` | iff `capability_registry.get("fs.read_file")` AND `_user_message_mentions_local` |
| 17 | First-interaction tour | `loop.py:716-733` | iff `is_first_interaction(db)` |
| 18 | Capability nudge | `loop.py:731-733` | iff `should_nudge_capabilities(db)` |
| 19 | Lessons learned (correction skills) | `loop.py:735-766` | iff `get_active_correction_skills(db)` returns rows |
| 20 | Friction recovery instruction | `loop.py:768-782` | iff `detect_friction` returns truthy |

**Total: 20 distinct conditionally-emitted prompt fragments at call time.** Composition is one long monolith.

---

## 2. Composition path

`assemble_prompt()` (`prompt.py:48-609`) is the composer. No template engine. Every section is a hand-written f-string or constant appended to `system_parts: list[str]` and then joined.

```
build_personality_block(soul_text, sliders)       # personality/engine.py
        ↓
system_parts = [personality_block]                 # prompt.py:97
system_parts.append(mode_override)                 # iff non-empty
system_parts.append("🎯 ACTIVE GOAL …")            # iff active_goal
system_parts.append("When you state a fact …")     # always
system_parts.append("RUNTIME GUARDRAIL …")         # always (PR #162/#188)
system_parts.append("\n".join(runtime_context_parts))  # always (PR #192/#202)
system_parts.append("BIAS TO ACTION …")            # always (PR #200/#202)
system_parts.append("FIRST CONTACT: …")            # iff _is_first_contact
system_parts.append("LOW WORKING MEMORY: …")       # iff pct_remaining < 10
system_parts.append("GRANDMA MODE — STRICT: …")    # iff band < 2

messages.append({"role": "system",
                 "content": "\n\n".join(system_parts)})

# THEN: separate system messages for memory/episodes
messages.append({"role": "system", "content": "## Last Session Handoff …"})
messages.append({"role": "system", "content": "## Relevant Knowledge: …"})
messages.append({"role": "system", "content": "## Shared Experiences: …"})
messages.append({"role": "system", "content": "## Relevant earlier context …"})

# THEN: actual conversation history + user message
```

---

## 3. Inputs the prompt depends on

| Input | Source | Varies between calls? | Affects |
|---|---|---|---|
| `config["personality"]["soul_path"]` | static config | no | personality block |
| `SOUL.md` file contents | filesystem | only on bot restart | personality block |
| Sliders (humor, formality, autonomy, epistemic_strictness, …) | DB + config defaults | **yes** — `/slider` writes DB live | personality modifiers, node filter strictness |
| `mode` arg | caller | yes per call | mode override |
| `session_id` arg | caller | yes | ACTIVE GOAL, episode scope |
| `pct_remaining` arg | caller (loop.py computes) | **yes every turn** | LOW WORKING MEMORY |
| `band` arg | caller (telegram_bot / demo_kiosk) | yes | GRANDMA MODE |
| `config["agent"]["default_model"]` | static config | no | RUNTIME CONTEXT model |
| env: `ANTHROPIC_OAUTH_ACCESS_TOKEN`, `ANTHROPIC_API_KEY` | env | rarely | RUNTIME CONTEXT auth |
| env: `INVOCATION_ID`, `KUBERNETES_SERVICE_HOST`, `AWS_LAMBDA_FUNCTION_NAME`, `/.dockerenv` | runtime | only on host migration | RUNTIME CONTEXT supervisor |
| `os.getcwd()` | runtime | could change if anything `os.chdir`s | RUNTIME CONTEXT CWD |
| DB: `episodes`/`nodes` counts | DB | yes | FIRST CONTACT guard |
| DB: active goal | DB | yes | ACTIVE GOAL |
| DB: turnover/relationship_moment/nodes/episodes | DB | yes | memory blocks |
| `user_message` | caller | every call | keyword-extraction → which nodes/episodes surface |

**Things that can change the prompt between two calls in the same session holding `user_message` constant:**

1. `pct_remaining` crossing the 10% threshold
2. `/slider autonomy 8` between turns (re-reads DB)
3. `/goal …` starting/ending a goal
4. `/memory 1M` pin (changes pct_remaining math)
5. New node landing in DB (turnover writer, episode summarizer)
6. Auth env rotation (oauth → api_key flip)
7. `_extract_keywords` returning a different set on phrasing change — silently changes which episodes surface

---

## 4. Contract tests + coverage map

| Section | Pinned by | File:line | Quality |
|---|---|---|---|
| Personality block | `test_autonomy_slider_wired.py:52-77` | autonomy/epistemic tiers pinned | Good |
| Mode override | `test_agent_loop.py:142-148` | substring "focused" only | Sparse |
| ACTIVE GOAL | `test_goal_slash.py:317-355` (3 tests) | presence + absence + session-scope | Good |
| Epistemic 1-liner | **none direct** | — | **GAP** |
| RUNTIME GUARDRAIL — 4 pillars | `test_self_env_confabulation_guard.py:80-128` | per-pillar tests | Good |
| RUNTIME CONTEXT block | `test_runtime_self_knowledge.py:452-543` (6 tests) | Good per-line | Good |
| RUNTIME CONTEXT — CWD line | `test_autonomy_slider_wired.py:151-160` | Good |
| BIAS TO ACTION — block + override | `test_self_env_confabulation_guard.py:130-143` | Good |
| BIAS TO ACTION — rule 1 (TRY FIRST) | `test_self_env_confabulation_guard.py:145-156` | Good |
| BIAS TO ACTION — rule 2 (INVESTIGATE) | **none** | — | **GAP** |
| BIAS TO ACTION — rule 3 (GATEKEEPERS) | `test_self_env_confabulation_guard.py:158-170` | Good |
| BIAS TO ACTION — rule 4 (RECOVERY) | `test_autonomy_slider_wired.py:171-185` | banned phrases pinned | Good |
| BIAS TO ACTION — rule 5 (SAFETY CARVE-OUT) | `test_self_env_confabulation_guard.py:172-187` | Good |
| BIAS TO ACTION — rule 6 (PUSHED BACK) | `test_self_env_confabulation_guard.py:189-200` | Good |
| BIAS TO ACTION — rule 7 (ASK AT MOST ONE) | `test_autonomy_slider_wired.py:163-168` | **WEAK** — substring assert only; no behavioral assertion that model output has ≤1 `?` |
| FIRST CONTACT | `test_agent_loop.py:150-187` (2) | Good |
| LOW WORKING MEMORY | `test_agent_loop.py:189-245` (4) | Good |
| GRANDMA MODE | `test_agent_loop.py:247-358` (7) | Excellent |
| GRANDMA MODE × guardrail interaction | `test_self_env_confabulation_guard.py:202-211` | Good |
| Turnover letter | `test_relationship_moments.py:54-74` | Good |
| Shared Experiences | `test_relationship_moments.py:12-46` (2) | Good |
| Relevant Knowledge / earlier-context | loose smoke (`test_agent_loop.py:776`) | **GAP** — no test pins keyword extraction → node selection |
| Per-call `loop.py` injections (FS/OS nudge, friction, lessons) | scattered, weak | — | **GAP** |

**What made PR #199's drift possible:** test stubs in `tests/conftest.py:117-125` mock `maybe_prepend_header` to a no-op with a frozen signature `lambda text, tokens`. PR #199 changed the real signature to `(text, tokens, max_tokens=200_000)` without updating the stub. `unittest.mock.patch(..., side_effect=…)` doesn't validate signatures until called — so `pytest --collect-only` passed, and only tests actually exercising the agent loop blew up. **Structural reason this is repeatable:** any kwarg addition to any helper that's stubbed in conftest will produce the same silent break-on-call. The contract is "stub signature = real signature" but nothing enforces it.

---

## 5. Prompt drift smell — prose-only vs code-enforced

Confirmed and expanded:

| v15 fix | Prose-only? | Code enforcement? | Verdict |
|---|---|---|---|
| Rule 4 RECOVERY > REFUSAL — banned phrases | **PROSE ONLY** | Test pins phrases *exist in the prompt*; no runtime tripwire scans assistant output. `loop.py:_looks_self_env_confabulated` does NOT include these patterns. | Audit's concern correct |
| Rule 7 ASK AT MOST ONE QUESTION | **PROSE ONLY** | Model is told to self-count `?`. No code reads response, counts question marks, retries/strips. | Audit's concern correct |
| Web-search gate UX rewrite | **NEITHER — not in prompt.py.** The "web_search gate" is in `tools/native_web_search.py` (Tier 0 injection) + `loop.py:985-1027` (kill-switch + daily cap). FS-nudge mentions "fs.* before web_search" but is gated on capability+message-shape heuristics. | Code-enforced via cap + killswitch | **Audit needs correction** — no prompt-prose gate exists |
| Autonomy slider wire-up (PR #202) | Mixed | **CODE ENFORCED** in `personality/engine.py:108-130` (slider read → modifier string) | Healthy |

**Additional prose-only sections that no code enforces:**

| Section | Enforcement |
|---|---|
| RUNTIME GUARDRAIL NETWORK pillar ("not in Docker sandbox") | **Code-enforced Layer 2** via `_looks_self_env_confabulated` tripwire |
| RUNTIME GUARDRAIL IDENTITY pillar ("not Kit 0") | **Code-enforced Layer 2** — Kit 0 delegation tripwire |
| RUNTIME GUARDRAIL HOST pillar ("no fake ssh") | **Code-enforced Layer 2** — host confab tripwire |
| BIAS TO ACTION rule 1 TRY FIRST | **No code enforcement.** Prose only. |
| BIAS TO ACTION rule 2 INVESTIGATE WITH TOOLS | **No code enforcement.** Prose only. |
| BIAS TO ACTION rule 3 SISTER AGENTS NOT GATEKEEPERS | Negative side (Kit 0 delegation) tripwire-caught; positive side prose only |
| BIAS TO ACTION rule 5 SAFETY CARVE-OUT ("rm -rf still pauses") | **No code enforcement** in prompt path; capabilities/* may have their own confirms |
| BIAS TO ACTION rule 6 WHEN PUSHED BACK ("DROP the caution") | **No code enforcement.** Prose only. |
| BIAS TO ACTION rule 7 ASK AT MOST ONE | **No code enforcement.** Prose only. |
| FIRST CONTACT ("DO NOT use 'welcome back'") | **No code enforcement.** Prose only. PR #142's welcome shortcut is a separate path. |
| LOW WORKING MEMORY ("say 'working memory'") | **No code enforcement.** Prose only. |
| GRANDMA MODE banned vocab (25+ terms) | **No code enforcement.** No outgoing-message sanitizer scrubs jargon when band < 2 (`test_sanitize_outgoing.py` covers credentials, not jargon). |

**Smell summary:** of the 10 monolithic prompt sections, only the 4 RUNTIME GUARDRAIL pillars have Layer-2 tripwires actually scanning output. Everything else — BIAS TO ACTION's 7 rules, FIRST CONTACT, LOW WORKING MEMORY, all of GRANDMA MODE — relies on the LLM to follow prose. **That is the same failure mode the v15 stress harness keeps re-discovering: the prompt grows, but enforcement doesn't.**

**Drift-rate evidence:** 5 edits to `prompt.py` in ~5 days (PRs #188, #192, #200, #202, #204 per `git log`). 35% of the file is now BIAS TO ACTION + RUNTIME blocks added in the last week. `grep -c system_parts.append` returns **9** — every behavioral fix in 2026 went into this one function.

---

## 6. Refactor target — split into `agent/prompt/sections/`

Proposed layout. Each section becomes its own module with (1) a `render(ctx) -> str | None` function, (2) explicit `Section` metadata (order, conditional, owner-PR), (3) its own paired contract test file.

```
src/windyfly/agent/prompt/
    __init__.py                          # re-export assemble_prompt
    assembler.py                         # orchestrator — calls each section.render(ctx)
    context.py                           # PromptContext dataclass
    sections/
        __init__.py                      # ordered list of sections
        personality.py                   # delegates to personality/engine
        mode_override.py
        active_goal.py                   # PR #204
        epistemic.py                     # the 1-liner
        runtime_guardrail.py             # PRs #162 + #188 — 4 pillars
        runtime_context.py               # PR #192 + #202
        bias_to_action.py                # PR #200 + #202 — 7 rules
        first_contact.py                 # PR #85
        low_working_memory.py            # PR #117
        grandma_mode.py                  # PR #118 + #121 + #123
        # Memory blocks:
        turnover_letter.py
        knowledge_nodes.py               # keyword + epistemic filter
        shared_experiences.py
        earlier_context.py               # PR #101 anti-amnesia
tests/prompt/sections/
    test_<one_per_section>.py
    test_section_ordering.py             # ONE integration test pinning order
```

**Sections that don't split cleanly:**

1. **`runtime_context.py`** pulls in `os`, `os.environ`, `os.path.exists`, `os.getcwd`, `windyfly.agent.models.get_anthropic_auth_path`. Either inject these via context (cleaner; section needs `PromptContext.supervisor`/`cwd`/`auth_path`) or import at call-time (current style; harder to mock).
2. **`bias_to_action.py`** 7 rules co-mingled in ~60-line string. The comment at `prompt.py:295-303` notes a soft tension with the autonomy slider and explicitly declines to gate. Question: does rule 7 (ASK AT MOST ONE) belong here or alongside the median-autonomy modifier in `personality/engine.py`?
3. **`knowledge_nodes.py`** couples keyword extraction (`_extract_keywords` is shared by node search AND earlier-context search). Splitting means promoting to `prompt/keywords.py` or duplicating.
4. **`active_goal.py` and `bias_to_action.py`** both want to override the user's autonomy slider — ordering coupling. After split, an integration test must pin that they don't contradict.
5. **Per-call `loop.py` injections** (FS nudge, OS nudge, friction, first-interaction, lessons) — these live in `agent/loop.py:650-790` today. Refactor decision: fold into `prompt/sections/` (cleaner inventory; but they depend on `capability_registry` and `failure_detector`) or leave in loop with documented contract.

### Five design questions for Grant

1. **Section ordering — declarative or imperative?** Per-section `order: int` (conflicts get ugly) vs explicit ordered list in `sections/__init__.py` (single source of truth, but PR friction). Lifeboat FSM doc chose explicit ordered transitions — consistent choice = explicit list.

2. **Contract test granularity — one per section, or one per claim?** BIAS TO ACTION has 7 rules pinned by 5 tests today. Pure split = 7 test files. Pragmatic split = one `test_bias_to_action.py` with one test per rule.

3. **Add Layer-2 enforcer for every prose-only rule, or accept some as prose-only by design?** Adding tripwires for "ASK AT MOST ONE", "say 'working memory'", and the 25-term GRANDMA MODE ban list is real work. Proposal: ratchet — when a rule is observed to fail in production (v15 surfaces 0/3 ambiguous_ok), it gets a tripwire; otherwise prose is fine. Audit should track *which prose rules have how many recorded failures* and prioritize.

4. **`build_personality_block` ownership — `personality/engine.py` or `prompt/sections/personality.py`?** Today engine reads sliders and emits modifiers (PR #202). Refactor must clarify: is "personality" *concept* owned by `personality/` with `prompt/sections/personality.py` as a thin wrapper? Or does engine collapse into the section and `personality/engine.py` shrinks to just `load_soul()`?

5. **Per-call `loop.py` injections — fold into `prompt/sections/` or leave in loop?** Folding gives one canonical inventory. Leaving keeps dependency direction clean (prompt has no opinion on capabilities; loop owns capability awareness). Instinct: fold + pass `capability_registry`/`friction` via `PromptContext` — but flag the dependency-injection cost.

---

### Critical implementation files for Phase 2.3.2

- `src/windyfly/agent/prompt.py`
- `src/windyfly/agent/loop.py` (lines 644-790 — per-call injections)
- `src/windyfly/personality/engine.py` (slider modifiers)
- `tests/test_self_env_confabulation_guard.py` (Layer 1+2+retry coverage)
- `tests/test_autonomy_slider_wired.py` (slider wire-up + CWD + rule 7)
