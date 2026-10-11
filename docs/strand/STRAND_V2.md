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
5c completed Part 2; 5d completes Part 3; 5e-5f Parts 4-5; 5g adds the new genes.

## Part 1: Foundation and Phase 0

### P1.1 Product identity (Part 1 §1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.1.1 | The product is Windy Fly: "Your AI. Your Rules. Your Ecosystem." | live | `tests/strand/test_p1_foundation.py::test_p1_1_name_and_tagline` |
| P1.1.2 | Part of the Windy ecosystem; chat on chat.windychat.ai, provisioned at hatch | live | `tests/strand/test_p1_foundation.py::test_p1_1_part_of_the_windy_ecosystem` |
| P1.1.3 | One shared Matrix bot id (@windyfly) | retired: every agent is its own @agent_<passport>, minted at hatch; the shared id was removed (#505) | — |
| P1.1.4 | Licence: proprietary | planned: Grant (via Boss): choose and state the licence; there is no LICENSE file and no licence field while the package is published to PyPI | — |
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
