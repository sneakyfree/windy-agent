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
5c1 starts Part 2 (genes 1.1-1.5); 5c2-5f fill in the rest; 5g adds the new genes.

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
