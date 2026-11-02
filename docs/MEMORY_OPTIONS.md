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

## 2. Roster today (Windy Chat; facts from windy-chat main 7afdd17a + Kit 0, 2026-11-02, counts only)
**Where the agent's "memory" lives today.** Nowhere of its own (see the convergence plan §2b):
- the conversation is the Matrix room itself: each turn pulls the last ~12 `m.room.message` events from Synapse and
  gives the model the last 10 (`runner/matrix-io.js` `_fetchHistory`); nothing is summarised or kept between turns;
- small per-agent preferences in the agent's own Matrix account data (model choice, peer policy, per-room stop);
- with #512 (dark): the Mirror's R23 ids (rids, pids, device ids, event ids, times; never words) in the agent's account
  data `ai.windy.mirror_store`.
**Who holds the key.** No per-person key. The agent DM is NOT end-to-end encrypted, by design (Grant 2026-07-26,
CLAUDE.md invariant 6): Windy can read it. Synapse's Postgres is on Kit 0's disk; its backups are age-encrypted with
the central backup key (identity in the lockbox).
**Copies.** Synapse's nightly pg_dump to R2 (dailies 35 days, weeklies 90 days); Kit 0 volume copies (7 days);
predeploy snapshots (14 days). These hold the room messages, and so would hold anything stored in Synapse.
**How the owner erases today.** Deleting a message = a Matrix redaction (the server keeps the original 3 days today,
1 hour after MR30 (c)); deleting the Windy account = onboarding's purge (rooms deleted; with #512, each agent's Mirror
store cleared). Copies in backups age out on the dates above (the privacy text says "within 90 days").
**Runtime move.** The room is the memory: windyfly reads the same room when it takes the agent's session
(chat_session), so today's "memory" moves for free; nothing else exists to move.

## 3. Options (Chat drafts; Agent second-reads; each needs a Hub #9 read before any build)
Common ground for all three: only what the owner says or the agent learns in the owner's OWN 1:1 DM; never Mirror data
(Mirror person notes stay MR13's own store and rules); never another person's words from a group room; the owner can
list, delete one, or delete all (`/notes`, `/forget`, as MR25 drafted for the Mirror); each recalled item is quoted to
the model as data; a turn that recalls nothing behaves as today.

### Option A: notes in the agent's own Matrix account data (server-side, next to the room)
- **Where:** short notes (each ≤ 280 chars, ≤ 200 notes) in the agent's account data `ai.windy.memory_notes`, the same
  place and shape MR13 drafted for Mirror notes, minus the Mirror tags.
- **Key:** none per person, same as the room it came from (invariant 6); backups as today.
- **Erase:** `/forget` deletes at once from the live store; backups keep it until they age out (dailies 35 d, weeklies
  90 d), said plainly in the words; the owner purge clears it (the #512 pattern, the agent's own token).
- **Runtime move:** free: windyfly reads the same account data with the agent's Matrix session.
- **Cost:** S (roster: write/recall/list/forget + one prompt slot; no new service). Storage: account data, tens of KB.
- **Leaves the machine:** recalled notes go to Windy Mind with the turn (Windy's compute; Mind keeps no one's words).

### Option B: per-owner encrypted store on Kit 0, erase by destroying the owner's key ("crypto-shredding")
- **Where:** a roster-owned SQLite (its own volume), every row encrypted with a per-owner data key; the data keys are
  wrapped by one master key from the lockbox (file-mounted, never in env).
- **Key:** Windy holds it (the wrapped per-owner key + the master); no human reads rows without the master.
- **Erase:** `/forget` deletes the row; "forget everything" or the owner purge DELETES the owner's data key, so every
  copy, backups included, is unreadable the same minute (no 90-day tail). Key rows are excluded from the volume backup
  (kept only in a small key store with its own short retention) so a backup can never bring a shredded key back.
- **Runtime move:** an export (decrypt, `ndjson-v1` as windyfly's bring-home reads, plus the erase marks) on the
  owner's request; windyfly imports it. Not free: one tool on each side.
- **Cost:** M (key handling, export, a retention rule for the key store; a Hub security read). Storage: tiny.
- **Leaves the machine:** as A.

### Option C: memory on the owner's own device (like the Mirror), the server keeps none
- **Where:** in the Windy app on the owner's computer or phone (OS keychain-protected store), reached through a small
  tool contract (memory-tools.v1: recall / note / forget), the way the Mirror reads work.
- **Key:** the owner's device.
- **Erase:** on the device, immediately; the server never held a copy beyond the turn.
- **Runtime move:** free: both runtimes speak the same tool contract to the same device.
- **Cost:** L (a contract + device builds on Word and Fly + both runtimes); memory only works while a device is online.
- **Leaves the machine:** the recalled notes, to Windy Mind, per turn.

### Chat's recommendation (for Boss; Hub reads all three)
**A now, B if notes ever hold more than short facts.** A matches where the agent's words already live (the same room,
the same backups, the same erase story), costs S and moves between runtimes for free; its honest limit is the 90-day
backup tail, which the privacy text already states for messages. B is the answer if notes grow into summaries of
conversations (then a key-destroying erase is worth M). C is the most private but the least available; it fits a later
"memory on my devices" choice the owner opts into, not the default.

## 4. Hub #9 privacy read (one per option)
[Hub]

## 5. Boss's decision
[Boss]
