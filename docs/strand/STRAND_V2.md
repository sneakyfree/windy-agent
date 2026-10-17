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
| P1.02.1 | One default model: windyfly.config.DEFAULT_MODEL; windyfly.toml names the same one, .env.example sets none, and no code names its own fallback | live | `tests/strand/test_p1_foundation.py::test_p1_02_there_is_one_default_model` |
| P1.02.2 | [agent] temperature / max_response_tokens / max_context_tokens are honoured | retired: never read (the creativity and response-length sliders and the model's window decide); the settings are removed from windyfly.toml, the setup templates, the config defaults and the dashboard Settings page (#594) | — |
| P1.02.3 | [memory] db_path and max_nodes_per_context come from the file | live | `tests/strand/test_p1_foundation.py::test_p1_02_memory_settings_come_from_the_file` |
| P1.02.4 | [memory] max_episodes_per_context is honoured | retired: never read (the context slider decides); removed from the config files and from auto-repair's tunable settings (#594) | — |
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
| P1.03.4 | Each migration is applied atomically: one run is one transaction, statement by statement (no executescript), and a failure leaves nothing behind | live | `tests/strand/test_p1_memory.py::test_p1_03_a_failed_migration_run_leaves_nothing_behind` |
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
| P1.03.21 | MEDIUM writes run together in batches of up to 50 for fairness (each write commits on its own), and one failing write never stops the rest of its batch | live | `tests/strand/test_step8_restated.py::test_p1_03_21_medium_writes_run_in_batches_and_one_failure_never_stops_the_rest` |
| P1.03.22 | Timestamps keep parsing on future Python versions (explicit adapters and converters, not the defaults deprecated in 3.12) | live | `tests/strand/test_p1_memory.py::test_p1_03_timestamps_parse_without_the_deprecated_defaults` |

### P1.04 Personality engine (gene 0.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.04.1 | SOUL.md is the personality and reaches the system message | live | `tests/strand/test_p1_loop.py::test_p1_04_soul_md_reaches_the_system_message` |
| P1.04.2 | A missing SOUL.md falls back to a default | live | `tests/test_personality.py::TestLoadSoul::test_returns_default_on_missing` |
| P1.04.3 | Humour below 3 strips the witty lines | live | `tests/test_personality.py::TestBuildPersonalityBlock::test_low_humor_strips_witty` |
| P1.04.4 | Formality above 7 adds the formal instruction | live | `tests/test_personality.py::TestBuildPersonalityBlock::test_high_formality_adds_instruction` |
| P1.04.5 | Sliders shape the personality block (e.g. autonomy) | live | `tests/test_autonomy_slider_wired.py::test_high_autonomy_emits_action_bias_modifier` |
| P1.04.6 | In raw mode (the default) the owner's SOUL.md is passed whole, never cut; with sliders on, the slider text added to it is at most 800 characters | live | `tests/strand/test_step8_restated.py::test_p1_04_6_the_soul_is_whole_in_raw_mode_and_the_slider_text_is_bounded` |
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
| P1.06.5 | The whole prompt stays under the effective limit (the model's window or the owner's /memory cap, less room for the answer): the oldest history goes first, never the soul or the current message | live | `tests/strand/test_p1_loop.py::test_p1_06_the_whole_prompt_stays_under_the_effective_limit` |
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
| P2.11.13 | The sender's windy_lang is read from the incoming event, and the agent answers in that language (the owner's messages only; a well-formed language-script-region code only, named in the prompt only for a language Windy serves; the last known language stays when a message has none) | live | `tests/test_sender_language.py::test_the_matrix_channel_notes_the_tag_from_the_incoming_event` |
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
| P2.13.4 | Kept replies go out on the next successful sync (not only after a reconnect), at most once every 30 s | live | `tests/strand/test_p2_matrix.py::test_p2_13_kept_replies_go_out_on_the_next_good_sync` |

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
| P2.21.3 | Hermes's SQLite store is read | retired: the reader guessed table names and no real Hermes database was ever provided; soul-import intake never takes a database file, so Hermes imports through its SOUL.md and MEMORY.md only (#594) | — |
| P2.21.4 | A ChatGPT export is read for preferences and topics | live | `tests/test_soul_import.py::TestChatGPTParser::test_parses_conversations` |
| P2.21.5 | ChatGPT's communication style is extracted: humour, formality and verbosity from plain counts over the owner's own messages (never the assistant's), no model call | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_chatgpt_style_comes_from_the_owners_own_messages_only` |
| P2.21.6 | The source is detected automatically | live | `tests/test_soul_import.py::TestSourceDetection::test_detects_openclaw` |
| P2.21.7 | The Soul Preview shows what would be imported | live | `tests/test_soul_import.py::TestSoulPreview::test_format_preview` |
| P2.21.8 | Without approval nothing is written | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_preview_writes_nothing` |
| P2.21.9 | Approved items are stored as low-confidence imports (0.5, imported_<source>, inferred) | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_approved_items_are_stored_as_low_confidence_imports` |
| P2.21.10 | Sensitive items are stored at 0.3, flagged for review; code skills are skipped, prose skills become unpromoted playbooks | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_sensitive_items_are_flagged_and_code_skills_skipped` |
| P2.21.11 | The owner can review flagged items: /soul review lists them by number and kind, /soul review <n> opens one, /soul keep <n> lets the agent use it | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_the_owner_reviews_flagged_items_one_at_a_time_and_keeps_one` |
| P2.21.12 | An imported tone (humour, formality, verbosity) can be applied to the sliders: /soul apply shows the changes, /soul yes sets them, /soul undo puts them back | live | `tests/strand/test_p2_soul_truth.py::test_p2_21_an_imported_tone_is_applied_after_a_yes_and_can_be_undone` |
| P2.21.14 | Imported personality traits are added to the SOUL.md the agent loads, shown first, after a snapshot, with /soul undo | planned: Windy Agent: waits on Boss's ruling on which SOUL.md Zero loads | — |
| P2.21.13 | The owner can start an import without a terminal: /soul import, send the export in chat, see the preview, /soul approve or /soul reject (the file is wiped either way); or upload it on the dashboard's Personality page (up to 90 MB, a one-time token, never a host path) | live | `tests/test_soul_import_flow.py::test_approve_keeps_the_import_and_wipes_the_file` |

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
| P2.22.11 | /preset says when raw mode means the preset won't change the tone | live | `tests/test_truthful_commands.py::test_preset_in_raw_mode_says_which_sliders_are_not_used` |

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
| P2.24.3 | Common corrections ("That is wrong", "Incorrect, it was ...", "Actually ...") are detected | live | `tests/strand/test_p2_soul_truth.py::test_p2_24_common_corrections_are_detected` |
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
| P3.31.10 | Success and failure uses are counted separately: a skill the agent used is judged on the owner's next message (friction = failure, otherwise success) | live | `tests/test_skill_engine.py::TestSkillVerdict::test_the_turn_after_a_skill_was_used_judges_it` |

### P3.32 Cost ledger and budget (gene 3.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P3.32.1 | Every model call writes one cost row (failed calls too) | live | `tests/test_cost_accounting.py::test_ledger_sink_writes_success_and_failure_rows` |
| P3.32.2 | Prices come from a rate card; an unknown model costs nothing and warns once | live | `tests/test_cost_accounting.py::test_unknown_model_has_no_cost_and_warns_once` |
| P3.32.3 | Monthly spend is summed | live | `tests/test_cost_tracker.py::TestCostTracker::test_monthly_spend_with_entries` |
| P3.32.4 | The budget check allows spending under budget | live | `tests/test_cost_tracker.py::TestBudgetCheck::test_within_budget` |
| P3.32.5 | Over budget, the turn is blocked before the model is called | live | `tests/test_agent_loop.py::TestBudgetEnforcement::test_blocks_when_over_budget` |
| P3.32.6 | Over budget, the owner can choose to go on anyway: /budget continue allows one more day's budget for the rest of the UTC day, once; it resets by itself and never lifts the monthly budget | live | `tests/test_budget_continue.py::test_over_budget_the_owner_can_continue_today_once` |
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
| P3.33.11 | Edges link memories into a graph | retired: nothing writes edges and no graph feature is planned; the table stays only so /forget removes any edge rows with a node (#594) | — |

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
| P3.41.8 | Bridge memory search also searches past conversations (none in guest mode; a 300-character preview each) | live | `tests/test_bridge_integration.py::TestMemorySearchCoversConversations::test_memory_search_also_searches_past_conversations` |
| P3.41.9 | The gateway's health route reports whether the brain is connected | live | `tests/test_gateway_smoke.py::TestHealthRoute::test_bridge_reports_connected_state` |
| P3.41.10 | Gateway HTTP and WebSocket chat are proven end to end without a live stack (gateway/tests/e2e-bridge.test.ts: the real gateway against a stand-in brain socket, run by CI) | live | `tests/strand/test_p4_map_index.py::test_p4_11_the_gateway_is_proven_end_to_end_in_ci` |

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
| P4.11.2 | The gateway serves the web UI (localhost:3000 by default) | live | `tests/strand/test_p4_map_index.py::test_p4_11_the_gateway_is_proven_end_to_end_in_ci` |
| P4.11.3 | The gateway's WebSocket chat works end to end | live | `tests/strand/test_p4_map_index.py::test_p4_11_the_gateway_is_proven_end_to_end_in_ci` |
| P4.11.4 | Sliders are set through the gateway's REST route | live | `tests/test_gateway_smoke.py::TestSlidersSetRoute::test_set_valid_slider` |
| P4.11.5 | Spend is read through the gateway's REST route | live | `tests/test_gateway_smoke.py::TestCostDailyRoute::test_reflects_logged_cost` |
| P4.11.6 | The gateway talks to the brain over the UDS bridge (the brain's own process, not the Matrix bot's) | live | `tests/test_bridge_integration.py::TestAgentRespondViaBridge::test_full_roundtrip` |
| P4.11.7 | The homeserver is Windy Chat (chat.windychat.ai) | live | `tests/test_contract_matrix.py::TestBotIdentityContract::test_default_homeserver` |
| P4.11.8 | One shared @windyfly bot user | retired: every agent is its own @agent_<passport>, minted at hatch; the shared id was removed (#505) | — |
| P4.11.9 | Rooms are end-to-end encrypted | retired: never active (no olm); dead E2E code removed, one honest line for encrypted events (#505) | — |
| P4.11.10 | Presence shows the agent online | live | `tests/strand/test_p2_matrix.py::test_p2_12_presence_online_at_start` |
| P4.11.11 | Typing shows while the agent works | live | `tests/strand/test_p2_matrix.py::test_p2_12_typing_on_before_the_turn_and_off_after_the_reply` |
| P4.11.12 | Read receipts: the agent marks a message read when it takes it up, before it starts typing | live | `tests/strand/test_p2_matrix.py::test_p4_11_the_agent_marks_a_message_read_before_it_starts_typing` |
| P4.11.13 | Onboarding (K2) gives the agent its chat account, using its passport | live | `tests/test_chat_session.py::TestFetchAgentChatSession::test_sends_ept_bearer_and_returns_session` |
| P4.11.14 | The directory (K3) lists the agent as discoverable | retired: agents stay private (Windy Chat 10-17: the agent directory was dropped in windy-chat #385, Grant's decision; Synapse runs with search_all_users false, so an agent is findable only by people already in a room with it) (#594) | — |
| P4.11.15 | Push (K6) delivers the agent's replies to the phone: replies go out as m.text, which Windy Chat's push covers (m.notice is never pushed); only agent-teamwork.v1 backstage (agent-to-agent requests and results, the stop line) is m.notice, by design; Windy Chat's prod proof BOARD 10-17T15:19Z (K6.m5) | live | `tests/strand/test_step8_restated.py::test_p4_11_15_replies_go_out_as_m_text_so_they_are_pushed` |
| P4.11.16 | The agent's conversations are backed up (its own encrypted database backup to Windy Cloud, in place of K8), and restored only after a yes, with a way back | live | `tests/test_backup_restore.py::TestListAndRestoreRoundTrip::test_backup_list_restore` |
| P4.11.17 | Translation history from the Windy Pro account server | live | `tests/test_contract_pro_api.py::TestTranslationHistoryContract::test_correct_path_and_auth` |
| P4.11.18 | Recordings from the account server | live | `tests/test_tools.py::TestWindyApiTools::test_get_recordings` |
| P4.11.19 | Clone training data from the account server | live | `tests/test_tools.py::TestWindyApiTools::test_get_clone_status` |
| P4.11.20 | Translate text through the account server | live | `tests/test_contract_pro_api.py::TestTranslateContract::test_correct_path_method_and_body` |
| P4.11.21 | The supported-languages list from the account server (GET /api/v1/translate/languages, public, read fresh each call; codes, names and quality tiers, bounded at 256 KB / 200 languages, every field checked) | live | `tests/test_sender_language.py::test_list_languages_reads_the_public_route_without_a_credential` |
| P4.11.22 | The JWT is verified with /auth/me | retired: an agent's identity is its Eternitas passport (EPT verified against the JWKS), which every agent uses; /auth/me is not used (#594) | — |
| P4.11.23 | The account-server JWT comes from the environment, not windyfly.toml | live | `tests/test_contract_pro_api.py::TestTranslationHistoryContract::test_correct_path_and_auth` |
| P4.11.24 | Shared contracts are vendored and pinned (JSON from windy-contracts) | live | `tests/test_shared_commands.py::test_the_vendored_contract_is_the_pinned_one` |
| P4.11.25 | Voice notes are transcribed in the agent (its own faster-whisper, optional extra) and answered | live | `tests/test_voice_handler.py::test_voice_handler_dispatches_transcript_to_agent` |
| P4.11.26 | The agent can query and tune WindySense | planned: Windy Agent: no integration; Hub decides whether it is still wanted | — |
| P4.11.27 | The phone and the agent share Matrix under a pinned contract (phone tools off by default) | live | `tests/test_phone_tools.py::test_dark_by_default` |
| P4.11.28 | Files are stored on Windy Cloud (POST /api/v1/storage/upload; only files in the allowed folders or whose full path the owner wrote, never keys, env files, the agent's data, browser profiles, keychains or hidden folders; 5 files / 200 MB per message, none outside a turn) | live | `tests/test_cloud_tool.py::test_upload_list_download_follow_clouds_contract` |
| P4.11.29 | Files on Windy Cloud are listed (GET /api/v1/storage/files; names framed as data, control characters stripped; other apps' files taint the turn) | live | `tests/test_cloud_tool.py::test_upload_list_download_follow_clouds_contract` |
| P4.11.30 | Files on Windy Cloud are retrieved (GET /api/v1/storage/files/{file_id} into the agent's downloads folder, 0600, never overwriting, capped) | live | `tests/test_cloud_tool.py::test_the_servers_name_is_cleaned_and_kept_inside_the_folder` |

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
| P4.13.2 | The version line is clear: the CHANGELOG explains that the v0.4.0/v0.5.0/v1.0.0 tags were phase markers, not the package releases of the same numbers | live | `tests/strand/test_p4_map_index.py::test_p4_13_the_changelog_explains_the_version_numbers` |

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
| P4.C2.5 | The owner can approve, reject or review the sensitive items; /soul drop, then /soul yes, deletes flagged items leaving no readable copy | live | `tests/strand/test_p2_soul_truth.py::test_p4_c2_the_owner_drops_flagged_items_after_a_yes_leaving_nothing_readable` |
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
| P4.C4.2 | The Bun gateway serves HTTP on port 3000 (by default) | live | `tests/strand/test_p4_map_index.py::test_p4_11_the_gateway_is_proven_end_to_end_in_ci` |
| P4.C4.3 | WebSocket chat works end to end | live | `tests/strand/test_p4_map_index.py::test_p4_11_the_gateway_is_proven_end_to_end_in_ci` |
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
| P5.78.4 | The token cache lives in the agent's state directory | live | `tests/test_state_dir_private_files.py::test_the_oauth_cache_lives_in_the_state_dir_and_is_0600` |

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
| P5.92.4 | Moments are captured only when emotional sensitivity is 3 or more (the old warmth gate) | live | `tests/strand/test_p5_extended.py::test_p5_92_moments_are_captured_only_at_emotional_sensitivity_3_or_more` |

### P5.93 Agent journal (gene C9.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.93.1 | A journal entry is written on the 10th turn of a session | live | `tests/strand/test_p5_extended.py::test_p5_93_a_journal_entry_is_written_on_the_tenth_turn_of_a_session` |
| P5.93.2 | Sessions do not share the turn count | live | `tests/strand/test_p5_extended.py::test_p5_93_sessions_do_not_share_the_count` |
| P5.93.3 | An emotional turn is journaled at once | live | `tests/strand/test_p5_extended.py::test_p5_93_an_emotional_turn_is_journaled_at_once` |
| P5.93.4 | Journal entries are read through the bridge | live | `tests/strand/test_p5_extended.py::test_p5_93_journal_entries_are_read_through_the_bridge` |
| P5.93.5 | The gateway answers with the same key when the brain is down | live | `tests/test_journal_key_and_dead_branch.py::test_the_journal_has_one_key_whether_the_brain_is_up_or_down` |

### P5.94 Self-assessment report card (gene C9.4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P5.94.1 | A six-score report with a grade | live | `tests/test_self_assessment.py::TestSelfAssessment::test_generates_report` |
| P5.94.2 | The report is kept as a memory | live | `tests/test_self_assessment.py::TestSelfAssessment::test_assessment_stored_as_node` |
| P5.94.3 | It runs through the bridge | live | `tests/strand/test_p5_extended.py::test_p5_94_the_report_card_runs_through_the_bridge` |
| P5.94.4 | The six scores are the strand's (memory accuracy, response quality, cost, satisfaction, skill health, uptime) | retired: the report card keeps its own six (retention, failure rate, soul currency, relationship depth, consistency, cost efficiency); the six scores in src/windyfly/agent/self_assessment.py are the record (#542) | — |
| P5.94.5 | It runs weekly | retired: the report card runs on demand (assessment.run); nothing schedules it and none is wanted (#546) | — |

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
| P5.102.4 | A skill rolls back through the bridge, and says so only when it did | live | `tests/test_skills_rollback_honest.py::test_a_skill_with_no_earlier_version_is_not_rolled_back` |
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
| P5.104.5 | The queue lives in the agent's state directory | live | `tests/test_state_dir_private_files.py::test_the_offline_queue_lives_in_the_state_dir_and_is_0600` |

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

## New genes (Wave 5g): what the original strand did not have

A row about another lane's service counts only after that lane confirms it in writing (Hub 10-11);
until then it stays `planned` with that lane as owner. Rows marked "(ours)" test only Windy Fly's
own behaviour.

### N1 Windy Mind: the agent's brain path

| Codon | Promise | Status | Test |
|---|---|---|---|
| N1.1 | An agent with a passport sends every model call to Windy Mind first, and uses Mind's answer without calling any other provider | live | `tests/test_mind_broker_routing.py::test_passport_present_calls_mind_first` |
| N1.2 | An agent with a passport never falls back to a provider key of its own when Mind fails | live | `tests/test_mind_broker_routing.py::test_mind_500_with_a_passport_never_calls_a_provider_key` |
| N1.3 | When Mind has a real failure (mind_unavailable, other 5xx, timeouts), a passport agent uses its small local model (the lifeboat) and logs that it did | live | `tests/test_mind_broker_routing.py::test_passport_agent_falls_to_the_local_lifeboat_and_says_so` |
| N1.4 | A standalone install with no passport skips Mind and uses its own keys | live | `tests/test_mind_broker_routing.py::test_no_passport_no_mind_call` |
| N1.5 | A chat call goes to Mind as a short-lived agent token (Bearer) with a DPoP proof for that request | live | `tests/test_mind_auth.py::TestWiredIntoTheBrokerPath::test_chat_goes_out_as_ept_agent_with_dpop` |
| N1.6 | If the short-lived token can't be minted, the agent uses its long-lived passport token, so it never goes silent | live | `tests/test_mind_auth.py::TestHeaders::test_mint_failure_falls_back_to_the_legacy_ept` |
| N1.7 | When the owner switches the agent off in Mind (403 grant_off), it stops at once: one request, no retry, no other model, no lifeboat | live | `tests/test_owner_pause.py::test_grant_off_raises_and_never_falls_back` |
| N1.8 | When Mind is busy (503 mind_unavailable with Retry-After), the agent retries once and does not mark Mind as broken | live | `tests/test_mind_brain_path.py::TestBrokerResilience::test_busy_503_retries_once_then_answers` |
| N1.9 | When Mind fails, the agent cools Mind down and makes no call to it during the cool-down | live | `tests/test_mind_brain_path.py::TestBrokerResilience::test_http_failure_records_cooldown_and_skips_next` |
| N1.10 | When the owner has switched off the model asked for (403 model_disabled), the agent asks once more with no model and lets Mind choose | live | `tests/test_owner_pause.py::test_model_disabled_retries_once_with_minds_choice` |
| N1.11 | With the model set to "auto", the agent names no model and the owner's Mind settings decide | live | `tests/test_auto_model.py::test_mind_gets_no_model_when_auto` |
| N1.12 | A model switch through Mind sends the exact model id, clears the per-channel pins and caches the new choice | live | `tests/test_mind_self.py::TestSwitching::test_success_puts_the_exact_id_and_clears_channel_pins` |
| N1.13 | Words that are not an exact model id (such as "groq") never switch the model; the agent lists the allowed choices | live | `tests/test_mind_self.py::TestSwitching::test_words_that_are_not_an_exact_id_do_not_switch_and_list_the_choices` |
| N1.14 | (ours) Only the owner can switch the model; other people can only read the status | live | `tests/test_mind_self.py::TestCapabilities::test_bands_reads_user_changes_owner` |
| N1.15 | The Mind address comes from MIND_API_URL, then the older MIND_BASE_URL, then the production default | live | `tests/test_mind_url_resolution.py::test_runtime_claim_follows_the_canonical_override` |
| N1.16 | The Mind client kept in this repo requires the same keys as Mind's published contract | live | `tests/test_mind_client_vendored.py::test_required_keys_match_contract` |
| N1.17 | The agent's usage view from Mind shows no cost | live | `tests/test_mind_self.py::TestReading::test_usage_has_no_money` |
| N1.18 | When Mind is busy, the person hears "I could not reach my AI", with no blame on credentials and no mention of Mind | live | `tests/test_anthropic_oauth_env_race.py::test_banner_names_busy_mind_not_a_missing_credential` |
| N1.19 | When Mind says the owner's own provider key was refused (502 connection, the provider named in x-mind-connection), that is recorded and Mind is not cooled down | live | `tests/strand/test_n1_n3_mind_eternitas_teams.py::test_n1_a_refused_own_key_named_in_x_mind_connection_does_not_cool_mind_down` |
| N1.20 | (ours) The Mind helper path is off unless WINDY_MIND_HELPER=1 | live | `tests/strand/test_n1_n3_mind_eternitas_teams.py::test_n1_the_mind_helper_is_off_unless_switched_on` |
| N1.21 | (ours) A read that Mind refuses with the short-lived token is retried once with the long-lived passport token | live | `tests/strand/test_n1_n3_mind_eternitas_teams.py::test_n1_a_refused_read_retries_once_with_the_long_lived_token` |
| N1.22 | (ours) A refused picture (vision_off, vision_consent_required) does not cool Mind down | live | `tests/strand/test_n1_n3_mind_eternitas_teams.py::test_n1_a_refused_picture_does_not_cool_mind_down` |
| N1.23 | When Mind cannot read the owner's pause switch (503 grant_unavailable), the agent waits Retry-After (at most 30 s), tries once, then holds off: no lifeboat, no provider key, no cool-down | live | `tests/test_mind_grant_unavailable.py::test_unreadable_switch_waits_retries_once_then_stops_without_the_lifeboat` |
| N1.24 | Behind an unreadable pause switch the loop never resurrects to the local model; a person hears one pinned line per 10 minutes and another agent hears nothing | live | `tests/test_mind_grant_unavailable.py::test_the_loop_never_resurrects_behind_an_unreadable_switch` |
| N1.25 | An unknown model (404 model_not_available) or one not offered to this agent (403 model_not_offered) is asked again once with no model | live | `tests/test_mind_caller_refusals.py::test_pick_another_model_codes_retry_once_with_no_model` |
| N1.26 | Refusals about the caller (grant_off, model_*, vision_*, connection, not_allowed_for_agent, any 4xx, 429) never cool Mind down | live | `tests/test_mind_caller_refusals.py::test_4xx_refusal_does_not_cool_mind_down` |

### N2 Eternitas: passports, hatching and trust

| Codon | Promise | Status | Test |
|---|---|---|---|
| N2.1 | Every agent gets its passport at hatch, on this machine | retired: hatching moved to the one hub ceremony; the passport is made there and no local passport token is written (#386, #430) | — |
| N2.2 | (ours) `windy go` asks the hub for a hatch ceremony and creates nothing on this machine until it finishes | live | `tests/test_hub_hatch.py::test_ticket_request_matches_the_contract_and_mints_nothing_locally` |
| N2.3 | (ours) A finished ceremony leaves the agent in the cloud; this machine keeps only a private (0600) record and writes no passport token | live | `tests/test_hub_hatch.py::test_complete_stays_in_the_cloud` |
| N2.4 | (ours) The ceremony runs once: a second `windy go` says the agent is already in the cloud and does not call the hub | live | `tests/test_hub_hatch.py::test_second_go_says_you_already_have_it_in_the_cloud` |
| N2.5 | (ours) If the account already has an agent, Windy Fly never takes it over without asking | live | `tests/test_hub_hatch.py::test_owner_has_agent_never_adopts_silently` |
| N2.6 | (ours) A failed ceremony is reported plainly and the next try starts with a fresh key | live | `tests/test_hub_hatch.py::test_dead_tickets_are_said_plainly_and_rotate_the_key[failed-The hatch failed]` |
| N2.7 | (ours) Rewriting the config keeps the agent's passport and token, so it never gets a second passport | live | `tests/test_hatch_once.py::test_rewriting_config_keeps_the_identity` |
| N2.8 | (ours) The file holding the passport token is readable only by its owner | live | `tests/test_hatch_once.py::test_env_file_is_private` |
| N2.9 | A fresh install uses the real Eternitas issuer (https://api.eternitas.ai) unless told otherwise | live | `tests/test_eternitas_issuer_default.py::test_default_is_the_production_issuer` |
| N2.10 | (ours) The hatch summary labels a mock passport as a local mock, never as a real passport | live | `tests/test_honest_hatch_summary.py::test_mock_issuer_passport_is_labelled` |
| N2.11 | (ours) A renewed passport token is written to the environment and the file atomically, keeping the other lines and one backup | live | `tests/test_ept_refresh.py::test_updates_env_and_file_atomically` |
| N2.12 | A refused renewal (401 bad credentials, 403 suspended or revoked) never crashes the agent | live | `tests/test_ept_refresh.py::test_refusals_do_not_raise` |
| N2.13 | The agent signs in to Windy Chat as its own @agent_<passport> account, made with its passport | live | `tests/test_chat_session.py::TestMatrixBotOneSoulLogin::test_login_adopts_minted_identity_and_device` |
| N2.14 | (ours) Revoking the passport also disables the local token (that one line is commented out, with a backup) | live | `tests/test_deregister.py::test_mark_local_revoked_comments_out_only_the_ept_line_atomically` |
| N2.15 | A revoked passport blocks a trust-gated action (send_email) | live | `tests/contract/test_trust_check.py::TestGate::test_revoked_status_blocks_everything` |
| N2.16 | A known revocation still blocks when Eternitas cannot be reached | live | `tests/contract/test_trust_check.py::TestRevocationSurvivesOutage::test_known_revocation_denies_when_service_unreachable[revoked]` |
| N2.17 | A revocation webhook clears the cached trust verdict | live | `tests/contract/test_trust_check.py::TestWebhookInvalidation::test_revoked_webhook_invalidates_cache` |
| N2.18 | A trust webhook whose body was changed fails its HMAC check | live | `tests/contract/test_trust_webhook_verify.py::TestHmac::test_rejects_tampered_body` |
| N2.19 | (ours) A trust webhook with a bad signature is refused through the bridge and changes nothing | live | `tests/strand/test_n1_n3_mind_eternitas_teams.py::test_n2_a_trust_webhook_with_a_bad_signature_is_refused_through_the_bridge` |
| N2.20 | (ours) The dashboard's trust banner says "unknown" until a verdict is cached, then shows the cached band | live | `tests/strand/test_n1_n3_mind_eternitas_teams.py::test_n2_the_trust_banner_says_unknown_until_a_verdict_is_cached` |
| N2.21 | The hatch summary says when a passport is suspended or revoked | retired: the line read _WINDYFLY_PASSPORT_DEAD, which nothing ever set; the dead branch is removed (#547) | — |
| N2.22 | A refused renewal tells the person what will actually help (a revoked or suspended passport is not fixed by signing in again) | live | `tests/test_ept_refresh.py::test_a_revoked_refresh_does_not_say_sign_in` |

### N3 Teams: the owner's agents in one room

| Codon | Promise | Status | Test |
|---|---|---|---|
| N3.1 | Two agents stop replying to each other after 6 replies in 10 minutes, with no model calls after that, and the owner is told once | live | `tests/test_turn_budget.py::test_two_agents_stop_at_the_budget_and_the_model_is_not_called` |
| N3.2 | When a person speaks in the room, the agents may carry on | live | `tests/test_turn_budget.py::test_a_person_in_the_room_lets_the_agents_carry_on` |
| N3.3 | People are never limited by the agent budget | live | `tests/test_turn_budget.py::test_humans_are_never_budgeted` |
| N3.4 | The owner's notice never shows raw room or agent ids | live | `tests/test_turn_budget.py::test_agent_names_are_safe_too` |
| N3.5 | Messaging a sibling agent makes a pair room where the owner alone is in charge, then posts | live | `tests/test_teams_tools.py::test_claim_creates_steps_down_invites_confirms_then_posts` |
| N3.6 | A retried room setup never makes a second room while the claim lives (60 s); after 410 claim_expired a new claim may make a new room | live | `tests/test_teams_tools.py::test_confirm_5xx_then_retry_confirms_the_same_room_never_a_second` |
| N3.7 | The owner's other agents are trusted; unknown agents are sandboxed | live | `tests/test_teams_tools.py::test_siblings_are_trusted_strangers_are_sandbox` |
| N3.8 | The team tools are off without the flag and need the trusted band | live | `tests/test_teams_tools.py::test_tools_are_trusted_band_and_dark_without_the_flag` |
| N3.9 | When Windy Chat refuses, the agent passes the refusal back as it is and posts nothing | live | `tests/test_teams_tools.py::test_chat_refusals_pass_through_in_the_contract_shape` |
| N3.10 | A "[no reply]" answer to another agent posts nothing | live | `tests/test_silence_token.py::test_agent_sender_silence_posts_nothing` |
| N3.11 | (ours) The list of the owner's other agents goes stale after 5 minutes | live | `tests/strand/test_n1_n3_mind_eternitas_teams.py::test_n3_the_sibling_list_goes_stale_after_five_minutes` |
| N3.12 | (ours) A failed refresh of that list keeps the last good list | live | `tests/strand/test_n1_n3_mind_eternitas_teams.py::test_n3_a_failed_sibling_refresh_keeps_the_last_good_list` |
| N3.13 | The owner's stop word in a room ("stop", "please stop", "enough", ...) silences the agent's replies to other agents there, gets no reply and no model call, and the owner's next normal message resumes (agent-teamwork.v1 P5; Grant 10-17) | live | `tests/test_owner_stop.py::test_the_owners_stop_silences_agent_replies_without_a_model_call_or_a_reply` |
| N3.14 | A stop never resets the turn budget (it used to: any human message reset it, so "stop" bought six more replies) | live | `tests/test_owner_stop.py::test_a_stop_never_resets_the_turn_budget` |
| N3.15 | The phone's Stop (an ai.windy.stop event) silences the same way, from the owner only; it works in guest mode too | live | `tests/test_owner_stop.py::test_the_stop_event_from_the_owner_silences_and_from_anyone_else_does_nothing` |
| N3.16 | Another agent's message is a turn only when it addresses this agent (a mention, ai.windy.task.to, or the two of them alone in the room) | live | `tests/test_owner_stop.py::test_another_agents_message_is_a_turn_only_when_it_addresses_this_agent` |
| N3.17 | A stop survives a restart (kept in a small state file, room ids only), and a malformed agent message is dropped, never raised out of the Matrix callback | live | `tests/test_owner_stop.py::test_a_stop_survives_a_restart` |
| N3.18 | (agent-teamwork.v1, dark WINDY_TEAMWORK=1) A typed request addressed to this agent is one turn, answered by exactly one result (m.notice, to the requester, same thread); a repeat of that task_id is no turn | live | `tests/test_teamwork.py::test_the_specialist_answers_a_request_once_as_a_result_with_no_outward_band` |
| N3.19 | The chief of staff asks with ask_agent (OWNER only); when every result is in it takes ONE turn and gives the owner ONE m.text that closes the task; its "asked them" reply is backstage | live | `tests/test_teamwork.py::test_the_chief_asks_two_agents_answers_backstage_then_gives_the_owner_one_answer` |
| N3.20 | Budgets: every agent message on a task's lineage counts; out of turns or seconds, no turn, and the owner gets one plain line with no model call | live | `tests/test_teamwork.py::test_at_the_deadline_the_owner_gets_one_plain_line_and_no_model_call` |
| N3.21 | Lineage guard in code: deeper than 3, asking back along the chain, or a repeat open (to, ask) is refused before sending, and the refusal goes to the model, never to the room | live | `tests/test_teamwork.py::test_the_guard_refuses_myself_a_repeat_and_a_closed_task` |
| N3.22 | A turn another agent triggered runs at TRUSTED at most: no outward tools, no ask_agent | live | `tests/test_teamwork.py::test_a_task_turn_is_capped_at_trusted_even_if_the_sender_resolved_higher` |
| N3.23 | With teamwork on, the contract's stop rule (a short message with a stop word) stops every agent in a team room and cancels open tasks; the smallest id confirms with one m.notice; a 1:1 room keeps the narrow rule | live | `tests/test_teamwork.py::test_the_contract_stop_in_a_team_room_cancels_tasks_and_one_agent_confirms` |
| N3.24 | ai.windy.resume and the owner's ai.windy.agents_stopped state stop and resume like the matching event; from the owner only; a resume is never answered | live | `tests/test_teamwork.py::test_resume_event_and_the_stopped_state_from_the_owner_only` |
| N3.25 | Typed task traffic is governed by budgets and the lineage guard, not the untyped turn budget (#528 backstop) | live | `tests/test_teamwork.py::test_typed_traffic_does_not_spend_the_untyped_backstop` |

### N4 Windy Mail: the agent's own mailbox

| Codon | Promise | Status | Test |
|---|---|---|---|
| N4.1 | (ours) With no mailbox, nothing is sent anywhere, even when other mail-service keys are set | live | `tests/test_mail_single_send_path.py::test_no_mailbox_never_sends_anywhere` |
| N4.2 | A send goes to Windy Mail's /api/v1/send with the agent's own passport token as the bearer | live | `tests/test_mail_send_bearer.py::test_send_request_uses_the_ept_bearer` |
| N4.3 | Everyone on a message goes on ONE request to Mail (to, cc and bcc as lists; more than one recipient needs Mail's broadcast permission) | live | `tests/test_mail_one_message.py::test_the_adapter_posts_one_request_with_the_lists` |
| N4.4 | Mail's refusals reach the owner in Mail's own words (the detail or message text Mail sends) | live | `tests/test_mail_one_message.py::test_the_adapter_returns_mails_own_sentence` |
| N4.31 | A Mail refusal wrapped as a dict in detail (every 429) reaches the owner as Mail's message, never raw JSON | live | `tests/test_mail_refusal_shapes.py::test_every_shape_mail_sends_reaches_the_owner_as_mails_sentence` |
| N4.5 | A 5xx from Mail says to check Sent before sending again | live | `tests/test_mail_one_message.py::test_a_5xx_says_check_sent_before_resending` |
| N4.6 | (ours) The inbox read reports "Mail did not answer" instead of an empty inbox | live | `tests/test_inbox_watch.py::TestListInboxSurfacesErrors::test_list_inbox_reports_timeout_instead_of_empty` |
| N4.7 | my_mailbox reads GET /api/v1/mailbox (mail/mailbox.v1) with the agent's own bearer and keeps only the contract's fields | live | `tests/test_my_mailbox.py::test_returns_the_contract_facts_with_my_own_bearer` |
| N4.8 | (ours) An agent brought home gets its mailbox address and Mail credentials installed | live | `tests/test_bring_home.py::test_happy_path_brings_the_agent_home` |
| N4.9 | (ours) The mail tool hands to, cc and bcc to the adapter in one call | live | `tests/test_mail_one_message.py::test_cc_and_bcc_travel_on_the_same_single_call` |
| N4.10 | (ours) A bad address means nothing is sent and no draft is made | live | `tests/test_mail_one_message.py::test_a_bad_address_sends_nothing` |
| N4.11 | (ours) Recipients are cleaned: names stripped, each address once across to/cc/bcc | live | `tests/test_mail_one_message.py::test_strings_lists_names_and_duplicates` |
| N4.12 | (ours) A message needs a "to" and carries at most 50 recipients | live | `tests/test_mail_one_message.py::test_needs_a_to_and_has_a_sanity_cap` |
| N4.13 | (ours) An address carrying a real newline (header injection) is refused | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n4_an_address_carrying_a_real_newline_header_is_refused` |
| N4.14 | (ours) Every outgoing mail carries the "an AI agent acting for" footer | live | `tests/test_outbound_consent.py::test_windy_mail_path_gets_the_footer` |
| N4.15 | (ours) With WINDY_SEND_CONFIRM=1 the model can only draft; nothing goes out | live | `tests/test_send_confirm.py::test_on_only_drafts_and_says_not_sent` |
| N4.16 | (ours) A stranger cannot approve a held draft | live | `tests/test_send_confirm.py::test_stranger_cannot_approve` |
| N4.17 | (ours) A trusted sibling agent cannot approve a held draft | live | `tests/test_send_confirm.py::test_a_trusted_sibling_cannot_confirm_a_held_draft` |
| N4.18 | (ours) A new recipient always asks first | live | `tests/test_send_confirm.py::test_new_recipient_always_asks` |
| N4.19 | (ours) A send with cc, bcc or several recipients always asks, even if all are known | live | `tests/test_send_confirm.py::test_cc_bcc_or_several_recipients_always_ask_even_if_all_known` |
| N4.20 | (ours) A known single recipient goes straight out | live | `tests/test_send_confirm.py::test_known_single_recipient_goes_straight_out` |
| N4.21 | (ours) One draft for the whole batch; one approval sends one message | live | `tests/test_mail_one_message.py::test_one_draft_for_the_whole_batch_and_one_approval_sends_one_message` |
| N4.22 | (ours) A held draft expires after 30 minutes | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n4_a_held_draft_expires_after_30_minutes` |
| N4.23 | (ours) The model has no tool to approve its own drafts | live | `tests/test_send_confirm.py::test_the_model_has_no_approve_tool` |
| N4.24 | (ours) send_email is refused on a turn that read untrusted mail or web content | live | `tests/test_taint_model.py::test_taint_gate_denies_external_effect_tools_when_tainted` |
| N4.25 | (ours) A trust-gate denial in the mail adapter sends nothing | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n4_a_trust_gate_denial_sends_nothing` |
| N4.26 | (ours) The inbox watch is silent on its first run, then announces only new mail | live | `tests/test_inbox_watch.py::TestJob::test_job_notifies_only_on_new_mail` |
| N4.27 | (ours) A "[TEST]" mail is marked seen but never announced | live | `tests/test_mail_one_message.py::test_test_mail_is_seen_but_never_announced` |
| N4.28 | (ours) Inbound email runs at the sender's resolved band, not as the owner | live | `tests/test_channel_inbound_band.py::test_email_inbound_threads_resolved_band` |
| N4.29 | The local mail rate limiter limits sends | retired: it never ran (no adapter had a database); Windy Mail enforces the limits and says them plainly (#546) | — |
| N4.30 | The inbox watch runs on Matrix as well as Telegram, in exactly one process per agent (Telegram when a Telegram bot is configured, else Matrix, or as WINDY_MAINTENANCE_RUNNER says) | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n4_the_inbox_watch_runs_in_exactly_one_process_and_matrix_can_run_it` |

### N5 Windy Calendar

| Codon | Promise | Status | Test |
|---|---|---|---|
| N5.1 | The calendar is off unless WINDY_CALENDAR=1 | live | `tests/test_windy_calendar.py::test_dark_by_default` |
| N5.2 | The calendar tools register only when Windy Calendar answers at boot | live | `tests/test_windy_calendar.py::test_no_tools_when_calendar_does_not_answer` |
| N5.3 | A call is POST {base}/invoke with the tool name and arguments and a DPoP proof (availability asks for 20 by default: ours) | live | `tests/test_windy_calendar.py::test_availability_wire_and_count_default` |
| N5.4 | (ours) Fly caps the slots it asks for at 50 and marks the list truncated | live | `tests/test_windy_calendar.py::test_availability_caps_slots` |
| N5.5 | A booking carries an idempotency key, and a slot already taken is reported with the next ones | live | `tests/test_windy_calendar.py::test_book_sends_idempotency_key_and_reports_slot_taken` |
| N5.6 | confirm_required waits for the owner and is never retried | live | `tests/test_windy_calendar.py::test_confirm_required_waits_and_is_not_retried` |
| N5.7 | Every refusal becomes one plain sentence, naming no internal service or token | live | `tests/test_windy_calendar.py::test_refusals_in_plain_words` |
| N5.8 | An expired token is re-minted once | live | `tests/test_windy_calendar.py::test_expired_token_remints_once` |
| N5.9 | A 401 that persists stops after one retry | live | `tests/test_windy_calendar.py::test_persistent_401_stops_after_one_retry` |
| N5.10 | The calendar token (EPT+agent for windy-calendar) is bound to the agent's key and cached | live | `tests/test_agent_token_mode_b.py::test_mode_b_happy_path_is_key_bound_and_cached` |
| N5.11 | A token bound to another key is rejected | live | `tests/test_agent_token_mode_b.py::test_token_bound_to_another_key_is_rejected` |
| N5.12 | A DPoP proof goes on every calendar request, GET included | live | `tests/test_windy_calendar.py::test_dpop_proof_rules_per_service[windy-calendar-GET-True]` |
| N5.13 | create_meeting only asks the owner, and logs counts only | live | `tests/test_windy_calendar.py::test_create_meeting_only_asks_the_owner` |
| N5.14 | (ours) When the owner's booking link is turned off, Fly says so plainly (its own sharing_off mapping) | live | `tests/test_windy_calendar.py::test_booking_link_off_is_said_plainly` |
| N5.15 | (ours) People who are not trusted see no calendar tools; create_meeting is the owner's alone | live | `tests/test_windy_calendar.py::test_non_owner_band_does_not_see_the_tools` |
| N5.16 | (ours) Only a listed agent of the same owner counts as trusted | live | `tests/test_windy_calendar.py::test_only_a_listed_same_owner_sibling_resolves_to_trusted` |
| N5.17 | (ours) With no token the reply is a plain "I couldn't sign in" | live | `tests/test_windy_calendar.py::test_no_token_and_network_errors_are_plain` |
| N5.18 | (ours) When Windy Calendar cannot be reached the reply says so plainly | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n5_a_network_error_is_said_plainly` |
| N5.19 | The appointments and block tools work end to end | live | `tests/test_windy_calendar.py::test_appointments_and_block_work_end_to_end` |

### N6 Windy Cloud: backups

| Codon | Promise | Status | Test |
|---|---|---|---|
| N6.1 | A backup restored as "latest" brings back exactly the original database | live | `tests/test_backup_restore.py::TestListAndRestoreRoundTrip::test_backup_list_restore` |
| N6.2 | Backups are encrypted (AES-256-GCM) before they leave the machine | live | `tests/test_cloud_backup_contract.py::TestCrypto::test_aes_gcm_round_trip` |
| N6.3 | A backup changed in storage is refused | live | `tests/test_cloud_backup_contract.py::TestCrypto::test_tamper_is_rejected` |
| N6.4 | With the owner's own backup secret, the key no longer comes from the passport | live | `tests/test_cloud_backup_contract.py::TestCrypto::test_user_key_is_zero_knowledge` |
| N6.5 | The passport-based backup key never changes, so old backups still open | live | `tests/test_backup_kdf_frozen.py::test_known_answers_unchanged` |
| N6.6 | Backups go to Cloud's /archive/agent as a multipart upload with encryption metadata and a checksum | live | `tests/test_cloud_backup_contract.py::TestUploadContract::test_upload_is_multipart_not_json` |
| N6.7 | A failed backup always says why | live | `tests/test_cloud_backup_contract.py::TestErrorSurfacing::test_backup_upload_returns_typed_error_on_blank_exception` |
| N6.8 | Cloud calls use the agent's own token first and the old credential only after a 401 or 403 | live | `tests/test_cloud_auth.py::test_backup_send_prefers_the_agent_token_and_falls_back_on_refusal` |
| N6.9 | With no credential at all, nothing is sent to the cloud | live | `tests/test_cloud_auth.py::test_backup_with_no_old_credential_and_no_token_sends_nothing` |
| N6.10 | A file upload is sent once with the Bearer token (Windy Cloud has no agent signed-header auth, so there is nothing to retry with); a 503 is not retried | live | `tests/test_cloud_tool.py::test_upload_answers_are_plain_and_a_503_is_not_retried` |
| N6.11 | The cloud file tools say plainly when the cloud is not configured | live | `tests/test_cloud_tool.py::test_unconfigured_is_plain` |
| N6.12 | (ours) A restore asks first, and keeps the current memory as an encrypted copy for /backup undo (24 hours); never a plaintext copy | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n6_a_restore_keeps_an_encrypted_undo_copy_never_a_plaintext_one` |
| N6.13 | (ours) A backup is due 24 hours after the last one by default | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n6_a_backup_is_due_after_24_hours_by_default` |
| N6.14 | (ours) auto_backup = false means no scheduled backup | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n6_auto_backup_off_means_never_due` |
| N6.15 | (ours) With no cloud credential the scheduled backup does not run | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n6_no_cloud_credential_means_no_scheduled_backup` |
| N6.16 | Each agent gets a free-tier storage plan from Windy Cloud at hatch | retired: an identity with no plan gets Cloud's free default and an agent's writes count against its owner's plan; the never-called allocate_cloud_quota is removed (#546) | — |
| N6.17 | No backup is uploaded without an owner-held key (WINDY_BACKUP_KEY): a key derived from the passport and name is never used for an upload, the refusal is counted in the log, and /backup tells the owner backups are off | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n6_no_backup_is_uploaded_without_an_owner_held_key` |

### N7 Phone tools (the owner's phone, through Windy Chat)

| Codon | Promise | Status | Test |
|---|---|---|---|
| N7.1 | Phone tools are off unless switched on | live | `tests/test_phone_tools.py::test_dark_by_default` |
| N7.2 | Phone tools work only in an unencrypted 1:1 room with the owner while the owner's phone says it is online | live | `tests/test_phone_tools.py::test_no_phone_unless_every_room_check_passes` |
| N7.3 | The agent follows a pinned, unchanged copy of the phone contract | live | `tests/test_phone_tools.py::test_the_vendored_contract_is_the_pinned_one` |
| N7.4 | The model sees only the tools the phone offers, and only on a turn with a live phone | live | `tests/test_phone_tools.py::test_only_the_tools_the_phone_offers` |
| N7.5 | A phone answer is read once, redacted from the chat, and becomes exactly one follow-up turn | live | `tests/test_phone_tools.py::test_a_result_is_read_once_redacted_and_becomes_one_turn` |
| N7.6 | Memory keeps only a short note of a phone answer, never the contact details | live | `tests/test_phone_tools.py::test_the_answer_turn_has_no_tools_and_stores_only_the_stub` |
| N7.7 | Answers from anyone but the owner are ignored | live | `tests/test_phone_tools.py::test_a_strangers_result_is_ignored` |
| N7.8 | If the phone never starts the request, the agent says it timed out and nothing ran | live | `tests/test_phone_tools.py::test_never_picked_up_is_a_timeout_that_ran_nothing` |
| N7.9 | On restart, leftover phone events are cleaned up and nothing else is touched | live | `tests/test_phone_tools.py::test_the_restart_sweep_redacts_only_phone_events` |
| N7.12 | When the phone started a request but no answer came within 600 s, the agent says the outcome is unknown, never that nothing was sent | live | `tests/test_phone_tools.py::test_started_but_silent_is_outcome_unknown` |
| N7.13 | A result that arrives after a timeout is redacted unread | live | `tests/test_phone_tools.py::test_a_late_result_after_a_timeout_is_redacted_unread` |
| N7.14 | While the agent is stopped at Windy Mind, a phone result turn reaches no model and gives an empty answer | live | `tests/test_phone_result_turn_silent.py::test_a_result_turn_posts_nothing_while_paused` |
| N7.15 | When Mind's stop switch cannot be read, a phone result turn gives an empty answer | live | `tests/test_phone_result_turn_silent.py::test_a_result_turn_posts_nothing_when_the_switch_is_unreadable` |
| N7.16 | A person who writes while the agent is stopped, or while the switch cannot be read, still hears the plain line | live | `tests/test_phone_result_turn_silent.py::test_a_person_still_hears_the_plain_lines` |
| N7.17 | The phone loop posts nothing to the room for an empty answer, and posts a non-empty one | live | `tests/test_phone_result_turn_silent.py::test_the_phone_loop_posts_nothing_for_an_empty_answer` |
| N7.10 | (ours) Only an owner turn is offered the phone; other turns never are | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n7_only_an_owner_turn_is_offered_the_phone` |
| N7.11 | (ours) With phone tools off, none are registered | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n7_the_phone_tools_are_not_registered_when_off` |

### N8 Windy Vault (agent side, dark)

| Codon | Promise | Status | Test |
|---|---|---|---|
| N8.1 | The agent decrypts a Vault lease exactly as Vault's frozen reference example says | live | `tests/test_vault_lease_jwe.py::test_vector_decrypts` |
| N8.2 | A lease sent to a key the agent no longer holds means "ask for one new lease", not a crash | live | `tests/test_vault_lease_jwe.py::test_unknown_kid_is_the_relase_signal` |
| N8.3 | A tampered lease is refused | live | `tests/test_vault_lease_jwe.py::test_tampered_ciphertext_and_header_fail` |
| N8.4 | Errors never reveal the lease's contents | live | `tests/test_vault_lease_jwe.py::test_wrong_private_key_fails_without_leaking` |
| N8.5 | Only the agreed encryption algorithm is accepted | live | `tests/test_vault_lease_jwe.py::test_wrong_algorithm_refused` |
| N8.6 | The lease key is created on first use, private to the owner (0600), named by Vault's own kid (a sha256 of the public key with the wv-keyid prefix), and stays the same until rotated | live | `tests/test_vault_kid_and_wire.py::test_the_kid_is_vaults_own_hash` |
| N8.7 | After a rotation the old key works for 24 hours, then is deleted | live | `tests/test_vault_lease_key.py::test_rotation_keeps_the_old_key_for_24h_then_drops_it` |
| N8.8 | No private material in what the agent stores or sends: the wire body is only {public_key: base64 of the 32 raw bytes} | live | `tests/test_vault_kid_and_wire.py::test_registration_body_is_only_the_raw_public_key` |
| N8.8b | Vault's reply must carry this key's own kid, or the registration is refused and nothing is stored | live | `tests/test_vault_kid_and_wire.py::test_the_kid_vault_returns_is_stored_and_a_mismatch_is_refused` |
| N8.8c | A lease sealed to the 16-hex kid decrypts, and a Lease.kid that differs from the header is refused | live | `tests/test_vault_kid_and_wire.py::test_a_lease_sealed_to_the_16_hex_kid_decrypts` |
| N8.9 | (ours) Nothing outside the vault package imports it: no tool, no loop call | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n8_nothing_outside_the_vault_package_imports_it` |
| N8.10 | A corrupt or unreadable lease key file is not replaced silently: one WARNING names the file and the reason (never its contents) before a new key replaces it | live | `tests/strand/test_n4_n8_mail_calendar_cloud_phone_vault.py::test_n8_a_bad_lease_key_file_is_logged_not_replaced_silently` |

### N9 Agent's Hands: the agent side of phone tools (planning, Boss 10-16)

Planned with the "Your Agent's Hands" v3 page. Full fields (contract, flag, needs, done when, size,
iPhone/Android notes) are in `docs/strand/AGENTS_HANDS_N9.md`. Gene N7 is what is live today.

| Codon | Promise | Status | Test |
|---|---|---|---|
| N9.1 | The agent offers any phone kind the pinned contract defines and the phone advertises, not a fixed pair; unknown kinds are refused | planned: Windy Agent: phone-tools v2 per-kind schemas from Chat/Mobile | — |
| N9.2 | Every phone kind has a risk class that maps to a capability tier and a confirm rule; a kind with no class is refused | planned: Windy Agent: phone-tools v2 risk field | — |
| N9.3 | Anything that sends, changes, deletes, shares or pays waits for the owner's tap on the confirm card; no tap or a "no" is "not done", never retried | planned: Windy Agent: the phone's local confirm (Mobile); approval.v1 for server-side classes (Hub) | — |
| N9.4 | Each phone request runs at most once: no resend after a timeout, a restart, an unknown outcome or a model retry | planned: Windy Agent: phone-tools v2 request_id | — |
| N9.5 | Every phone answer is read once and wiped; memory keeps a stub; files never reach the model as raw bytes | planned: Windy Agent: extend N7.5/N7.6 to every kind | — |
| N9.6 | Phone requests have per-kind limits, and a pause stops every phone request at once | planned: Windy Agent: caps + pause check | — |
| N9.7 | The no-confirm / confirm / never policy table is code and tested; the "never" column cannot be asked away | planned: Windy Agent: phone_policy.py | — |
| N9.8 | Android accessibility actions: reading runs, a send/pay/delete/confirm tap waits for the card, password fields are never read | planned: Windy Agent: after Mobile's power-mode build | — |
| N9.9 | A shell on an owner machine over Windy Mesh is FULL_MACHINE: owner only, a card per session start, start/stop counts only; agents never attach to or type into a session through the broker (v1) | planned: Windy Agent: after Windy Code's session broker and Mind S20 | — |
| N9.10 | A simulated phone drives every kind through done, declined, expired and unknown in CI; lab phones only | planned: Windy Agent: tests/phone_sim.py | — |
| N9.11 | HQ gets one counts-only event per phone request (kind, class, outcome, duration, platform) | planned: Windy Agent: Admin's event family | — |

### N10 The owner sees and deletes the agent's memory (planned, Boss 10-17)

Planned after Boss's privacy truth check (Word's v25 text). Full plan:
`~/windy-orchestra/specs/AGENT_MEMORY_DELETE_N10_2026-10-17.md`. "Deleted" means the messages, their search
index and embeddings, the summaries made from them (relationship moments, journal, turnover letter), the
correction text in failures, and nothing left readable in the database file or its WAL. Backups are a
separate gap. Each upload sends `retention_count=5` and Windy Cloud trims to the newest 5 on upload (its
second copy follows after 7 days), so the last 5 of a STOPPED agent stay with no time limit. Cloud erases
them at once (its second copy in about 8 days) only when an account purge reaches it (Cloud, BOARD
10-17).

| Codon | Promise | Status | Test |
|---|---|---|---|
| N10.1 | The owner sees conversation history by date and by search, and deletes it after a confirm, in chat (/history) and on the dashboard; owner band only, none in guest mode | live | `tests/strand/test_n10_memory_delete.py::test_n10_history_deletes_a_day_and_its_summaries_after_a_yes_and_keeps_other_days` |
| N10.2 | /forget finds facts and messages by search and deletes the ones the owner picks after a confirm, leaving no readable copy on disk | live | `tests/strand/test_n10_memory_delete.py::test_n10_forget_finds_and_deletes_after_a_yes_leaving_nothing_readable` |
| N10.3 | When the helper or the account is deleted, the agent learns it by pulling a signed status (agent-status.v1) and acts: blocked keeps memory and stops model calls and backups; purged (signed, twice 6 h apart) wipes memory on Windy-run hosts; unknown never deletes anything; Windy Cloud erases the helper's backups on the same identity.purged / tombstone | planned: Windy Agent with Windy Hub and Windy Cloud: contract first, Boss's GO and policy for owner machines | — |
