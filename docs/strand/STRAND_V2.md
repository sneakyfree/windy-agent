# Windy Fly strand v2: the living spec

The DNA strand (docs/WINDY_FLY_DNA_STRAND_PART1-5.md) is the original plan; those files are kept
as history. **This file is what Windy Fly promises today**, one row per codon, and it is
executable: `tests/strand/test_scorecard.py` fails the build if a row and the code disagree.

**Status words**
- `live`: the promise holds, and the named test proves it. The test must exist and must not be
  skippable (no skip/xfail decorator, module mark or call). A skipped test never counts as green.
- `retired: <decision> (#PR)`: deliberately removed. The PR that removed it carries the decision.
- `planned: <owner>: <what>`: not true today. It has an owner and a next step; it is not hidden.

Codon ids are `P<part>.<gene>.<n>` for the original strand's parts and `N<area>.<n>` for genes the
original plan did not have (Wave 5g: Windy Mind, Eternitas, teams, Mail, Calendar, Cloud, phone
tools, Vault). A row owned by another lane counts only after that lane confirms it (Hub).

Built in waves (strand-to-green plan, 2026-10-10): 5a set the frame plus Part 1 §4 and gene 0.3;
5b1 added Part 1 §1-§3 and genes 0.1-0.2; 5b2 completes Part 1 (genes 0.4-0.9, checklist C0);
5c completed Part 2; 5d completes Part 3; 5e1 adds Part 4 Phase 5 (genes 5.1-5.5, checklist C5);
5e2 the rest of Part 4 (map, file index, roadmap, checklists C1-C4); 5f1 Part 5 Phases 6-8; 5f2 Phases
9-10; 5g adds the new genes.

## Part 1: Foundation and Phase 0

### P1.1 Product identity (Part 1 §1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.1.1 | The product is Windy Fly: "Your AI. Your Rules. Your Ecosystem." | live | `tests/strand/test_p1_foundation.py::test_p1_1_name_and_tagline` |
| P1.1.2 | Part of the Windy ecosystem; chat on chat.windychat.ai, provisioned at hatch | live | `tests/strand/test_p1_foundation.py::test_p1_1_part_of_the_windy_ecosystem` |
| P1.1.3 | One shared Matrix bot id (@windyfly) | retired: every agent is its own @agent_<passport>, minted at hatch; the shared id was removed (#505) | — |
| P1.1.4 | Licence: proprietary (Copyright 2026 Windstorm Labs LLC, all rights reserved) in LICENSE, the package metadata and the README | live | `tests/strand/test_p1_foundation.py::test_p1_1_licence_is_proprietary` |
| P1.1.5 | Runs on Python 3.12 or newer | live | `tests/strand/test_p1_foundation.py::test_p1_1_python_312_or_newer` |
| P1.1.6 | TypeScript/Bun gateway, tested in CI | live | `tests/strand/test_p1_foundation.py::test_p1_1_bun_gateway_is_tested_in_ci` |
| P1.1.7 | SQLite with FTS5 full-text search | live | `tests/strand/test_p1_memory.py::test_p1_4_episodes_fts_finds_saved_text` |
| P1.1.8 | sqlite-vec vector search | retired: never loaded; dependency and claims removed (#505) | — |
| P1.1.9 | matrix-nio is the chat protocol library | live | `tests/strand/test_p1_foundation.py::test_p1_1_matrix_nio_is_the_chat_protocol` |
| P1.1.10 | TOML config | live | `tests/test_config_resilience.py::test_valid_config_loads_normally` |
| P1.1.11 | Dependencies locked with uv and bun | live | `tests/strand/test_p1_foundation.py::test_p1_1_locked_with_uv_and_bun` |

### P1.2 Architecture (Part 1 §2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.2.1 | A ReAct loop: the model calls tools, sees results, then answers | live | `tests/test_agent_loop.py::TestToolExecution::test_tool_reloop_executes_tools` |
| P1.2.2 | A memory manager over SQLite | live | `tests/test_memory.py::TestEpisodes::test_save_and_retrieve` |
| P1.2.3 | A personality engine that reads SOUL.md | live | `tests/test_personality.py::TestLoadSoul::test_loads_existing_file` |
| P1.2.4 | An in-process write queue | live | `tests/strand/test_p1_memory.py::test_p1_03_write_queue_runs_on_a_daemon_thread` |
| P1.2.5 | Windy Pro tools when Windy Pro is configured and answers (else not offered) | live | `tests/test_tools.py::TestWindyToolsOnlyWhenConfigured::test_answering_pro_registers_four` |
| P1.2.6 | No Windy Pro tools without a working Windy Pro | live | `tests/test_tools.py::TestWindyToolsOnlyWhenConfigured::test_unreachable_no_tools` |

### P1.3 Repo layout (Part 1 §3, gene 0.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.3.1 | Root files: pyproject, windyfly.toml, SOUL.md, README, .gitignore, .env.example | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[root]` |
| P1.3.2 | Core modules: package, main, config | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[core]` |
| P1.3.3 | agent/: loop, prompt, models | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[agent]` |
| P1.3.4 | memory/: the 8 modules | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[memory]` |
| P1.3.5 | personality/: engine | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[personality]` |
| P1.3.6 | personality/mode.py (mode switching) | retired: did nothing; removed (#499) | — |
| P1.3.7 | channels/: Matrix bot, CLI | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[channels]` |
| P1.3.8 | tools/: registry, Windy Pro, web search | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[tools]` |
| P1.3.9 | tests/ with the core test files | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[tests]` |
| P1.3.10 | data/ is tracked empty (data/.gitkeep) | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[data]` |

### P1.01 Repo setup (gene 0.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.01.1 | git init and an initial commit | retired: a one-time build step, not a runtime promise (#526) | — |
| P1.01.2 | .gitignore keeps secrets, databases, provider files and caches out of git | live | `tests/strand/test_p1_foundation.py::test_p1_01_gitignore_keeps_secrets_and_state_out` |
| P1.01.3 | .env.example names every key once | live | `tests/strand/test_p1_foundation.py::test_p1_01_env_example_names_each_key_once` |
| P1.01.4 | The package is "windyfly" and declares its runtime dependencies | live | `tests/strand/test_p1_foundation.py::test_p1_01_package_named_windyfly_with_its_runtime_deps` |

### P1.02 Config (gene 0.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.02.1 | One default model | planned: Windy Agent: four defaults disagree (windyfly.toml, DEFAULT_CONFIG, .env.example, the loop's fallback); pick one, the others read it | — |
| P1.02.2 | [agent] temperature / max_response_tokens / max_context_tokens are honoured | planned: Windy Agent: never read (the creativity and response-length sliders decide); honour them or drop them from the file | — |
| P1.02.3 | [memory] db_path and max_nodes_per_context come from the file | live | `tests/strand/test_p1_foundation.py::test_p1_02_memory_settings_come_from_the_file` |
| P1.02.4 | [memory] max_episodes_per_context is honoured | planned: Windy Agent: never read (5 + 5 x context_window slider decides); honour it or drop it | — |
| P1.02.5 | [personality] sliders default from the file | live | `tests/strand/test_p1_foundation.py::test_p1_02_personality_sliders_default_from_the_file` |
| P1.02.6 | [costs] daily budget is enforced | live | `tests/test_cost_tracker.py::TestBudgetCheck::test_exceeds_budget` |
| P1.02.7 | [matrix] settings come from the file | live | `tests/strand/test_p1_foundation.py::test_p1_02_matrix_settings_come_from_the_file` |
| P1.02.8 | load_config reads windyfly.toml into a dict (safe defaults on a corrupt file) | live | `tests/test_config_resilience.py::test_valid_config_loads_normally` |
| P1.02.9 | Secrets in the environment override the file | live | `tests/test_config_resilience.py::test_env_overrides_still_apply_on_corrupt_config` |

### P1.4 Database schema (Part 1 §4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.4.1 | Every file database opens in WAL mode | live | `tests/strand/test_p1_memory.py::test_p1_4_journal_mode_wal` |
| P1.4.2 | synchronous=NORMAL | live | `tests/strand/test_p1_memory.py::test_p1_4_synchronous_normal` |
| P1.4.3 | foreign_keys=ON | live | `tests/strand/test_p1_memory.py::test_p1_4_foreign_keys_on` |
| P1.4.4 | busy_timeout=5000 ms | live | `tests/strand/test_p1_memory.py::test_p1_4_busy_timeout_5000` |
| P1.4.5 | `nodes` keeps all 16 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[nodes]` |
| P1.4.6 | `episodes` keeps all 14 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[episodes]` |
| P1.4.7 | `soul` keeps all 8 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[soul]` |
| P1.4.8 | `skills` keeps all 17 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[skills]` |
| P1.4.9 | `failures` keeps all 9 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[failures]` |
| P1.4.10 | `cost_ledger` keeps all 7 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[cost_ledger]` |
| P1.4.11 | `episodes_fts` (FTS5) finds saved episode text | live | `tests/strand/test_p1_memory.py::test_p1_4_episodes_fts_finds_saved_text` |
| P1.4.12 | `schema_version` has row 1 and records every migration | live | `tests/strand/test_p1_memory.py::test_p1_4_schema_version_tracks_every_migration` |

### P1.03 Database layer (gene 0.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.03.1 | Opening a database creates its directory | live | `tests/strand/test_p1_memory.py::test_p1_03_creates_the_data_dir` |
| P1.03.2 | Declared types are parsed (TIMESTAMP comes back as a datetime) | live | `tests/strand/test_p1_memory.py::test_p1_03_parse_decltypes` |
| P1.03.3 | Reopening runs no migration twice and keeps the data | live | `tests/strand/test_p1_memory.py::test_p1_03_reopen_does_not_rerun_migrations` |
| P1.03.4 | Each migration is applied atomically | planned: Windy Agent: SQL-string migrations run through executescript, which commits each statement; make them atomic, or record that only callable migrations are | — |
| P1.03.5 | execute / executemany / fetchone / fetchall give rows keyed by column name | live | `tests/strand/test_p1_memory.py::test_p1_03_rows_are_addressed_by_column_name` |
| P1.03.6 | save_episode stores an episode that can be read back | live | `tests/test_memory.py::TestEpisodes::test_save_and_retrieve` |
| P1.03.7 | Recent episodes come newest first | live | `tests/test_memory.py::TestEpisodes::test_multiple_episodes_ordered` |
| P1.03.8 | Recent episodes can be filtered by session | live | `tests/test_memory.py::TestEpisodes::test_session_filter` |
| P1.03.9 | upsert_node creates a node that get_node returns | live | `tests/test_memory.py::TestNodes::test_upsert_new` |
| P1.03.10 | upsert_node on the same (type, name, scope) updates it | live | `tests/test_memory.py::TestNodes::test_upsert_existing_updates` |
| P1.03.11 | Nodes can be listed by type | live | `tests/test_memory.py::TestNodes::test_get_by_type` |
| P1.03.12 | Nodes can be searched by name | live | `tests/test_memory.py::TestNodes::test_search_by_name` |
| P1.03.13 | Soul keys are stored and read back | live | `tests/test_memory.py::TestSoul::test_upsert_and_get` |
| P1.03.14 | Cost rows are logged and read back | live | `tests/test_memory.py::TestCostLedger::test_log_and_retrieve` |
| P1.03.15 | Failures are logged and read back | live | `tests/test_memory.py::TestFailures::test_log_and_retrieve` |
| P1.03.16 | The six core tables exist on a new database | live | `tests/test_memory.py::TestDatabaseCreation::test_creates_all_tables` |
| P1.03.17 | The write queue runs writes on a daemon thread | live | `tests/strand/test_p1_memory.py::test_p1_03_write_queue_runs_on_a_daemon_thread` |
| P1.03.18 | HIGH writes run before MEDIUM, MEDIUM before LOW | live | `tests/test_strand_wave4_write_queue.py::test_C0_11_strict_priority_order_with_several_items_each` |
| P1.03.19 | A failing write does not stop the queue | live | `tests/test_chaos.py::TestWriteQueueCrashRecovery::test_worker_survives_failing_callback` |
| P1.03.20 | stop() finishes the writes already queued | live | `tests/test_chaos.py::TestWriteQueueCrashRecovery::test_stop_flushes_pending_items` |
| P1.03.21 | MEDIUM writes run as one transaction per batch of up to 50 | planned: Windy Agent: the queue batches up to 50 but each write commits itself; share one transaction, or restate the promise as "batched for fairness" | — |
| P1.03.22 | Timestamps keep parsing on future Python versions | planned: Windy Agent: P1.03.2 relies on sqlite3's default timestamp converter, deprecated since Python 3.12 (warning in the strand run); register explicit adapters/converters | — |

### P1.04 Personality engine (gene 0.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.04.1 | SOUL.md is the personality and reaches the system message | live | `tests/strand/test_p1_loop.py::test_p1_04_soul_md_reaches_the_system_message` |
| P1.04.2 | A missing SOUL.md falls back to a default | live | `tests/test_personality.py::TestLoadSoul::test_returns_default_on_missing` |
| P1.04.3 | Humour below 3 strips the witty lines | live | `tests/test_personality.py::TestBuildPersonalityBlock::test_low_humor_strips_witty` |
| P1.04.4 | Formality above 7 adds the formal instruction | live | `tests/test_personality.py::TestBuildPersonalityBlock::test_high_formality_adds_instruction` |
| P1.04.5 | Sliders shape the personality block (e.g. autonomy) | live | `tests/test_autonomy_slider_wired.py::test_high_autonomy_emits_action_bias_modifier` |
| P1.04.6 | The personality block stays under 600 tokens | planned: Windy Agent: not enforced; about 474 tokens with the default SOUL.md at maximum sliders, but a custom SOUL.md has no cap | — |
| P1.04.7 | Modes (companion / focused / neutral) override the personality | retired: switching modes did nothing; modes removed (#499) | — |

### P1.05 Model calls (gene 0.5)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.05.1 | One call returns content, model, tokens and tool calls, and is costed once | live | `tests/test_cost_accounting.py::test_success_is_recorded_once_with_cost_and_billing` |
| P1.05.2 | An agent with a passport asks Windy Mind first | live | `tests/test_mind_broker_routing.py::test_passport_present_calls_mind_first` |
| P1.05.3 | Model names route to their provider (gpt/o to OpenAI, claude to Anthropic, ...) | live | `tests/strand/test_p1_loop.py::test_p1_05_model_names_route_to_their_provider` |
| P1.05.4 | Three retries with exponential backoff | retired: replaced by a failover chain with a per-provider cooldown (#46) | — |
| P1.05.5 | Every call, failed ones too, lands in the cost ledger | live | `tests/test_cost_accounting.py::test_ledger_sink_writes_success_and_failure_rows` |

### P1.06 Prompt and agent loop (gene 0.6)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.06.1 | The prompt starts with the system message | live | `tests/test_agent_loop.py::TestAssemblePrompt::test_has_system_message` |
| P1.06.2 | The user's message comes last | live | `tests/test_agent_loop.py::TestAssemblePrompt::test_has_user_message_at_end` |
| P1.06.3 | The last turns of this session are in the prompt; other sessions' are not | live | `tests/strand/test_p1_loop.py::test_p1_06_remembers_the_last_turn_in_the_session` |
| P1.06.4 | Relevant memory is added; high strictness drops inferred facts | live | `tests/test_agent_loop.py::TestEpistemicFiltering::test_high_strictness_filters_inferred` |
| P1.06.5 | The whole prompt stays under max_context_tokens | planned: Windy Agent: no overall cap (each block has its own budget; the system message alone is about 8.2k characters on an empty database); see P1.02.2 | — |
| P1.06.6 | The config reaches the model call | live | `tests/test_agent_loop.py::TestAgentRespond::test_passes_config_to_llm` |
| P1.06.7 | Each turn is saved as episodes (with its emotional context) | live | `tests/test_agent_loop.py::TestEmotionalContextIntegration::test_emotional_context_passed_to_episode` |
| P1.06.8 | Facts are learned only from the owner's own untainted words | live | `tests/test_facts_owner_only.py::TestTurns::test_owner_turn_stores_the_facts` |
| P1.06.9 | Nobody else's words become facts about the owner | live | `tests/test_facts_owner_only.py::TestTurns::test_non_owner_turns_store_no_fact_and_no_conflict` |
| P1.06.10 | The reply text is returned | live | `tests/test_agent_loop.py::TestAgentRespond::test_returns_response` |

### P1.07 Terminal chat and entry point (gene 0.7)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.07.1 | A typed line goes through the agent in one UUID session and the reply is printed | live | `tests/strand/test_p1_loop.py::test_p1_07_cli_turn_goes_through_the_agent_and_is_printed` |
| P1.07.2 | quit / exit leave the chat | live | `tests/test_chat_after_hatch.py::test_slash_quit_and_exit_leave_the_chat` |
| P1.07.3 | main loads the .env files before the config | live | `tests/strand/test_p1_loop.py::test_p1_07_main_loads_env_files_before_the_config` |

### P1.08 Tests and release (genes 0.8, 0.9)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.08.1 | Phase 0 tests exist and pass | retired: a property of the suite, not a codon; the scorecard and the required CI test jobs carry it (#526) | — |
| P1.08.2 | Phase 0 commit and tag v0.1.0 | retired: a one-time build step, not a runtime promise (#526) | — |

### P1.C0 Phase 0 checklist (Part 4 §14)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.C0.1 | Repo and directories | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[root]` |
| P1.C0.2 | windyfly.toml is parsed | live | `tests/test_config_resilience.py::test_valid_config_loads_normally` |
| P1.C0.3 | The six tables exist | live | `tests/test_memory.py::TestDatabaseCreation::test_creates_all_tables` |
| P1.C0.4 | Episodes are saved and read | live | `tests/test_memory.py::TestEpisodes::test_save_and_retrieve` |
| P1.C0.5 | Nodes are upserted and searched | live | `tests/test_memory.py::TestNodes::test_search_by_name` |
| P1.C0.6 | SOUL.md is in the system prompt | live | `tests/strand/test_p1_loop.py::test_p1_04_soul_md_reaches_the_system_message` |
| P1.C0.7 | A model answers through at least one provider | live | `tests/test_mind_broker_routing.py::test_passport_present_calls_mind_first` |
| P1.C0.8 | It answers in the terminal | live | `tests/strand/test_p1_loop.py::test_p1_07_cli_turn_goes_through_the_agent_and_is_printed` |
| P1.C0.9 | It remembers within a session | live | `tests/strand/test_p1_loop.py::test_p1_06_remembers_the_last_turn_in_the_session` |
| P1.C0.10 | Cost is recorded | live | `tests/test_cost_accounting.py::test_ledger_sink_writes_success_and_failure_rows` |
| P1.C0.11 | The write queue runs HIGH, then MEDIUM, then LOW | live | `tests/test_strand_wave4_write_queue.py::test_C0_11_strict_priority_order_with_several_items_each` |
| P1.C0.12 | All tests pass | retired: a property of the suite, not a codon; the required CI test jobs carry it (#526) | — |

## Part 2: Matrix, Windy Pro tools, soul, cockpit, truth layer

### P2.11 Matrix channel (gene 1.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P2.11.1 | The Matrix client is built from the config (homeserver; env overrides) | live | `tests/test_contract_matrix.py::TestBotIdentityContract::test_custom_homeserver` |
| P2.11.2 | The default homeserver is Windy Chat | live | `tests/test_contract_matrix.py::TestBotIdentityContract::test_default_homeserver` |
| P2.11.3 | Token login | live | `tests/test_matrix_bot.py::TestMatrixBotLogin::test_token_login` |
| P2.11.4 | The token's real owner is the agent's identity (never a stale configured id) | live | `tests/test_matrix_bot.py::TestMatrixBotLogin::test_identity_realigned_to_token_owner` |
| P2.11.5 | Password login when a user is configured | live | `tests/strand/test_p2_matrix.py::test_p2_11_password_login_with_a_user` |
| P2.11.6 | No credentials is a clear error, not a crash | live | `tests/test_matrix_bot.py::TestMatrixBotLogin::test_no_credentials_raises` |
| P2.11.7 | The agent ignores its own messages | live | `tests/test_matrix_bot.py::TestMatrixBotMessage::test_ignores_own_messages` |
| P2.11.8 | A reconnect backlog is not replayed (messages older than 120 s) | live | `tests/test_matrix_bot.py::TestMatrixBotMessage::test_ignores_backlog_messages` |
| P2.11.9 | One session per room | live | `tests/test_matrix_bot.py::TestMatrixBotMessage::test_creates_session_per_room` |
| P2.11.10 | A message gets an answer in the same room | live | `tests/test_matrix_bot.py::TestMatrixBotMessage::test_responds_to_fresh_message` |
| P2.11.11 | Replies carry windy_original and windy_lang | live | `tests/test_contract_matrix.py::TestMessageFormatContract::test_response_message_has_correct_schema` |
| P2.11.12 | windy_lang is detected from the reply text | live | `tests/test_contract_matrix.py::TestMessageFormatContract::test_windy_lang_detects_non_latin` |
| P2.11.13 | The sender's windy_lang is read from the incoming event | planned: Windy Agent: never read (only outbound sets it); use it to answer in the sender's language, or retire the promise | — |
| P2.11.14 | Invites to this agent are accepted | live | `tests/test_matrix_bot.py::TestMatrixBotInvite::test_accepts_invite` |
| P2.11.15 | Invites for someone else are ignored | live | `tests/test_matrix_bot.py::TestMatrixBotInvite::test_ignores_invite_for_others` |
| P2.11.16 | The welcome message has the agreed shape | live | `tests/test_contract_matrix.py::TestMessageFormatContract::test_welcome_message_has_correct_schema` |
| P2.11.17 | A shutdown signal stops the sync loop and shuts down cleanly | live | `tests/test_matrix_bot.py::TestMatrixBotGracefulShutdown::test_sigterm_cancels_sync_and_runs_graceful` |
| P2.11.18 | `--channel matrix` wires the full runtime | live | `tests/test_matrix_db_wired.py::test_matrix_branch_wires_runtime` |
| P2.11.19 | End-to-end encryption via matrix-nio[e2e] | retired: never active (no olm); dead E2E code removed, one honest line for encrypted events (#505) | — |

### P2.12 Typing and presence (gene 1.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P2.12.1 | Typing shows before the turn and stops after the reply | live | `tests/strand/test_p2_matrix.py::test_p2_12_typing_on_before_the_turn_and_off_after_the_reply` |
| P2.12.2 | Presence is online at start and offline at shutdown | live | `tests/strand/test_p2_matrix.py::test_p2_12_presence_online_at_start` |

### P2.13 Offline queue and reconnection (gene 1.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P2.13.1 | A failing sync backs off exponentially, then reconnects | live | `tests/test_matrix_sync_backoff.py::TestSyncErrorBackoff::test_sleeps_exponentially_then_escapes` |
| P2.13.2 | A reply that fails to send is kept for later | live | `tests/strand/test_p2_matrix.py::test_p2_13_a_failed_reply_is_kept_for_later` |
| P2.13.3 | Kept replies are sent once the connection is back | live | `tests/test_matrix_bot.py::TestPendingResponseQueue::test_flush_pending` |
| P2.13.4 | Kept replies go out on the next successful sync (not only after a reconnect) | planned: Windy Agent: the queue flushes only at reconnect and shutdown, so while sync stays healthy a failed reply waits; flush from the sync-success callback | — |

### P2.14 Tool registry and Windy Pro tools (gene 1.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P2.14.1 | Tools register with a name, description, parameters and function | live | `tests/test_tools.py::TestToolRegistry::test_register_and_count` |
| P2.14.2 | Schemas come out in the function-calling format | live | `tests/test_tools.py::TestToolRegistry::test_get_schemas` |
| P2.14.3 | execute() runs a tool and returns a string | live | `tests/test_tools.py::TestToolRegistry::test_execute_with_dict` |
| P2.14.4 | An unknown tool or a failing tool returns an error, never raises | live | `tests/test_tools.py::TestToolRegistry::test_execute_error_returns_error_json` |
| P2.14.5 | The model's tool calls are executed inside the turn | live | `tests/test_tool_calling_e2e.py::TestAgentLoopToolCalling::test_agent_executes_weather_tool` |
| P2.14.6 | Translation history from Windy Pro (path + auth) | live | `tests/test_contract_pro_api.py::TestTranslationHistoryContract::test_correct_path_and_auth` |
| P2.14.7 | Recordings from Windy Pro | live | `tests/test_tools.py::TestWindyApiTools::test_get_recordings` |
| P2.14.8 | Clone training status from Windy Pro | live | `tests/test_tools.py::TestWindyApiTools::test_get_clone_status` |
| P2.14.9 | Translate text through Windy Pro (path, method, body) | live | `tests/test_contract_pro_api.py::TestTranslateContract::test_correct_path_method_and_body` |
| P2.14.10 | The four Windy Pro tools register when Windy Pro is configured and answers | live | `tests/test_tools.py::TestWindyToolsOnlyWhenConfigured::test_answering_pro_registers_four` |

### P2.15 Phase 1 tests and release (gene 1.5)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P2.15.1 | Phase 1 test files exist and pass; tag v0.2.0 | retired: properties of the suite and a one-time release step, not codons (#526) | — |

### P2.21 Soul import (gene 2.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P2.21.1 | An OpenClaw export is read (soul, memories, skills) | live | `tests/test_soul_import.py::TestOpenClawParser::test_parses_soul` |
| P2.21.2 | A Hermes export is read (MEMORY.md, skills) | live | `tests/test_soul_import.py::TestHermesParser::test_parses_memory_file` |
| P2.21.3 | Hermes's SQLite store is read | planned: Windy Agent: the reader guesses table names and no test drives it; prove it on a real-shaped Hermes database or drop it | — |
| P2.21.4 | A ChatGPT export is read for preferences and topics | live | `tests/test_soul_import.py::TestChatGPTParser::test_parses_conversations` |
| P2.21.5 | ChatGPT's communication style is extracted | planned: Windy Agent: personality is always empty for ChatGPT exports | — |
| P2.21.6 | The source is detected automatically | live | `tests/test_soul_import.py::TestSourceDetection::test_detects_openclaw` |
| P2.21.7 | The Soul Preview shows what would be imported | live | `tests/test_soul_import.py::TestSoulPreview::test_format_preview` |
| P2.21.8 | Without approval nothing is written | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_preview_writes_nothing` |
| P2.21.9 | Approved items are stored as low-confidence imports (0.5, imported_<source>, inferred) | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_approved_items_are_stored_as_low_confidence_imports` |
| P2.21.10 | Sensitive items are stored at 0.3, flagged for review; code skills are skipped, prose skills become unpromoted playbooks | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_sensitive_items_are_flagged_and_code_skills_skipped` |
| P2.21.11 | The owner can review flagged items | planned: Windy Agent: needs_review is stored but nothing reads it; the preview offers "review sensitive" with no flow behind it | — |
| P2.21.12 | Imported personality (traits, humour, formality) is applied on approval | planned: Windy Agent: shown in the preview, then dropped by the importer | — |
| P2.21.13 | The owner can start an import without a terminal | planned: Windy Agent: reachable only through the bridge and the gateway API; no command or dashboard page calls it | — |

### P2.22 The cockpit: presets and sliders (gene 2.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P2.22.1 | Preset buddy sets its slider values | live | `tests/test_control_panel.py::TestPresets::test_apply_buddy` |
| P2.22.2 | Preset engineer sets its slider values | live | `tests/test_control_panel.py::TestPresets::test_apply_engineer` |
| P2.22.3 | Preset powerhouse sets its slider values | live | `tests/test_control_panel.py::TestPresets::test_apply_powerhouse` |
| P2.22.4 | A preset persists | live | `tests/test_control_panel.py::TestPresets::test_preset_persists` |
| P2.22.5 | /preset really applies | live | `tests/test_slider_preset_slash.py::test_preset_buddy_actually_applies` |
| P2.22.6 | Sliders are range-checked | live | `tests/test_control_panel.py::TestSliders::test_value_too_low` |
| P2.22.7 | Unknown slider names are refused | live | `tests/test_control_panel.py::TestSliders::test_invalid_slider_name` |
| P2.22.8 | Sliders read back, with config defaults | live | `tests/test_control_panel.py::TestSliders::test_config_defaults` |
| P2.22.9 | Monthly cost is estimated from the sliders | live | `tests/test_control_panel.py::TestCostEstimation::test_powerhouse_more_than_engineer` |
| P2.22.10 | With raw mode off, the sliders shape the agent's tone | live | `tests/test_control_panel.py::test_turning_raw_off_hands_tone_back_to_the_sliders` |
| P2.22.11 | /preset says when raw mode means the preset won't change the tone | planned: Windy Agent: raw mode is the default (Grant 07-25), so buddy and engineer give the same prompt while /preset still answers "applied" | — |

### P2.23 Truth layer (gene 2.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P2.23.1 | Every remembered fact carries its label ([VERIFIED], [USER_STATED], [INFERRED], ...) | live | `tests/strand/test_p2_soul_truth.py::test_p2_23_every_fact_carries_its_label_at_low_strictness` |
| P2.23.2 | Strictness above 7 drops inferred and speculative facts | live | `tests/strand/test_p2_soul_truth.py::test_p2_23_strictness_above_7_drops_inferred_and_speculative` |
| P2.23.3 | Strictness above 9 keeps only verified and owner-stated facts | live | `tests/strand/test_p2_soul_truth.py::test_p2_23_strictness_above_9_keeps_only_verified_and_user_stated` |
| P2.23.4 | The model is told to say how sure it is and to flag inferred facts | live | `tests/strand/test_p2_soul_truth.py::test_p2_23_the_model_is_told_to_say_how_sure_it_is` |

### P2.24 "Never Wrong Twice" (gene 2.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P2.24.1 | A factual correction is detected | live | `tests/test_control_panel.py::TestFrictionDetection::test_detects_factual_error` |
| P2.24.2 | Agreement is never mistaken for a correction | live | `tests/test_failure_detector_precision.py::TestFrictionPrecision::test_no_false_positive_on_agreement` |
| P2.24.3 | Common corrections ("That is wrong", "Incorrect, it was ...", "Actually ...") are detected | planned: Windy Agent: the patterns were tuned for precision and miss these; widen them without breaking P2.24.2 | — |
| P2.24.4 | Failures are logged and read back | live | `tests/test_memory.py::TestFailures::test_log_and_retrieve` |
| P2.24.5 | Recurring failures are found over 7 days | live | `tests/test_memory.py::TestFailures::test_recurring_check` |
| P2.24.6 | A correction reaches the model as a correction | live | `tests/strand/test_p2_soul_truth.py::test_p2_24_a_correction_reaches_the_model_as_a_correction` |
| P2.24.7 | A recurring failure adds a stronger warning | live | `tests/test_control_panel.py::TestFrictionHandling::test_recurring_gives_extra_warning` |
| P2.24.8 | Recurring corrections become lessons in the prompt | live | `tests/test_correction_skill_application.py::test_lessons_block_appears_when_skills_present` |

### P2.25 Phase 2 tests and release (gene 2.5)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P2.25.1 | Phase 2 test files exist and pass; tag v0.3.0 | retired: properties of the suite and a one-time release step, not codons (#526) | — |

## Part 3: skills, cost, intents, emotion, gateway, decay, conflicts, offline

### P3.31 Skills (gene 3.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.31.1 | A skill is created and stored (unpromoted by default) | live | `tests/test_skills_engine.py::TestSkillManager::test_create_skill` |
| P3.31.2 | Saving a skill promotes it and mirrors it to a file | live | `tests/test_skill_engine.py::TestSaveViewList::test_save_promotes_and_mirrors_to_file` |
| P3.31.3 | Viewing a skill returns its text and counts the use | live | `tests/test_skill_engine.py::TestSaveViewList::test_view_returns_body_and_counts_usage` |
| P3.31.4 | Re-saving bumps the version and keeps the lineage | live | `tests/test_skill_engine.py::TestSaveViewList::test_resave_bumps_version_with_lineage` |
| P3.31.5 | A skill rolls back to its parent | live | `tests/test_hardening_skills.py::TestSkillRollback::test_rollback_to_parent` |
| P3.31.6 | Skill files dropped in the skills folder become skills | live | `tests/test_skill_engine.py::TestFileSync::test_dropped_file_becomes_promoted_skill` |
| P3.31.7 | Skills are text playbooks only: no skill text can ever run as code | live | `tests/test_skills_text_only.py::test_skills_module_cannot_execute_code` |
| P3.31.8 | Only the playbook language is accepted | live | `tests/test_skills_text_only.py::test_only_playbook_is_accepted` |
| P3.31.9 | Skills run in a sandbox, promoted only after syntax, execution and safety gates | retired: the sandbox did not isolate and the safety gate was bypassable; executable skills, sandbox, evaluator and golden tests removed (#504) | — |
| P3.31.10 | Success and failure uses are counted separately | planned: Windy Agent: increment_usage has no callers; viewing counts a use without success or failure | — |

### P3.32 Cost ledger and budget (gene 3.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.32.1 | Every model call writes one cost row (failed calls too) | live | `tests/test_cost_accounting.py::test_ledger_sink_writes_success_and_failure_rows` |
| P3.32.2 | Prices come from a rate card; an unknown model costs nothing and warns once | live | `tests/test_cost_accounting.py::test_unknown_model_has_no_cost_and_warns_once` |
| P3.32.3 | Monthly spend is summed | live | `tests/test_cost_tracker.py::TestCostTracker::test_monthly_spend_with_entries` |
| P3.32.4 | The budget check allows spending under budget | live | `tests/test_cost_tracker.py::TestBudgetCheck::test_within_budget` |
| P3.32.5 | Over budget, the turn is blocked before the model is called | live | `tests/test_agent_loop.py::TestBudgetEnforcement::test_blocks_when_over_budget` |
| P3.32.6 | Over budget, the owner can choose to go on anyway | planned: Windy Agent: a hard block today; the only way past is raising the budget in settings | — |
| P3.32.7 | The user is warned in chat as spending nears the budget | retired: the warning leaked into user replies; it goes to the log instead (#72) | — |

### P3.33 Intents (gene 3.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.33.1 | Migration 2 adds the intents, edges, conflicts and soul-history tables | live | `tests/test_intents.py::TestMigrationV2::test_intents_table_exists` |
| P3.33.2 | Intents are created, read, completed and paused | live | `tests/test_intents.py::TestIntentCRUD::test_create_and_get` |
| P3.33.3 | Goals ("I want / need / remind me") are detected from the owner's words | live | `tests/test_intents.py::TestIntentDetection::test_detect_want` |
| P3.33.4 | A plain question is not a goal | live | `tests/test_intents.py::TestIntentDetection::test_no_intent_in_question` |
| P3.33.5 | The model-based goal detector builds its prompt without error | live | `tests/test_intents.py::TestLLMIntentPath::test_prompt_formats_without_error` |
| P3.33.6 | Model-based goal detection is off by default (cost; Boss) | live | `tests/test_intents.py::TestLLMIntentPath::test_llm_off_by_default` |
| P3.33.7 | When switched on, an inferred goal reaches the 24-hour inbox | live | `tests/test_intents.py::TestLLMIntentPath::test_llm_answer_becomes_inbox_intent` |
| P3.33.8 | Mentioning a goal again keeps it fresh | live | `tests/test_intents.py::TestTouchIntent::test_touch_resets_score_and_clock` |
| P3.33.9 | Stale goals decay and pause on the daily schedule | live | `tests/test_intents.py::TestDecaySchedulerRunsIntentDecay::test_scheduler_decays_and_pauses_stale_intents` |
| P3.33.10 | /decay also decays goals | live | `tests/test_decay_runs_intents.py::test_slash_decay_pauses_stale_intents` |
| P3.33.11 | Edges link memories into a graph | planned: Windy Agent: the edges table exists but nothing writes to it; build the graph or drop the table | — |

### P3.34 Emotional awareness (gene 3.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.34.1 | Stress is detected in a message | live | `tests/test_emotion.py::TestEmotionDetection::test_detect_stress_frustrated` |
| P3.34.2 | Excitement is detected in a message | live | `tests/test_emotion.py::TestEmotionDetection::test_detect_excitement` |
| P3.34.3 | Sustained stress is read from the owner's own turns only | live | `tests/test_emotion.py::TestEmotionalTrend::test_sustained_stress_with_interleaved_assistant_turns` |
| P3.34.4 | Sustained stress adds a supportive note; excitement matches energy | retired: prompt steering by emotion removed in favour of the substrate (#304) | — |

### P3.35 Phase 3 tests and release (gene 3.5)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.35.1 | Phase 3 test files exist and pass; tag v0.4.0 | retired: properties of the suite and a one-time release step, not codons (#526) | — |

### P3.41 Brain bridge and gateway (gene 4.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.41.1 | The brain opens its bridge socket and removes it on stop | live | `tests/test_bridge_integration.py::TestBridgeLifecycle::test_start_creates_socket_file` |
| P3.41.2 | A malformed frame gets a structured error, not a crash | live | `tests/test_bridge_integration.py::TestFrameParsing::test_invalid_json_returns_structured_error` |
| P3.41.3 | A chat turn round-trips through the bridge | live | `tests/test_bridge_integration.py::TestAgentRespondViaBridge::test_full_roundtrip` |
| P3.41.4 | Sliders are read and set through the bridge | live | `tests/test_bridge_integration.py::TestDispatchRoundtrips::test_sliders_set_and_get_roundtrip` |
| P3.41.5 | Today's spend is read through the bridge | live | `tests/test_bridge_integration.py::TestDispatchRoundtrips::test_cost_daily_returns_float` |
| P3.41.6 | Goals are listed through the bridge | live | `tests/test_bridge_integration.py::TestDispatchRoundtrips::test_intents_list_returns_array` |
| P3.41.7 | Memory is searched through the bridge (knowledge nodes) | live | `tests/test_bridge_integration.py::TestDispatchRoundtrips::test_memory_search_returns_list` |
| P3.41.8 | Bridge memory search also searches past conversations | planned: Windy Agent: memory.search returns nodes only; episodes are not searched | — |
| P3.41.9 | The gateway's health route reports whether the brain is connected | live | `tests/test_gateway_smoke.py::TestHealthRoute::test_bridge_reports_connected_state` |
| P3.41.10 | Gateway HTTP and WebSocket chat are proven end to end without a live stack | planned: Windy Agent: only live-gated tests cover the gateway boot and the /ws/chat proxy; add a hermetic bun test gateway -> bridge | — |

### P3.42 Cognitive decay (gene 4.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.42.1 | Old memories fade on each decay run | live | `tests/test_phase4.py::TestCognitiveDecay::test_decay_returns_counts` |
| P3.42.2 | A faded memory (below 0.2) becomes speculative but is kept | live | `tests/strand/test_p3_memory.py::test_p3_42_faded_memories_become_speculative_but_are_kept` |
| P3.42.3 | Decay never erases a memory (Chronicle Law 1) | live | `tests/test_decay_immediate.py::TestChronicleLaw1::test_low_score_nodes_survive_with_floored_weight` |
| P3.42.4 | Old conversations are never rewritten or pruned | live | `tests/test_decay_immediate.py::TestChronicleLaw1::test_ancient_episode_content_never_touched` |
| P3.42.5 | Memories below 0.05 are deleted; episodes over 90 days are summarised and deleted | retired: decay may dim, never erase (Doctrine Law 1) (#312) | — |
| P3.42.6 | Decay runs on demand and reports what it did | live | `tests/test_decay_immediate.py::TestDecayImmediate::test_returns_nonzero_counts_when_nodes_exist` |

### P3.43 Conflicts (gene 4.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.43.1 | A contradicting update is detected | live | `tests/test_phase4.py::TestConflictDetector::test_detects_conflict` |
| P3.43.2 | A contradicting update is held, not applied over the old value | live | `tests/test_memory_conflict_hold.py::TestHold::test_contradicting_update_does_not_overwrite` |
| P3.43.3 | "Keep old" keeps the old value | live | `tests/test_memory_conflict_hold.py::TestResolve::test_keep_old_keeps_the_old_value` |
| P3.43.4 | "Keep new" applies the held value | live | `tests/test_memory_conflict_hold.py::TestResolve::test_keep_new_applies_the_held_value` |
| P3.43.5 | The owner is shown a pending conflict and asked to choose | live | `tests/test_memory_conflict_hold.py::TestPromptFacts::test_owner_turn_carries_the_pending_conflict` |
| P3.43.6 | Nobody but the owner sees the owner's conflicts | live | `tests/test_memory_conflict_hold.py::TestPromptFacts::test_non_owner_bands_never_see_it` |

### P3.44 Sub-agents (gene 4.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.44.1 | Specialist sub-agents with their own context, cost tag, time budget and depth 1 | retired: unused; sub_agents.py and shape-shift removed (#501) | — |

### P3.45 Offline fallback (gene 4.5)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.45.1 | When the online check itself fails, the agent assumes it is online | live | `tests/test_is_online_resilience.py::test_all_probes_fail_defaults_to_online` |
| P3.45.2 | Offline, a local model answers | live | `tests/test_phase4.py::TestOfflineMode::test_offline_response_with_ollama` |
| P3.45.3 | Offline with no local model, the owner gets a plain offline message | live | `tests/test_phase4.py::TestOfflineMode::test_offline_response_no_ollama` |
| P3.45.4 | Messages are queued safely while offline (atomic write) | live | `tests/test_offline_queue_atomic.py::TestOfflineQueueAtomic::test_atomic_write_survives_crash` |
| P3.45.5 | Back online, nothing queued goes through the model; the owner is told how many waited | live | `tests/test_offline_queue_no_replay.py::test_nothing_goes_through_the_model` |
| P3.45.6 | Back online, the queue is replayed through the model | retired: a replay drove a spend auto-pause; never replay, tell the owner (#434) | — |

### P3.46 Phase 4 tests and release (gene 4.6)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.46.1 | Phase 4 test files exist and pass; tag v0.5.0 | retired: properties of the suite and a one-time release step, not codons (#526) | — |

## Part 4: Phase 5 (dashboard, personality versioning, observability), ecosystem map, file index

### P4.51 Trust dashboard data (gene 5.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.51.1 | The dashboard summary has every section, even on an empty agent | live | `tests/test_phase5.py::TestDashboard::test_empty_dashboard` |
| P4.51.2 | It counts memories and conversations | live | `tests/test_phase5.py::TestDashboard::test_populated_dashboard` |
| P4.51.3 | Memories are broken down by how sure the agent is and by scope | live | `tests/strand/test_p4_phase5.py::test_p4_51_memory_is_broken_down_by_status_and_scope` |
| P4.51.4 | Spend today, this week, this month and by kind of task | live | `tests/strand/test_p4_phase5.py::test_p4_51_costs_today_week_month_and_by_task_type` |
| P4.51.5 | Unpriced (local or Mind) cost rows never break the summary | live | `tests/test_phase5.py::TestDashboard::test_unpriced_only_task_type_does_not_crash` |
| P4.51.6 | Failures: total, resolved and the improvement rate | live | `tests/test_phase5.py::TestDashboard::test_failure_improvement_rate` |
| P4.51.7 | The five most used promoted skills, with their success rate | live | `tests/strand/test_p4_phase5.py::test_p4_51_top_five_promoted_skills_by_use` |
| P4.51.8 | Goals counted as active, completed and paused ("abandoned") | live | `tests/strand/test_p4_phase5.py::test_p4_51_goals_counted_by_state` |
| P4.51.9 | The personality preset is named, or "custom" | live | `tests/test_phase5.py::TestDashboard::test_preset_detection` |
| P4.51.10 | The summary is served through the bridge to the gateway | live | `tests/strand/test_p4_phase5.py::test_p4_51_the_summary_is_served_through_the_bridge` |

### P4.52 Personality versioning, drift and rollback (gene 5.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.52.1 | A snapshot records the sliders that changed (and only those) | live | `tests/test_retention.py::TestSnapshotDedup::test_changed_slider_records_exactly_one_new_row` |
| P4.52.2 | The personality history names each slider and can be filtered | live | `tests/test_personality_drift.py::TestHistory::test_history_names_the_slider_and_can_filter` |
| P4.52.3 | Drift compares against the values from 30 days ago | live | `tests/test_personality_drift.py::TestDetectDrift::test_agent_change_against_30_day_baseline_is_drift` |
| P4.52.4 | The owner's own slider changes are never called drift | live | `tests/test_personality_drift.py::TestDetectDrift::test_owner_change_is_not_drift` |
| P4.52.5 | A move of 2 points or less is not drift | live | `tests/test_personality_drift.py::TestDetectDrift::test_small_agent_change_is_not_drift` |
| P4.52.6 | The daily check detects before it snapshots, so drift can fire | live | `tests/test_personality_drift.py::TestPeriodicCheck::test_scheduler_detects_before_snapshotting` |
| P4.52.7 | Drift reaches the owner's dashboard summary | live | `tests/test_personality_drift.py::TestPeriodicCheck::test_dashboard_summary_includes_drift` |
| P4.52.8 | The drift alert clears once the drift is gone | live | `tests/test_personality_drift.py::TestPeriodicCheck::test_alert_clears_when_drift_resolves` |
| P4.52.9 | A rollback restores the earlier value and clears the drift | live | `tests/test_personality_drift.py::TestRollback::test_rollback_restores_value_and_clears_drift` |
| P4.52.10 | A rollback is recorded in the history as a rollback | live | `tests/strand/test_p4_phase5.py::test_p4_52_a_rollback_is_recorded_as_a_rollback` |
| P4.52.11 | A rollback touches personality sliders only | live | `tests/test_personality_drift.py::TestRollback::test_rollback_only_touches_personality_sliders` |
| P4.52.12 | Before a rollback the owner sees exactly what will change (the diff), and nothing is written | live | `tests/test_personality_drift.py::TestRollbackShowsExactlyWhatItRestores::test_plan_lists_only_what_changes_and_writes_nothing` |
| P4.52.13 | If a slider moved after the preview, the rollback restores nothing | live | `tests/test_personality_drift.py::TestRollbackRefusesAChangedPlan::test_slider_moved_after_preview_restores_nothing` |
| P4.52.14 | The preview works through the bridge | live | `tests/test_personality_drift.py::TestRollbackShowsExactlyWhatItRestores::test_bridge_dry_run_returns_changes_and_changes_nothing` |

### P4.53 Automated skill evaluation v2 (gene 5.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.53.1 | Golden tests per skill, run in a sandbox | retired: skills are text playbooks; skill code execution and golden_tests.py removed (#504) | — |
| P4.53.2 | A regression suite over promoted skills alerts the owner | retired: nothing to execute once skills are text only (#504) | — |

### P4.54 Observability: the event log (gene 5.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.54.1 | Events are written to the events table and read back | live | `tests/test_phase5.py::TestEventLogging::test_log_and_retrieve` |
| P4.54.2 | Event writes are LOW priority | live | `tests/strand/test_p4_phase5.py::test_p4_54_events_are_written_at_low_priority` |
| P4.54.3 | Every declared event type is emitted somewhere in the code | live | `tests/test_event_log.py::test_every_declared_event_type_is_emitted_somewhere` |
| P4.54.4 | Memory writes are recorded without the owner's words | live | `tests/test_event_log.py::test_memory_write_records_insert_and_update_without_the_owners_words` |
| P4.54.5 | A held conflict is recorded once | live | `tests/test_event_log.py::test_a_held_conflict_is_recorded_once_and_is_not_a_memory_write` |
| P4.54.6 | A surfaced goal is recorded without its description | live | `tests/test_event_log.py::test_intent_surface_on_create_and_touch_without_the_description` |
| P4.54.7 | Recording an event never breaks the write it describes | live | `tests/test_event_log.py::test_record_event_never_breaks_the_write_it_describes` |
| P4.54.8 | Events older than 30 days are pruned | live | `tests/test_retention.py::TestEventsPrune::test_prune_events_drops_old_keeps_recent` |
| P4.54.9 | Events are read (and filtered by type) through the bridge | live | `tests/strand/test_p4_phase5.py::test_p4_54_events_are_read_through_the_bridge` |
| P4.54.10 | cost.log, skill.evaluate and matrix.message events | retired: no emitter and no reader here or in 9 sister repos; the cost ledger is the one cost record (#524) | — |
| P4.54.11 | Console logs are structured JSON | retired: the event log is the structured record; console output is for humans (#524) | — |

### P4.55 Phase 5 tests and release (gene 5.5)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.55.1 | Phase 5 test files exist and pass; tag v1.0.0 | retired: properties of the suite and a one-time release step, not codons (#526) | — |

### P4.C5 Phase 5 checklist (Part 4 §14)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.C5.1 | The dashboard returns the full summary | live | `tests/test_phase5.py::TestDashboard::test_empty_dashboard` |
| P4.C5.2 | A snapshot is saved to soul_history | live | `tests/test_retention.py::TestSnapshotDedup::test_changed_slider_records_exactly_one_new_row` |
| P4.C5.3 | Drift detection flags changes the owner did not make | live | `tests/test_personality_drift.py::TestDetectDrift::test_owner_change_is_not_drift` |
| P4.C5.4 | A rollback restores the earlier state | live | `tests/test_personality_drift.py::TestRollback::test_rollback_restores_value_and_clears_drift` |
| P4.C5.5 | Golden tests run against promoted skills | retired: skill code execution removed (#504) | — |
| P4.C5.6 | The regression suite catches broken skills | retired: skill code execution removed (#504) | — |
| P4.C5.7 | The event log captures the system's events (every declared type is emitted) | live | `tests/test_event_log.py::test_every_declared_event_type_is_emitted_somewhere` |
| P4.C5.8 | Events are pruned after 30 days | live | `tests/test_retention.py::TestEventsPrune::test_prune_events_drops_old_keeps_recent` |

### P4.11 Windy ecosystem map (Part 4 §11)

The map predates Windy Mind, Eternitas, Mail, Calendar, Search, teams and Admin telemetry; those
are the new genes (Wave 5g).

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.11.1 | The brain's parts exist: loop, memory, skills, control panel, Matrix bot, bridge | live | `tests/strand/test_p4_map_index.py::test_p4_11_the_brain_parts_in_the_map_exist` |
| P4.11.2 | The gateway serves the web UI on localhost:3000 | planned: Windy Agent: proven by bun tests in CI, not yet by a scorecard test (see P3.41.10) | — |
| P4.11.3 | The gateway's WebSocket chat works end to end | planned: Windy Agent: live-gated only (see P3.41.10) | — |
| P4.11.4 | Sliders are set through the gateway's REST route | live | `tests/test_gateway_smoke.py::TestSlidersSetRoute::test_set_valid_slider` |
| P4.11.5 | Spend is read through the gateway's REST route | live | `tests/test_gateway_smoke.py::TestCostDailyRoute::test_reflects_logged_cost` |
| P4.11.6 | The gateway talks to the brain over the UDS bridge (the brain's own process, not the Matrix bot's) | live | `tests/test_bridge_integration.py::TestAgentRespondViaBridge::test_full_roundtrip` |
| P4.11.7 | The homeserver is Windy Chat (chat.windychat.ai) | live | `tests/test_contract_matrix.py::TestBotIdentityContract::test_default_homeserver` |
| P4.11.8 | One shared @windyfly bot user | retired: every agent is its own @agent_<passport>, minted at hatch; the shared id was removed (#505) | — |
| P4.11.9 | Rooms are end-to-end encrypted | retired: never active (no olm); dead E2E code removed, one honest line for encrypted events (#505) | — |
| P4.11.10 | Presence shows the agent online | live | `tests/strand/test_p2_matrix.py::test_p2_12_presence_online_at_start` |
| P4.11.11 | Typing shows while the agent works | live | `tests/strand/test_p2_matrix.py::test_p2_12_typing_on_before_the_turn_and_off_after_the_reply` |
| P4.11.12 | Read receipts | planned: Windy Agent: the agent sends none; send them or drop the promise | — |
| P4.11.13 | Onboarding (K2) gives the agent its chat account, using its passport | live | `tests/test_chat_session.py::TestFetchAgentChatSession::test_sends_ept_bearer_and_returns_session` |
| P4.11.14 | The directory (K3) lists the agent as discoverable | planned: Windy Agent: no registration call (teams only reads the owner's roster); decide with Windy Chat whether agents are listed | — |
| P4.11.15 | Push (K6) delivers the agent's replies to the phone | planned: Windy Agent: server-side, nothing in this repo; Windy Chat to confirm push covers agent replies | — |
| P4.11.16 | The agent's conversations are backed up (its own encrypted database backup to Windy Cloud, in place of K8) | live | `tests/test_cloud_backup_contract.py::TestListAndRestoreRoundTrip::test_backup_list_restore` |
| P4.11.17 | Translation history from the Windy Pro account server | live | `tests/test_contract_pro_api.py::TestTranslationHistoryContract::test_correct_path_and_auth` |
| P4.11.18 | Recordings from the account server | live | `tests/test_tools.py::TestWindyApiTools::test_get_recordings` |
| P4.11.19 | Clone training data from the account server | live | `tests/test_tools.py::TestWindyApiTools::test_get_clone_status` |
| P4.11.20 | Translate text through the account server | live | `tests/test_contract_pro_api.py::TestTranslateContract::test_correct_path_method_and_body` |
| P4.11.21 | The supported-languages list from the account server | planned: Windy Agent: not wrapped; wrap it or drop it | — |
| P4.11.22 | The JWT is verified with /auth/me | planned: Windy Agent: identity is the passport (EPT/JWKS) now; record the retirement in a PR | — |
| P4.11.23 | The account-server JWT comes from the environment, not windyfly.toml | live | `tests/test_contract_pro_api.py::TestTranslationHistoryContract::test_correct_path_and_auth` |
| P4.11.24 | Shared contracts are vendored and pinned (JSON from windy-contracts) | live | `tests/test_shared_commands.py::test_the_vendored_contract_is_the_pinned_one` |
| P4.11.25 | Voice notes are transcribed in the agent (its own faster-whisper, optional extra) and answered | live | `tests/test_voice_handler.py::test_voice_handler_dispatches_transcript_to_agent` |
| P4.11.26 | The agent can query and tune WindySense | planned: Windy Agent: no integration; Hub decides whether it is still wanted | — |
| P4.11.27 | The phone and the agent share Matrix under a pinned contract (phone tools off by default) | live | `tests/test_phone_tools.py::test_dark_by_default` |
| P4.11.28 | Files are stored on Windy Cloud | live | `tests/test_cloud_tool.py::TestUploadHappyPath::test_uploads_with_correct_url_and_auth` |
| P4.11.29 | Files on Windy Cloud are listed | live | `tests/test_cloud_tool.py::TestListInbox::test_normalises_files_array_in_envelope` |
| P4.11.30 | Files on Windy Cloud are retrieved | planned: Windy Agent: download is not wrapped | — |

### P4.12 Master file index (Part 4 §12)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.12.1 | Phase 0 files exist | live | `tests/strand/test_p4_map_index.py::test_p4_12_index[phase0]` |
| P4.12.2 | Phase 1 files exist | live | `tests/strand/test_p4_map_index.py::test_p4_12_index[phase1]` |
| P4.12.3 | Phase 2 files exist | live | `tests/strand/test_p4_map_index.py::test_p4_12_index[phase2]` |
| P4.12.4 | Phase 3 files exist | live | `tests/strand/test_p4_map_index.py::test_p4_12_index[phase3]` |
| P4.12.5 | Phase 4 files exist (brain and gateway) | live | `tests/strand/test_p4_map_index.py::test_p4_12_index[phase4]` |
| P4.12.6 | Phase 5 files exist | live | `tests/strand/test_p4_map_index.py::test_p4_12_index[phase5]` |
| P4.12.7 | personality/mode.py | retired: did nothing; removed (#499) | — |
| P4.12.8 | skills/sandbox.py, skills/evaluator.py, skills/golden_tests.py | retired: executable skills, sandbox, evaluator and golden tests removed (#504) | — |
| P4.12.9 | agent/sub_agents.py | retired: unused; sub_agents.py and shape-shift removed (#501) | — |
| P4.12.10 | "About 47 source files" | retired: a count of the old plan, not a promise; this file is the record (#526) | — |

### P4.13 Roadmap (Part 4 §13)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.13.1 | Tags v0.1.0 to v1.0.0, one per phase, over 18 weeks | retired: one-time release steps, not codons (#526) | — |
| P4.13.2 | The version line is clear (the CHANGELOG has no 1.0.0 and reuses 0.1.0, 0.4.0 and 0.5.0 after the reset to 0.6/0.7) | planned: Windy Agent: one CHANGELOG note explaining the reset | — |

### P4.C1 Phase 1 checklist (Part 4 §14)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.C1.1 | matrix-nio is installed and imports | live | `tests/strand/test_p1_foundation.py::test_p1_1_matrix_nio_is_the_chat_protocol` |
| P4.C1.2 | The agent logs into the homeserver | live | `tests/test_matrix_bot.py::TestMatrixBotLogin::test_token_login` |
| P4.C1.3 | It accepts room invites to itself | live | `tests/test_matrix_bot.py::TestMatrixBotInvite::test_accepts_invite` |
| P4.C1.4 | It answers messages | live | `tests/test_matrix_bot.py::TestMatrixBotMessage::test_responds_to_fresh_message` |
| P4.C1.5 | Typing shows while it works | live | `tests/strand/test_p2_matrix.py::test_p2_12_typing_on_before_the_turn_and_off_after_the_reply` |
| P4.C1.6 | Presence shows it online | live | `tests/strand/test_p2_matrix.py::test_p2_12_presence_online_at_start` |
| P4.C1.7 | Replies carry windy_lang | live | `tests/test_contract_matrix.py::TestMessageFormatContract::test_response_message_has_correct_schema` |
| P4.C1.8 | It reconnects after a network drop | live | `tests/test_matrix_sync_backoff.py::TestSyncErrorBackoff::test_sleeps_exponentially_then_escapes` |
| P4.C1.9 | The registry has the 4 Windy Pro tools (when Windy Pro answers) | live | `tests/test_tools.py::TestWindyToolsOnlyWhenConfigured::test_answering_pro_registers_four` |
| P4.C1.10 | The model calls tools through function calling | live | `tests/test_tool_calling_e2e.py::TestAgentLoopToolCalling::test_agent_executes_weather_tool` |

### P4.C2 Phase 2 checklist (Part 4 §14)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.C2.1 | An OpenClaw export is read | live | `tests/test_soul_import.py::TestOpenClawParser::test_parses_soul` |
| P4.C2.2 | A Hermes export is read | live | `tests/test_soul_import.py::TestHermesParser::test_parses_memory_file` |
| P4.C2.3 | A ChatGPT export is read | live | `tests/test_soul_import.py::TestChatGPTParser::test_parses_conversations` |
| P4.C2.4 | The Soul Preview comes before any write | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_preview_writes_nothing` |
| P4.C2.5 | The owner can approve, reject or review the sensitive items | planned: Windy Agent: approve and reject work; reviewing flagged items has no surface (P2.21.11) | — |
| P4.C2.6 | Imports carry the right confidence and source | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_approved_items_are_stored_as_low_confidence_imports` |
| P4.C2.7 | The presets (buddy, engineer, powerhouse, and the newer ones) apply all their values | live | `tests/strand/test_p4_map_index.py::test_p4_c2_7_each_preset_applies_all_its_values` |
| P4.C2.8 | Each slider is written and read back | live | `tests/test_control_panel.py::TestSliders::test_set_and_get` |
| P4.C2.9 | The cost estimate is broken down by slider and adds up | live | `tests/strand/test_p4_map_index.py::test_p4_c2_9_estimate_breaks_down_by_every_slider_and_adds_up` |
| P4.C2.10 | Epistemic strictness filters the facts in the prompt | live | `tests/strand/test_p2_soul_truth.py::test_p2_23_strictness_above_7_drops_inferred_and_speculative` |
| P4.C2.11 | A correction is detected as friction | live | `tests/test_control_panel.py::TestFrictionDetection::test_detects_factual_error` |
| P4.C2.12 | Failures are logged with their fault type | live | `tests/test_memory.py::TestFailures::test_log_and_retrieve` |
| P4.C2.13 | A recurring failure changes what the agent does | live | `tests/test_control_panel.py::TestFrictionHandling::test_recurring_gives_extra_warning` |

### P4.C3 Phase 3 checklist (Part 4 §14)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.C3.1 | A skill is created and stored | live | `tests/test_skills_engine.py::TestSkillManager::test_create_skill` |
| P4.C3.2 | Skills pass a syntax gate | retired: executable skills, sandbox and gates removed (#504) | — |
| P4.C3.3 | Skills pass an execution gate | retired: executable skills, sandbox and gates removed (#504) | — |
| P4.C3.4 | Skills pass a safety gate | retired: executable skills, sandbox and gates removed (#504) | — |
| P4.C3.5 | A skill is promoted after passing all gates | retired: no gates; saving a playbook promotes it (P3.31.2) (#504) | — |
| P4.C3.6 | A skill rolls back to its parent | live | `tests/test_hardening_skills.py::TestSkillRollback::test_rollback_to_parent` |
| P4.C3.7 | Each model call logs its cost with model and tokens | live | `tests/test_cost_accounting.py::test_ledger_sink_writes_success_and_failure_rows` |
| P4.C3.8 | The daily budget is enforced | live | `tests/test_agent_loop.py::TestBudgetEnforcement::test_blocks_when_over_budget` |
| P4.C3.8b | The agent warns in chat before the budget runs out | retired: the warning leaked into user replies; it goes to the log instead (#72) | — |
| P4.C3.9 | Migration 2 adds the 4 tables | live | `tests/test_intents.py::TestMigrationV2::test_intents_table_exists` |
| P4.C3.10 | A goal is detected from the conversation | live | `tests/test_intents.py::TestIntentDetection::test_detect_want` |
| P4.C3.11 | Inferred goals surface within 24 hours (when switched on) | live | `tests/test_intents.py::TestLLMIntentPath::test_llm_answer_becomes_inbox_intent` |
| P4.C3.12 | Stale goals decay | live | `tests/test_intents.py::TestDecaySchedulerRunsIntentDecay::test_scheduler_decays_and_pauses_stale_intents` |
| P4.C3.13 | Emotion is read from each message | live | `tests/test_emotion.py::TestEmotionDetection::test_detect_stress_frustrated` |
| P4.C3.14 | Sustained stress changes the agent's behaviour | retired: prompt steering by emotion removed in favour of the substrate (#304) | — |

### P4.C4 Phase 4 checklist (Part 4 §14)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.C4.1 | The UDS bridge carries JSON between brain and gateway | live | `tests/test_bridge_integration.py::TestAgentRespondViaBridge::test_full_roundtrip` |
| P4.C4.2 | The Bun gateway serves HTTP on port 3000 | planned: Windy Agent: bun tests in CI only (see P3.41.10) | — |
| P4.C4.3 | WebSocket chat works end to end | planned: Windy Agent: live-gated only (see P3.41.10) | — |
| P4.C4.4 | Decay lowers old memories' scores | live | `tests/test_phase4.py::TestCognitiveDecay::test_decay_returns_counts` |
| P4.C4.5 | Aged memories (below 0.05) are deleted | retired: decay may dim, never erase (Doctrine Law 1) (#312) | — |
| P4.C4.6 | A contradicting update is detected | live | `tests/test_phase4.py::TestConflictDetector::test_detects_conflict` |
| P4.C4.7 | The owner is asked to resolve it | live | `tests/test_memory_conflict_hold.py::TestPromptFacts::test_owner_turn_carries_the_pending_conflict` |
| P4.C4.8 | A sub-agent runs within its token budget | retired: unused; sub_agents.py and shape-shift removed (#501) | — |
| P4.C4.9 | Sub-agent depth is limited to 1 | retired: unused; sub_agents.py and shape-shift removed (#501) | — |
| P4.C4.10 | Offline, a local model answers (or the owner is told) | live | `tests/test_phase4.py::TestOfflineMode::test_offline_response_with_ollama` |

### P4.F Strand footer (Part 4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P4.F.1 | "Total codons: 59" | retired: the old plan's count (its §14 lists 67); this file's script counts are the record (#526) | — |
| P4.F.2 | Part 5 of the strand is kept | live | `tests/strand/test_p4_map_index.py::test_p4_f_part5_of_the_strand_is_kept` |

## Part 5: the beyond-blueprint amendment (Phases 6-10)

### P5.61 Shape-shift context manager (gene C6.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.61.1 | A context manager saves the sliders, reconfigures, and restores them | retired: shape-shift was unreachable (its tools lost their boot step) and its restore was a no-op; removed (#501) | — |

### P5.62 Shape-shift autonomy gating (gene C6.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.62.1 | Autonomy decides whether a shift asks, announces or stays silent | retired: shape-shift removed (#501) | — |

### P5.63 Shape-shift bias slider (gene C6.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.63.1 | shape_shift_bias picks a shift or a sub-agent, set in all 8 presets | retired: the slider had no consumer; slider and sub-agents removed (#501) | — |

### P5.C6 Phase 6 checklist

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.C6.1 | The context manager saves and restores sliders | retired: shape-shift removed (#501) | — |
| P5.C6.2 | Autonomy gating gives the right announcement | retired: shape-shift removed (#501) | — |
| P5.C6.3 | The bias slider is in the control panel and all 8 presets | retired: slider removed (#501) | — |
| P5.C6.4 | shape_shift / shape_shift_restore are registered tools | retired: shape-shift removed (#501) | — |
| P5.C6.5 | The gateway's /api/shape-shift routes work | retired: shape-shift removed (#501) | — |

### P5.71 Provider registry (gene C7.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.71.1 | The strand's built-in providers are registered (it says 11, lists 12; kimi and zai since) with no key in the table | live | `tests/strand/test_p5_providers.py::test_p5_71_the_strands_built_in_providers_are_registered` |
| P5.71.2 | A failing provider hands the call to the next one | live | `tests/test_provider_failover.py::test_failover_walks_to_next_provider_on_failure` |
| P5.71.3 | Keys are read from the environment on every call (a rotated key is picked up) | live | `tests/test_provider_failover.py::test_api_key_re_read_from_env_on_every_call` |
| P5.71.4 | One provider table shared by the gateway (TypeScript) and the brain | retired: providers.ts and every /api/providers route removed; the brain's table is the only one (#522) | — |

### P5.72 Model discovery (gene C7.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.72.1 | Live model lists per provider, cached 5 minutes, discovered in a batch | retired: gateway provider management removed (#522) | — |

### P5.73 Keys (gene C7.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.73.1 | A multi-key vault per provider (add, delete, activate, masked) | retired: one key store, the env file the runtime loads (#522) | — |
| P5.73.2 | Keys left in the old provider files move to the env file, then the files are scrubbed | live | `tests/test_provider_salvage.py::test_keys_from_every_source_move_into_the_env_file_then_files_are_scrubbed` |
| P5.73.3 | A key in the provider overrides file is never used | live | `tests/test_provider_salvage.py::test_the_overrides_file_never_supplies_a_key` |

### P5.74 OpenRouter OAuth PKCE (gene C7.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.74.1 | Connect OpenRouter by OAuth PKCE; the key is stored and models discovered | retired: gateway provider management removed (#522) | — |

### P5.75 Key validation (gene C7.5)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.75.1 | A route validates a key and reports models and balance | retired: gateway provider management removed (#522) | — |

### P5.76 Active model (gene C7.6)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.76.1 | The dashboard switches the active model | retired: the brain never read the gateway's setting; the route is removed (#522) | — |
| P5.76.2 | A model chosen with /model survives a restart | live | `tests/strand/test_p5_providers.py::test_p5_76_a_model_chosen_with_slash_model_survives_a_restart` |

### P5.77 Custom providers (gene C7.7)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.77.1 | Custom providers are added, updated and removed through REST | retired: REST-added providers never reached the brain; routes removed (#522) | — |
| P5.77.2 | A custom provider in windyfly.toml is used for its models (key from WINDY_PROVIDER_<NAME>_API_KEY) | live | `tests/strand/test_p5_providers.py::test_p5_77_a_custom_provider_from_windyfly_toml_is_used_for_its_models` |

### P5.78 Anthropic OAuth tokens (gene C7.8)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.78.1 | A token within 5 minutes of expiry is refreshed, and the new tokens are cached privately (0600) | live | `tests/strand/test_p5_providers.py::test_p5_78_a_token_near_expiry_is_refreshed_and_cached_privately` |
| P5.78.2 | A token far from expiry is used as is | live | `tests/strand/test_p5_providers.py::test_p5_78_a_token_far_from_expiry_is_used_as_is` |
| P5.78.3 | Without an OAuth token the manager steps aside and the API key is used | live | `tests/strand/test_p5_providers.py::test_p5_78_without_an_oauth_token_the_manager_steps_aside_for_the_api_key` |
| P5.78.4 | The token cache lives in the agent's state directory | planned: Windy Agent: the default (data/.anthropic_oauth.json) is relative to the working directory, and the file is chmod-ed after it is written; use the state dir and create it 0600 | — |

### P5.C7 Phase 7 checklist

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.C7.1 | All built-in providers are registered | live | `tests/strand/test_p5_providers.py::test_p5_71_the_strands_built_in_providers_are_registered` |
| P5.C7.2 | Model discovery returns a live list | retired: gateway provider management removed (#522) | — |
| P5.C7.3 | Multi-key add/delete/activate | retired: one key store (#522) | — |
| P5.C7.4 | The OpenRouter OAuth flow completes | retired: gateway provider management removed (#522) | — |
| P5.C7.5 | Key validation returns the right status | retired: gateway provider management removed (#522) | — |
| P5.C7.6 | A model switch persists across restarts (/model) | live | `tests/strand/test_p5_providers.py::test_p5_76_a_model_chosen_with_slash_model_survives_a_restart` |
| P5.C7.7 | A custom provider is usable for model calls (windyfly.toml) | live | `tests/strand/test_p5_providers.py::test_p5_77_a_custom_provider_from_windyfly_toml_is_used_for_its_models` |
| P5.C7.8 | Anthropic OAuth refreshes expiring tokens | live | `tests/strand/test_p5_providers.py::test_p5_78_a_token_near_expiry_is_refreshed_and_cached_privately` |

### P5.81-P5.85 Mission Control: remote machines (genes C8.1-C8.5)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.81.1 | A machine registry with live links and health polling | retired: no daemon in any repo ever answered on :3100; Mission Control removed (#502) | — |
| P5.82.1 | Remote machines are listed, added, read, updated and removed | retired: Mission Control removed (#502) | — |
| P5.83.1 | A terminal relay to a remote PTY | retired: Mission Control removed (#502) | — |
| P5.84.1 | Remote service restart, exec and health | retired: would have sent arbitrary commands over ws:// on a LAN; removed (#502) | — |
| P5.85.1 | Provider config is synced to remote machines | retired: would have sent plaintext keys over ws://; removed (#502) | — |

### P5.C8 Phase 8 checklist

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.C8.1 | A machine is added and its link comes up | retired: Mission Control removed (#502) | — |
| P5.C8.2 | Machine health polling returns data | retired: Mission Control removed (#502) | — |
| P5.C8.3 | The terminal relay carries PTY I/O | retired: Mission Control removed (#502) | — |
| P5.C8.4 | A service restart reaches the remote | retired: Mission Control removed (#502) | — |
| P5.C8.5 | Provider sync reaches the remote | retired: Mission Control removed (#502) | — |

### P5.91 Context gas-tank header (gene C9.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.91.1 | The header shows the agent, the time and how much context is left | live | `tests/test_context_header.py::TestContextTracker::test_format_header_green` |
| P5.91.2 | It shows again after an hour | live | `tests/test_context_header.py::TestContextTracker::test_header_after_one_hour` |
| P5.91.3 | It shows again after a 10% change | live | `tests/test_context_header.py::TestContextTracker::test_should_show_header_after_10pct_delta` |
| P5.91.4 | It does not repeat on rapid replies | live | `tests/test_context_header.py::TestContextTracker::test_no_header_rapid_fire` |
| P5.91.5 | Yellow when context runs low | live | `tests/test_context_header.py::TestContextTracker::test_format_header_yellow` |
| P5.91.6 | Red when it is nearly gone | live | `tests/test_context_header.py::TestContextTracker::test_format_header_red` |
| P5.91.7 | The header goes on the reply | live | `tests/test_context_header.py::TestMaybePrependHeader::test_prepends_on_first_call` |
| P5.91.8 | It reads against the channel's real context cap | live | `tests/test_context_header.py::TestEffectiveCapHonored::test_1M_cap_at_30K_used_shows_healthy` |

### P5.92 Relationship moments (gene C9.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.92.1 | After an emotional turn a one-line moment is saved | live | `tests/strand/test_p5_extended.py::test_p5_92_a_moment_is_saved_after_an_emotional_turn` |
| P5.92.2 | Saved moments reach the prompt | live | `tests/test_relationship_moments.py::TestRelationshipMomentsInPrompt::test_moments_appear_in_assembled_prompt` |
| P5.92.3 | Moments are listed through the bridge | live | `tests/test_gap_closure.py::TestDecayConflictsMomentsFailures::test_moments_list_with_data` |
| P5.92.4 | Moments are captured only when emotional sensitivity is 3 or more (the old warmth gate) | planned: Windy Agent: the gate in the turn is untested | — |

### P5.93 Agent journal (gene C9.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.93.1 | A journal entry is written on the 10th turn of a session | live | `tests/strand/test_p5_extended.py::test_p5_93_a_journal_entry_is_written_on_the_tenth_turn_of_a_session` |
| P5.93.2 | Sessions do not share the turn count | live | `tests/strand/test_p5_extended.py::test_p5_93_sessions_do_not_share_the_count` |
| P5.93.3 | An emotional turn is journaled at once | live | `tests/strand/test_p5_extended.py::test_p5_93_an_emotional_turn_is_journaled_at_once` |
| P5.93.4 | Journal entries are read through the bridge | live | `tests/strand/test_p5_extended.py::test_p5_93_journal_entries_are_read_through_the_bridge` |
| P5.93.5 | The gateway answers with the same key when the brain is down | planned: Windy Agent: the bridge returns "journal", the gateway's offline fallback "entries"; use one key | — |

### P5.94 Self-assessment report card (gene C9.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.94.1 | A six-score report with a grade | live | `tests/test_self_assessment.py::TestSelfAssessment::test_generates_report` |
| P5.94.2 | The report is kept as a memory | live | `tests/test_self_assessment.py::TestSelfAssessment::test_assessment_stored_as_node` |
| P5.94.3 | It runs through the bridge | live | `tests/strand/test_p5_extended.py::test_p5_94_the_report_card_runs_through_the_bridge` |
| P5.94.4 | The six scores are the strand's (memory accuracy, response quality, cost, satisfaction, skill health, uptime) | planned: Windy Agent (Hub decides): the six are retention, failure rate, soul currency, relationship depth, "consistency" (which is average cost again) and cost efficiency | — |
| P5.94.5 | It runs weekly | planned: Windy Agent: on demand only; nothing schedules it | — |

### P5.C9 Phase 9 checklist

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.C9.1 | The context header shows the time and the percentage | live | `tests/test_context_header.py::TestContextTracker::test_format_header_green` |
| P5.C9.2 | A relationship moment is saved after an emotional turn | live | `tests/strand/test_p5_extended.py::test_p5_92_a_moment_is_saved_after_an_emotional_turn` |
| P5.C9.3 | A journal entry is written on the 10th turn | live | `tests/strand/test_p5_extended.py::test_p5_93_a_journal_entry_is_written_on_the_tenth_turn_of_a_session` |
| P5.C9.4 | Self-assessment returns a six-score report | live | `tests/test_self_assessment.py::TestSelfAssessment::test_generates_report` |

### P5.101 Personality versioning API (gene C10.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.101.1 | The history comes back through the bridge | live | `tests/test_gap_closure.py::TestPersonalityVersioning::test_snapshot_then_history` |
| P5.101.2 | A snapshot is made through the bridge | live | `tests/test_gap_closure.py::TestPersonalityVersioning::test_snapshot_returns_batch_id` |
| P5.101.3 | Drift is found through the bridge | live | `tests/strand/test_p5_extended.py::test_p5_101_drift_is_found_through_the_bridge` |
| P5.101.4 | A confirmed rollback through the bridge restores the value | live | `tests/strand/test_p5_extended.py::test_p5_101_a_confirmed_rollback_through_the_bridge_restores_the_value` |

### P5.102 Skills API (gene C10.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.102.1 | Skills are created and listed through the bridge | live | `tests/test_gap_closure.py::TestSkillsManagement::test_skills_create_then_list` |
| P5.102.2 | A skill is promoted through the bridge | live | `tests/test_gap_closure.py::TestSkillsManagement::test_skills_promote` |
| P5.102.3 | A skill in a code language is refused | live | `tests/test_gap_closure.py::TestSkillsManagement::test_skills_create_rejects_code_language` |
| P5.102.4 | A skill rolls back through the bridge, and says so only when it did | planned: Windy Agent: skills.rollback answers rolled_back: true for a skill with no parent | — |
| P5.102.5 | Evaluate, golden tests and regression through the bridge | retired: executable skills removed (#504) | — |

### P5.103 System internals API (gene C10.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.103.1 | Decay runs through the bridge and reports counts | live | `tests/test_gap_closure.py::TestDecayConflictsMomentsFailures::test_decay_run_returns_counts` |
| P5.103.2 | Unresolved conflicts are listed | live | `tests/test_gap_closure.py::TestDecayConflictsMomentsFailures::test_conflicts_list_after_creation` |
| P5.103.3 | A conflict is resolved | live | `tests/test_gap_closure.py::TestDecayConflictsMomentsFailures::test_conflicts_resolve` |
| P5.103.4 | Failures are listed | live | `tests/strand/test_p5_extended.py::test_p5_103_failures_are_listed_through_the_bridge` |
| P5.103.5 | The mode is read and set | retired: modes did nothing; removed (#499) | — |
| P5.103.6 | Offline status reports the connection and the local model | live | `tests/test_gap_closure.py::TestOfflineEvents::test_offline_status` |
| P5.103.7 | Events are listed and filtered | live | `tests/strand/test_p4_phase5.py::test_p4_54_events_are_read_through_the_bridge` |

### P5.104 Offline message queue (gene C10.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.104.1 | Queued messages survive a crash (atomic write) | live | `tests/test_offline_queue_atomic.py::TestOfflineQueueAtomic::test_atomic_write_survives_crash` |
| P5.104.2 | A message is queued when the lifeboat times out | live | `tests/test_lifeboat_actually_queues.py::TestLifeboatTimeoutQueuesMessage::test_lifeboat_timeout_actually_queues` |
| P5.104.3 | Back online, nothing queued goes through the model | live | `tests/test_offline_queue_no_replay.py::test_nothing_goes_through_the_model` |
| P5.104.4 | Back online, the queue is replayed | retired: a replay drove a spend auto-pause; never replay, tell the owner (#434) | — |
| P5.104.5 | The queue lives in the agent's state directory | planned: Windy Agent: the default (data/offline_queue.json) is relative to the working directory | — |

### P5.C10 Phase 10 checklist

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.C10.1 | Personality history returns the soul_history rows | live | `tests/test_gap_closure.py::TestPersonalityVersioning::test_snapshot_then_history` |
| P5.C10.2 | A personality snapshot creates a checkpoint | live | `tests/test_gap_closure.py::TestPersonalityVersioning::test_snapshot_returns_batch_id` |
| P5.C10.3 | Drift detection flags changed sliders | live | `tests/strand/test_p5_extended.py::test_p5_101_drift_is_found_through_the_bridge` |
| P5.C10.4 | The skills list returns the skills | live | `tests/test_gap_closure.py::TestSkillsManagement::test_skills_create_then_list` |
| P5.C10.5 | Skill evaluation runs the 3-gate pipeline | retired: executable skills, sandbox and gates removed (#504) | — |
| P5.C10.6 | The decay endpoint runs and returns counts | live | `tests/test_gap_closure.py::TestDecayConflictsMomentsFailures::test_decay_run_returns_counts` |
| P5.C10.7 | The conflicts list returns unresolved entries | live | `tests/test_gap_closure.py::TestDecayConflictsMomentsFailures::test_conflicts_list_after_creation` |
| P5.C10.8 | Resolving a conflict updates the database | live | `tests/test_gap_closure.py::TestDecayConflictsMomentsFailures::test_conflicts_resolve` |
| P5.C10.9 | Setting the mode validates and persists it | retired: modes removed (#499) | — |
| P5.C10.10 | Offline status reports connectivity and the local model | live | `tests/test_gap_closure.py::TestOfflineEvents::test_offline_status` |
| P5.C10.11 | Events come back with 24-hour counts | live | `tests/strand/test_p4_phase5.py::test_p4_54_events_are_read_through_the_bridge` |
| P5.C10.12 | The offline queue persists messages to disk | live | `tests/test_offline_queue_atomic.py::TestOfflineQueueAtomic::test_atomic_write_survives_crash` |

### P5.CNT Updated master counts (Part 5)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.CNT.1 | Every bridge method the gateway calls exists in the brain | live | `tests/strand/test_p5_extended.py::test_p5_cnt_every_bridge_method_the_gateway_calls_exists` |
| P5.CNT.2 | 93 codons, ~52 files, 11 phases, ~61 routes, 39 bridge methods | retired: the old plan's counts; this file's script counts are the record (#526) | — |
