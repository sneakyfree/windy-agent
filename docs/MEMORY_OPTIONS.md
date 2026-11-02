# Per-agent memory for hatched people: options (convergence plan step 2)

**Status:** DRAFT for Boss. Options first, then each gets a Hub #9 privacy read, BEFORE any build (Boss 2026-11-02 11:57Z).
**Authors:** Windy Agent (§1: windyfly facts today) and Windy Chat (§2: roster facts; §3: the options). Roster side due Wed 2026-11-04 18:00Z.
**Fixed by Boss:** the memory does NOT go into Windy Mind (Mind keeps no one's words; Mind #540 dropped its conversations table on 11-02). Until a decision, the roster's words stay: it remembers only the recent conversation.

Each option answers the same four questions: **where it lives**, **who holds the key**, **how the owner erases it** (including copies and backups), **how it survives a runtime move** (roster <-> windyfly), plus what it costs per person and what leaves the machine.

## 1. windyfly today (Windy Agent; facts from windy-agent master 393f9811, 2026-11-02, file:line)
**Where it lives.**
- One plain SQLite file on the agent's own machine (default `data/windyfly.db`, `config.py:42`; `WINDYFLY_DB_PATH` or the config's `memory.db_path` can move it). It runs in WAL mode (`memory/database.py:880`).
- The person's words and facts about them are in:
  - `episodes`: raw messages, summaries and embeddings (`database.py:83`), plus the `episodes_fts` full-text index (`:158`);
  - `nodes` and `edges`: facts and the graph (`:64`, `:205`);
  - `soul` and `soul_history`;
  - `skills`, `failures`, `intents`, `conflicts`, `events`, `agent_actions`, `goals`;
  - the Journal, kept as `chronicle_journal` rows in `nodes` (`memory/journal.py:22-24`).

**Who holds the key.**
- **There is no key at rest.** It's plain `sqlite3`, with no SQLCipher and no `PRAGMA key` (`database.py:748-760`).
- The file gets the process umask; the code sets no mode (`database.py:852-853`).
- Protection is the machine account. On Zero, the home directory is 0700, so other users can't reach the file (checked 11-02).

**How the owner erases it.**
- `/forget <words>` searches 9 tables and deletes nothing until the owner confirms with `/forget yes` (`commands/core.py:1924`, `memory/forget.py`).
  - It's a **hard delete**: `PRAGMA secure_delete=ON` (`forget.py:96`), FTS `optimize` (`:121`), `wal_checkpoint(TRUNCATE)` (`:132`). It doesn't VACUUM.
- `/history delete <date>|all` does the same for messages (`core.py:1436`; the dashboard too, `bridge/uds_server.py:271-318`).
- Decay only dims and never deletes (`memory/decay.py:1-16`). Retention prunes only bookkeeping (events older than 30 days, duplicate soul_history rows; `retention.py:38`).
- `windy reset --soft` clears the tables; `--hard` removes `data/` (`commands/_legacy.py:2033-2075`).
- **Erase does NOT reach copies.**
  - Cloud backups keep a forgotten item until 5 newer backups replace them, and the owner is told so (`forget.py:11-12`; `core.py:1389,1882`).
  - Local export archives are untouched.

**Copies that leave the database.**
- **Cloud backup:** on by default, every 24 h, gzip + AES-256-GCM (`cloud_backup.py:1-15`, `:104-125`, `:146`, `:310-312`).
  - The key is the OWNER's own `WINDY_BACKUP_KEY`; nothing uploads without it (`:267-276`).
  - The 5 newest are kept, and Cloud enforces that too (`:44`, `:333`).
  - A restore first writes an encrypted undo copy (0600, kept 24 h; `backup_restore.py:18-23`, `:335-341`).
- **`windy export` / `/export` (owner only):** a `tar.gz` of the database snapshot plus `.env`, `windyfly.toml`, `SOUL.md`, sounds and birth certificates (`_legacy.py:2128-2170`; `core.py:2567-2616`).
  - **Unencrypted, includes the `.env` secrets, and written with the default umask** (usually 0644). A hardening fix is noted in §1a.
- **Model calls:** normal turns and the daily Journal send conversation text to the model through Windy Mind (`agent/maintenance.py:159-168`, `:250-261`; `agent/models.py:425-437`). Mind keeps no one's words (Mind #540).
- Embeddings are computed locally (sentence-transformers, optional; `memory/embeddings.py:1-27`).

**How it survives a runtime move.**
- In: `windy bring-home` imports memory as `ndjson-v1` (gzip + base64, sha256-checked) into episodes (`bring_home.py:17-18`, `:47`, `:335`). Soul import reads ChatGPT, Hermes and OpenClaw exports, previews them, and writes only on the owner's yes (`soul_import/flow.py:1-9`).
- Out: only the whole-install archive above. **There is no memory-only export (JSON/NDJSON), and no way to carry the owner's erase marks across.**

### 1a. What this means for the options (Agent's view)
- windyfly's store is "on the owner's machine, protected by the machine account". It fits an owner who runs their own agent. It doesn't fit a shared server (the roster's Kit 0) without per-person encryption.
- Every option needs what windyfly lacks today:
  - erase that reaches copies (or copies that expire on a known date the owner is told);
  - a memory-only export with erase marks, for a runtime move (§5 of the convergence plan).
- Hardening, independent of the options (windyfly PR, Agent): write export archives 0600, and leave `.env` out by default (or say plainly that the archive holds secrets).

## 2. Roster today (Windy Chat)
[Chat: ...]

## 3. Options (Chat drafts; Agent second-reads)
[Chat: 2-3 options, each: where / key / erase / runtime move / cost / what leaves the machine]

## 4. Hub #9 privacy read (one per option)
[Hub]

## 5. Boss's decision
[Boss]
