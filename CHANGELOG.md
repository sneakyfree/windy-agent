# Changelog

**About the version numbers.** The git tags `v0.4.0`, `v0.5.0` (2026-03-25) and `v1.0.0`
(2026-03-26, "Complete DNA Strand") marked build phases 3, 4 and 5 of the first plan. They were
never releases: the package version at all three was 0.0.1. Package releases started on
2026-04-01 and reused those numbers for different code: 0.4.0 (the unified command registry),
0.5.0 and 0.5.1 below are package versions, not those tags, and 0.1.0 is this file's first entry.
No package 1.0.0 was ever released. The release line runs 0.5.1, then 0.6.0 (2026-07-04) and the
0.7.x releases. The `v1.0.0` tag stays where it is, as a historical phase marker.

## Unreleased

- **`windy version --config <file>`.** `windy version` now takes the config file the agent runs with and
  shows that file's daily budget, through the same reader as the budget gate. Before, it only followed
  WINDYFLY_CONFIG or windyfly.toml, so an agent started with --config showed another file's budget.

- **A daily budget that can't work no longer silences the agent.** `[costs] daily_budget_usd` set to 0 or
  less, under $0.01, text, nan or infinity used to block every turn (or crash `/version`). It now falls back
  to the default ($5.00/day) with one warning per distinct bad value. The budget gate, `/budget continue`,
  `/version`, `/status`, `/pulse`, `windy version`, `windy status` and the dashboard all read it through
  one reader, so they always show the number the gate uses.

- **"Stop" means stop.** When the owner writes "stop" (or "please stop", "enough", "be quiet") in a room
  where agents are talking, the agent stops answering the other agents there at once.
  - The stop itself gets no reply and no model call, so nothing more lands on the phone.
  - The owner's next normal message resumes.
  - The phone's Stop button (an `ai.windy.stop` event) does the same, from the owner only, and works in
    guest mode too.
  - Before, any person speaking in the room reset the agents' reply budget, so "stop" gave them six more
    replies each.
  - A stop now survives a restart of the agent. An iPhone's curly apostrophe ("that’s enough") counts.
  - Good to know: in a one-to-one chat with the agent, "stop" gets no reply either; the next message
    resumes. Only a whole message that is one of the stop phrases counts ("stop by the store" and "stop
    the timer" do not). The phrases are Windy Chat's one shared list: stop, stop it, stop now, stop
    talking, please stop, enough, that's enough, be quiet, quiet, silence, shut up, pause, halt, and
    /stop. Agents are recognised by their `@agent_` account name.
  - In a chat room, /stop now means "agents, be quiet". It no longer shuts the agent down; to stop the
    agent itself, use `windy stop` in a terminal.

- **Agents work together as a team, and the work ends on its own** (off unless `WINDY_TEAMWORK=1`, on top
  of `WINDY_TEAMS=1`; Windy Chat's agent-teamwork.v1, still a draft). In a Windy Chat room with the
  owner's other agents:
  - The agent the owner asked (the chief of staff) can hand one part of the job to another agent with
    `ask_agent`. Each request carries a budget (default 6 turns and 5 minutes, at most 20 and 30 minutes).
  - The other agent answers that request exactly once and then goes quiet. A repeat of the same request
    is not answered.
  - When every answer is in, the chief of staff writes the owner ONE answer. If time runs out first,
    the owner gets one plain line saying who answered and who did not, with no model call.
  - The agents' back-and-forth goes out as quiet notices in the task's thread, so the phone does not
    buzz for it. Only the answer to the owner is a normal, pushed message.
  - Only the owner's own agents can send the agent a task, and at most 20 tasks per room per 10 minutes
    become turns.
  - Code, not the model, refuses a request that goes more than 3 levels deep, asks back an agent already
    in the chain, or repeats an open question.
  - A turn started by another agent's task may use public reads only (web search, weather, news,
    calculations). It can't send, write, message another agent or hand out more work. Each answer it
    gets back is fenced as another agent's words, never instructions.
  - Good to know: open tasks live in memory. If the agent restarts while a task is open, that task's
    one answer to the owner is not sent; the owner asks again.
  - In a room with the owner's other agents, a short message with "stop", "halt", "enough", "pause",
    "quiet" or "silence" in it stops every agent at once and cancels the open tasks. One agent confirms
    with one quiet line. The phone's Resume button, or the owner's next message, resumes. In a
    one-to-one chat the narrow rule above stays, so "stop the timer" is still answered.

- **Agents answer other agents only when addressed.** Another agent's message is now a turn only when it
  mentions this agent, sends it a typed task, or the two of them are alone in the room. Teamwork stays
  possible; chatter that names nobody no longer wakes every agent.

- **"Save this to my cloud", "what's in my cloud?" and now "get that file back" work.** The first two
  never worked: they called Windy Cloud addresses that don't exist. They now use Windy Cloud's real storage
  routes, as Windy Cloud described them, and fetching a file back is new.
  - The agent uploads only files in the folders it may use, or a file whose full path (starting with / or
    ~) you wrote in your own message. A bare file name or a quoted earlier message does not count.
  - It never uploads keys, passwords or env files, its own memory, browser profiles or keychains (on
    Linux, macOS and Windows), or anything in a hidden folder of your home folder.
  - At most 5 files or 200 MB per message. If a file of that name is already there, it asks first,
    because Windy Cloud moves the old one to the trash.
  - Downloads go to the agent's own downloads folder, with a cleaned name, readable only by you, never
    replacing an earlier download, and within size and disk limits.
  - Only the owner can use these. Listing files saved by other Windy apps counts as reading outside text:
    for the rest of that message the agent does not upload, download or send anything.

- **Keep going today when the budget runs out.** When the agent hits its daily budget it now says the owner
  can type `/budget continue`, which lets it spend up to one more day's budget for the rest of the day, once.
  It goes back to normal at midnight UTC by itself, and the monthly budget, if one is set, is never lifted:
  when the month's budget is used up, `/budget continue` says so and isn't used up itself, and the daily
  message only offers it while the month still has room. Only the owner can do this. `/budget`, `/version`
  and `windy version` now show the limit the agent really uses, and `/budget set` really changes it (until
  a restart; a value that isn't a number is refused); before, they used a setting the agent never read.

- **The agent answers in your language.** When your Windy app tags a message with your language, the agent
  now answers in it (it only ever tagged its own replies before).
  - Only your own messages set it; another person in a shared room can't change the language of the
    agent's replies.
  - Only a well-formed language code is used (like `es`, `pt-BR`, `zh-Hant-TW`), and only a language Windy
    serves is named to the model. A message without a tag keeps the last language.
- **"What languages can you translate?"** now reads Windy's own list, fresh each time (not cached), with
  each language's quality to and from English. The list is read with limits: at most 256 KB and 200
  languages, names as plain text of at most 64 characters, and no redirects.

- **Big exports no longer stall the agent or the page.** Reading a large export (up to 90 MB) now
  happens off the agent's main loop, so the dashboard and chat keep answering meanwhile, and the
  dashboard waits up to 5 minutes for it instead of 30 seconds. A reload no longer loses a waiting
  import: the page brings its preview back. If the page has lost track of a waiting import, a "Cancel
  the waiting import" button clears it (and wipes its file) instead of blocking new uploads for 30 minutes.

- **Restoring from a cloud backup is safe again, asks first, and can be undone.** `/backup restore`
  (or `/backup restore <name>`) downloads the backup, opens it with your own backup key, and checks it,
  without changing anything. It then tells you the backup's date and size, what the agent remembers now,
  and that anything you deleted since the backup will come back. `/backup yes` restores it without
  stopping the agent. First it keeps an encrypted copy of the current memory, so `/backup undo` can put it
  back within 24 hours; after that the copy is wiped. No unencrypted copy is ever left on disk.
  - Refused without your own `WINDY_BACKUP_KEY` (said plainly).
  - Refused if the backup is damaged, too big, or made by a newer agent.
  - Plain answers when Windy Cloud has locked the agent's storage or refuses its credentials.
  - Off in guest mode.
  - `/backup list` now shows each backup's name, date and size; before, every column showed "?".
  - `/import` now says to stop the agent before unpacking a local archive.
  The old restore wrote over the memory file while the agent was using it; it was switched off earlier
  today and is now removed.

- **The strand stops promising what the product shouldn't do.** Eight leftover promises are settled on the
  record.
  - **Dropped (never used):**
    - the config settings `temperature`, `max_response_tokens`, `max_context_tokens` and
      `max_episodes_per_context`. They are removed from `windyfly.toml`, the setup templates, the defaults,
      auto-repair's tunables and the dashboard's Settings page, which showed values the agent never used;
    - the Hermes database reader (it guessed table names);
    - the empty memory graph;
    - the agent directory listing (agents stay private, as Windy Chat confirmed);
    - the old `/auth/me` check (the passport replaced it).
  - **Restated to match what is true:** writes are batched for fairness, and the owner's SOUL.md is never
    cut in raw mode, with slider text bounded.
  - **Confirmed:** pushes to the phone cover the agent's replies (Windy Chat's prod proof).

- **Only the owner can make the agent send, call, buy, publish or upload.** The agent's older tools carried
  no permission level, so anyone the agent treats below the owner could get them run, and a tool the
  model named was run for any sender, strangers included. That covers three kinds of sender: someone
  talking to it during a guest-mode demo, a text message relayed from the owner's phone (texts can be
  spoofed), and a sibling agent. Now every older tool has a level. Only tools that read public
  information (web, news, weather, plans, domain search, sums) are open to others. The owner's own data,
  changes to it, and anything that acts outward are the owner's alone. A new tool with no level is the
  owner's by default. The check happens when a tool runs, not only in what the model is offered. What
  family members or guests may get back is for the owner to decide.

- **The strand no longer claims the cloud file tools work.** "Save this file to my cloud" and "what's in
  my cloud?" have never worked in production: they called Windy Cloud routes that do not exist, and their
  tests passed only against a mock of those guessed routes (Windy Cloud, 10-17). Their strand rows are
  marked planned until they are moved to Windy Cloud's real storage routes.

- **Import from another AI on the dashboard.** The Personality page has a new "Bring a personality
  from another AI" section. Upload a ChatGPT `conversations.json` or an export zip (up to 90 MB), read
  what was found, then Approve or Reject. The file is kept in memory and wiped either way, and items
  that need your review or a tone to apply are pointed to `/soul review` and `/soul apply` in chat.
- **The dashboard can no longer make the agent read a folder on its computer.** The old soul import
  routes took a file path from the browser, so anyone with the dashboard password could make the agent
  parse any folder on the host. The path form is removed. An import now starts with an upload, and every
  later step uses the short-lived one-time token it returns.

- **Bring your old AI's tone with you.** After you approve an import, the agent tells you the tone
  it found: humour, formality and verbosity, from an OpenClaw `config.yaml` or `config.json`, or worked
  out from how you write in a ChatGPT export. It counts only your own messages, never ChatGPT's replies,
  makes no model call, and keeps only the three numbers. `/soul apply` shows what would change, `/soul yes`
  sets the sliders, and `/soul undo` puts them back (a slider you changed since stays as you set it).
  With raw mode on (the default) the agent says that tone sliders are not used until you turn it off.
  An OpenClaw tone was silently skipped before, because PyYAML is not installed with the agent; it is
  now read without it.

- **Review imported items one at a time.** `/soul review` lists the imported items waiting for
  your review by number and kind only, never their text. `/soul review 2` opens one. `/soul keep 1 3`
  lets the agent use those items. `/soul drop 2` (or `/soul drop all`) asks first, then `/soul yes`
  deletes them for good, leaving no readable copy in the memory file. The numbers come from your latest
  list and expire after 10 minutes. Soul import and review are off in guest mode.

- **An imported secret waits for review whatever it is filed as.** Only beliefs and identity items
  were held for review, so a PIN, password, key or account number filed as an ordinary fact was
  imported straight away and could be recalled into the model's prompt. Any imported item that looks
  like a secret is now held for review too.

- **Import a personality from another AI, right in chat.** Type `/soul import` and send your export
  as a file in the same chat: a ChatGPT `conversations.json`, or a zip of an OpenClaw, Hermes or
  ChatGPT export, up to 25 MB on Windy Chat or 20 MB on Telegram. The agent shows what it found, and
  nothing is kept until you type `/soul approve`; `/soul reject` cancels. Either way the file is wiped.
  The file is held in memory where the host allows, never in the agent's folders, and only the
  export files the importer reads are taken (never media or databases); zips with path tricks, links, hidden characters or bomb-like compression are
  refused. Sensitive items wait for your review and are not used until you keep them. An imported
  item never changes a fact the agent already has. `/soul` on its own still shows the personality.

- **Read receipts.** When the agent takes up your message on Windy Chat it marks it read, and then
  starts typing, so you can see it has seen your message. If the receipt fails, the reply still comes.

- **Imported items flagged for review stay out of the agent's mind until you keep them.** A soul
  import stores sensitive items (beliefs and identity items) flagged for review. Nothing read that
  flag, so those items could still be recalled into the model's prompt and show in memory searches. Every memory listing and search now skips flagged
  items, so they reach no model until the owner keeps them.

- **New mail is not lost when there is nobody to tell yet.** If the agent can't send the owner its
  new-mail notice, for example before the owner's direct room exists, the mail is no longer marked as
  seen, so the next check announces it. With several Telegram owners, the mail counts as told
  as soon as one of them got it, so the others are not told again every check. A wrong `WINDY_MAINTENANCE_RUNNER` value is logged, a Matrix
  process that isn't the runner says where maintenance runs, and the daily journal closes the memory
  file it opens.

- **A Matrix-only agent now watches its mailbox and keeps its journal.** The background jobs (the
  daily journal, key refreshes, the mail inbox watch) used to run only in the Telegram process, so an
  agent on Windy Chat alone never ran them. Exactly one process per agent now runs them: Telegram
  when a Telegram bot is set up, otherwise Matrix (a minute after it starts), so no job ever runs
  twice. `WINDY_MAINTENANCE_RUNNER=telegram|matrix` pins it. New mail is announced in the owner's
  direct room on Matrix.

- **The agent notices everyday corrections.** "That is wrong", "that's not right", a reply that
  opens with "Incorrect," or "Wrong,", and "Actually, it's ..." now count as corrections, so "Never
  Wrong Twice" learns from them. Agreement such as "Actually, that's exactly right" or "that's not
  wrong" still doesn't count.

- **Skills learn whether they helped.** When the agent uses one of its skills, the owner's next
  message decides how it went. A correction ("no, that's wrong", "I already said") counts as a
  failure for that skill, and anything else counts as a success. Before this the success and failure
  counts were never filled in.

- **`/forget` also removes the correction skill made from a correction you delete.** That skill's
  text comes from your own words, so it now goes with the correction, and a search finds it too.
  `/forget find <words>` searches for words that begin with yes, no or delete. The reply now says
  "the items you picked".

- **See and delete conversation history.** `/history` still shows the last messages, and now also
  how many there were each day for the last 30 days (UTC). `/history find <words>` lists matching
  messages. `/history delete <day>`, `<day>..<day>`, `all` or numbers from a search shows what will
  go, and nothing is deleted until `/history yes`. A day's summaries (moments, journal, turnover
  letter) and corrections go with it. Facts and other memories stay; `/forget` removes those. The
  dashboard's Memory page has a Conversations section that does the same: it previews first and
  deletes exactly what its counts showed. Deleting a conversation search
  result there now deletes that message. No readable copy is left in the memory file. Earlier
  daily cloud backups keep a copy until 5 newer backups replace them. Guest mode can't use either.

- **The dashboard no longer locks its own owner out.** Every dashboard request, including a
  signed-in owner's own page loads, counted against the limit of 5 failed sign-in attempts a
  minute. So the sixth request in a minute got "Too many auth attempts". Behind nginx every
  request comes from the same address, so that locked out everyone. Now only failed sign-ins
  count. A blocked address is still refused before the password is checked.

- **`/forget` works.** `/forget <words>` lists everything the agent remembers that contains those
  words: messages, facts and summaries, corrections, goals, tool records and log entries. Then
  `/forget delete <numbers>` (or `all`) shows exactly what will go, and nothing is deleted until
  `/forget yes`. The delete leaves no readable copy in the memory file or its log. Earlier daily cloud
  backups keep a copy until 5 newer backups replace them, and the reply says so. Demoting a
  correction skill moved from `/forget` to `/demote` (or `/unlearn`).

- **Searching memory on the dashboard also finds past conversations.** The Memory page searched
  only stored facts. It now also shows matching messages from past conversations, as a short
  preview marked "conversation". In guest mode, conversations are never shown.

- **No cloud backup without the owner's own key.** An agent with no `WINDY_BACKUP_KEY` used to
  encrypt its backups with a key worked out from its passport and name, both of which go to Windy
  Cloud with the upload, so Cloud could have opened them. Such an agent now uploads nothing. The
  scheduled backup logs a warning with a running count (never the key), and `/backup` tells the owner
  that cloud backups are off and why. Agents with their own key back up as before, and restoring old
  backups still works.

- **A conversation sent to Anthropic always starts with the person's message.** When the recent
  history began with one of the agent's own replies (for example with the context slider at 0, which
  keeps an odd number of messages), the request could start with an assistant turn, which Anthropic
  refuses. Such leading replies, and tool results whose tool call is not there, are now left out of
  that request.

- **A long conversation no longer pushes the prompt past the model's limit.** Before each model
  call the agent now checks the prompt against the real limit: the model's context window, or the
  owner's `/memory` cap, less room for the answer. If it is over, the oldest conversation history
  is left out first. The agent's personality (SOUL.md) and the current message are never cut. Only
  the window size is used; the old `max_context_tokens` setting still does nothing.

- **A failed database upgrade no longer leaves memory half changed.** Upgrades of the memory
  database used to commit each SQL step as it went, so a failure part-way left some changes in
  and released the lock other openers were waiting on. Now one upgrade run is one transaction:
  it applies completely or not at all.

- **The gateway is tested end to end without a live stack.** A new bun test starts the real
  gateway against a stand-in brain on a private socket and checks, over real HTTP and a real
  WebSocket, that the web page is served, the health check sees the brain, chat refuses a
  client without the dashboard password, and a chat message reaches the brain and its answer
  comes back. CI runs it with the other gateway tests.

- **Dates in memory keep working on newer Python.** The memory database now registers its own
  date and time handling instead of Python's built-in one, which has been deprecated since 3.12.
  Stored values and what comes back are unchanged.

- **One default model.** The model used when nothing picks one was named in four places that
  disagreed: windyfly.toml (kimi-k2.5), the built-in defaults (claude-sonnet-5), `.env.example`
  (gpt-4o-mini) and fallbacks in the code (gpt-4o-mini, claude-sonnet-4-6). Now there is one,
  `claude-sonnet-5`, in `windyfly.config.DEFAULT_MODEL`. windyfly.toml names the same model,
  `.env.example` leaves `DEFAULT_MODEL` unset so a copied file no longer overrides it, and every
  fallback reads the one value. An install that sets its own model is unchanged. An install that
  relied on the shipped windyfly.toml (kimi-k2.5) or on the old gpt-4o-mini last resort now asks for
  claude-sonnet-5, so it needs an Anthropic key or Windy Mind.

- **A damaged Vault lease key file is reported, not replaced in silence.** If the agent's lease key
  file can't be read or holds no key list, the agent logs one warning naming the file and the reason
  (never its contents) before a new key replaces it. A missing file is still a quiet first run. The
  bad file is not copied aside, because a plaintext copy of a key file is not allowed.

- **A reply that failed to send goes out on the next good sync.** The agent kept such replies but
  sent them only after it reconnected, so while the connection stayed healthy a person could wait
  hours for an answer. Now every successful sync sends what was kept, at most once every 30
  seconds, so a reply the homeserver keeps refusing is not sent again on every sync.
  Each sync sends at most 3 kept replies, so a hung send cannot hold up the next sync for long, and
  a reply still kept after 24 hours is dropped with one warning and counted in the heartbeat.

- **Memory can no longer split between the agent and other programs.** Opening the agent's memory
  file to check its header, and copying it for the daily backup, quietly released the agent's
  locks on it. The next other program to close the memory (the WindyTalk bridge on a restart)
  then thought it was the last user and removed the agent's recent-writes file. From then on the
  agent and that program each kept writing memories the other could not see (Windy 0, 2026-10-15).
  The header is now checked through SQLite itself. Backups, `/export`, `windyfly backup` and the
  copy made before a restore all take a consistent snapshot through SQLite, so they also include
  writes not yet folded into the main file (backups used to leave those out). On Linux the agent
  now checks every 15 minutes for this split and logs an ERROR "memory split" if it ever happens
  again. `/export` also saves the configured memory file rather than always `data/windyfly.db`.

- **"Windy's computers are busy" is a short wait, not an outage.** For an owner whose account uses
  only Windy's own computers, Windy Mind may answer "busy" (503 `no_third_party_unavailable`). The
  agent now waits the time Mind asks for once and tries again. If they are still busy, it does not
  mark Mind as broken and never switches to the backup brain for later turns. A model on the
  owner's own machine may answer, with one plain line saying so; otherwise the owner gets "My helper
  is waiting for a free Windy computer. Please try again in a minute." If the owner picked a model
  that runs outside Windy (403 `no_third_party_model`), the agent says so plainly instead of
  quietly using another model or falling back. Another agent gets no reply in either case.

- **The agent's own background calls name Haiku 5.5.** The daily journal, failure lessons, goal
  checks and the vision fallback named Haiku 4.5 in code. Windy Mind quietly served 5.5 instead,
  but once Mind serves every pick exactly as named they would have moved to the older, pricier
  model without anyone choosing it. They now share one setting, `SMALL_CLAUDE_MODEL`
  (`claude-haiku-5-5`). An owner's `journal_model`, `goal.evaluator_model`, `WINDY_DISTILL_MODEL`
  or `WINDY_VISION_MODEL` still wins.

- **The model menu comes from Windy Mind, so new models show up by themselves.** `/model` now lists
  the models Windy Mind lets this agent use (`GET /v1/catalog/me`, read with the agent's own token
  and cached for a minute), and `opus`, `sonnet` or `haiku` picks the newest of that family that is
  up right now, so "haiku" means Haiku 5.5 once Mind offers it. If Mind can't be read, `/model`
  falls back to the old list. The agent's "Model:" line now names the model that actually answered
  this conversation's last reply, and claims no model before the first one, instead of quoting the
  configured default.

- **An agent on a Claude model keeps its web search.** When a Claude model (Opus 5.5, for example)
  was reached through Windy Mind, the agent swapped its own web search for Anthropic's built-in one,
  which Mind cannot run, so the agent had no search at all. On the Mind route it now keeps its own
  web search; the built-in one is used only when the agent talks to Anthropic directly. One rule
  (`mind_route_first`) now decides both where the turn goes and which search it gets.

- **Windy Chat no longer forgets the conversation when the agent restarts.** Each room's thread now
  has a stable id (`matrix:<room>:v<N>`, the shape Telegram already uses) instead of a random one
  made fresh by every process, so what you said before a restart or an update is still in the
  conversation afterwards, on every device. `/new` and the wake-up catch-up now work in Windy Chat
  too. Session ids whose channel holds colons (every Matrix room id) are now read correctly.

- **The weekly fire drill no longer fails a lifeboat that worked.** The lifeboat step now passes when
  the reply carries either lifeboat marker (🛟, or the 🚨 "free local model" notice) and the answer
  to 2+2, read after the notice. It prints the whole reply instead of the first 200 characters. The
  rule lives in `scripts/fire-drill-lifeboat-check.py`. No change to anything an owner sees.

- **Agents can no longer talk to each other forever.** In a Windy Chat room, an agent now answers at
  most 6 messages from other agents in 10 minutes unless a person says something there. Past that it
  ignores the other agent (no AI call, so a loop costs nothing) and tells its owner once, in their own
  chat: "I stopped answering <agent> in <room>: we had sent each other 6 messages in 10 minutes with no
  one else talking. Say something in that room and I'll carry on." Any message from a person in the
  room resets it. Agents that choose not to reply use none of the allowance. Settings:
  `WINDY_AGENT_TURN_BUDGET`, `WINDY_AGENT_TURN_WINDOW_S`.

- **A paused agent never answers another agent.** While its owner has it switched off in
  Windy Mind, the agent still tells a person, plainly, that it is paused (no model call), but
  it posts nothing in reply to another agent. Two paused agents of one owner in a room
  would otherwise answer each other's "I'm paused" line forever. In Windy Chat an empty
  answer to an agent now always posts nothing, with or without agent teams switched on.

- **The event log records memory, conflicts and goals.** Every memory write
  (`memory.write`), every new held conflict (`conflict.detect`) and every goal the agent
  notes or sees again (`intent.surface`) is now an event, with ids and types only (never
  what the memory or goal says). Five event types that nothing ever wrote are gone:
  `cost.log` (cost lives in the cost ledger, the one record), `skill.evaluate`,
  `matrix.message`, `personality.change`, `email.outbound`. A test now fails if a declared
  type is never emitted.

- **A token saved from chat survives a restart on every install.** Setting up Cloudflare or
  GitHub from chat wrote the token to `~/.windy/windy-0.env`, a file only Windy 0 loads, so
  on any other install it was gone at the next restart. It now goes to the env file the
  agent actually loads (`WINDY_ENV_FILE`, else the project `.env`). Windy 0 is unchanged
  (it sets `WINDY_ENV_FILE`).

- **Provider keys live in one place: your agent's private env file.** The old dashboard's
  hidden Providers page (`index.legacy.html`, linked from nowhere) and its own key files are
  gone, with all 15 `/api/providers` routes and the OpenRouter sign-in. The agent never used
  most keys saved there. **Nothing is lost:** at its next start the agent moves every key it
  finds in `data/providers.json`, `data/provider_keys.json` (the active key),
  `data/provider_notes.json` (a key pasted into a note) or `~/.windy/providers.json` into
  its env file (`WINDY_ENV_FILE`, else the project `.env`; 0600). A key already in the
  env file wins. Only after every key is saved does it remove the old dashboard files and
  strip keys from `~/.windy/providers.json`. If saving fails, it changes no file, uses those
  keys for that run, and tries again at the next start. Its log names providers, never keys.
  Notes you wrote on that page are kept in `~/.windy/retired-provider-notes.json` (0600),
  with anything that looked like a key replaced by "[moved to the env file]" (or
  "[key-shaped text removed]" when that key was not the one moved). To add or change a key:
  put it in your env file, or run the setup wizard. Windy agents with a passport choose their model in Windy
  Mind. A key in `windyfly.toml` (`[providers.<name>] api_key`) is not touched.

- **No more "warmth" slider leftovers.** The warmth slider was removed in July, but
  the dashboard's quick presets still set it (each click sent a change the agent
  refused), the gateway still listed it when the agent was offline, and the setup
  page still wrote it into new config files. All gone; a test now checks that every
  slider name the dashboard and gateway use is a real slider.

- **`/decay` and the dashboard's decay button also fade old goals.** They ran only the
  memory decay; the daily maintenance run also decayed intents. All three now do the
  same thing.

- **Phone tools contract updated to phone-tools.v1 1.0.1** (wording only: the privacy
  line now says the chat server keeps a deleted phone-tool message up to 3 days, then
  erases it). Phone tools stay off unless `WINDY_PHONE_TOOLS=1`.

- **A personality restore applies only the list you confirmed.** If a slider moved
  between showing you the list and your click, nothing is restored; the dashboard
  shows the new list and asks you to confirm again.

- **`/skills` works.** It imported two functions that never existed, so it always
  answered "Error". It now lists the promoted playbooks (`/skills all` for every one).

- **Agent teams and phone tools know who the agent is without MATRIX_BOT_USER.**
  They read the agent's own Matrix id from that setting alone, so an agent without
  it (every quickstart install, and Windy 0) answered "chat is not configured" when
  it had to create a pair room. They now use the id the chat server confirms for
  the agent's token.

- **Only your own words become facts and goals about you.** The agent used
  to pick up "my name is ..." or "I live in ..." from anyone who wrote to it
  (another person, a paired agent) and store it as a trusted fact about you,
  and to turn anyone's "I need to ..." into one of your goals. Now it learns
  facts and goals only from your own messages, and not from a turn where it
  also read mail or the web. Messages from others store nothing of this kind
  and do not refresh your existing goals.

- **Removed `/mode` (companion / focused / neutral).** Gone: the `/mode`
  command, the `mode.get` / `mode.set` bridge methods, `GET`/`PUT /api/mode`,
  `get_mode_override` and `personality/mode.py`. Why: `/mode` said "Mode
  switched" but changed nothing (the saved value was never read), so it told
  the owner something untrue. Tone now comes from the model plus your soul; to
  tune it, use the personality sliders with raw mode off.

- **Dashboard summary no longer crashes on unpriced usage.** A task type
  whose ledger rows are all unpriced (local / Mind rows store no cost) made
  the whole summary fail; it now shows 0.0 for that type.

- **Shape-shift and the old sub-agent module are retired.** Removed the
  `shape_shift` / `shape_shift_restore` tools (already unregistered since the
  10-10 tool trim), the bridge methods `shape_shift.execute` /
  `shape_shift.restore`, the gateway routes `/api/shape-shift` and
  `/api/shape-shift/restore`, the `shape_shift_bias` slider (from every preset
  and the cost table), and the unused `agent/sub_agents.py`. Why: a smart model
  changes its approach without a tool; the slider was read by nothing and
  restore said "restored" while doing nothing. Instead: ask the agent directly
  to work as a coder, writer, researcher and so on; the model adapts on its
  own (named long-running helpers are still the collaborators). An old
  `slider_shape_shift_bias` setting in an existing database is ignored.

- **Mission Control (remote machine management) removed.** It dialed a
  daemon on port 3100 that no repo ever shipped, so it never worked, and its
  exec and provider-sync routes would have sent commands and plaintext
  provider keys over unencrypted ws:// on a LAN. Gone: the Machines page,
  every `/api/machines*` route, and the `/ws/terminal/:id` and
  `/ws/machine/:id` sockets. Manage each machine's agent on that machine
  (windy CLI / its own dashboard); fleet operations are not part of Windy Fly.

- **The gateway deletes Mission Control's leftover `data/machines.json` at
  start.** It held the tokens typed for remote machines. The gateway
  overwrites it with zeros, deletes it, and logs one line. That is cleanup,
  not a secure erase: SSDs can keep old blocks. If you never start the
  gateway again, the file is safe to delete by hand.

- **Executable skills removed; skills are text playbooks only.** Why: the
  skill "sandbox" was a plain subprocess with full disk, network and (for JS)
  the agent's secrets, and its safety check ran after the code. Now: skills are
  text playbooks the agent reads; code runs only through the agent's tools
  (e.g. `shell.exec` in its sandbox) under the owner's trust settings. Gone: the
  evaluate / golden-test / regression runners and their bridge methods, API
  routes, dashboard button and `skills-run/eval/test` commands; any skill
  language other than `playbook` is refused; old code rows stay inert text.

- **Three claims that were not true are retired.** Memory search is keyword +
  full-text (SQLite FTS5); the optional `[semantic]` extra adds embedding
  similarity. The unused `sqlite-vec` dependency is gone. Matrix messages are
  not end-to-end encrypted by Windy Fly: the dead encryption code (key upload,
  device auto-trust, key requests) is removed, and a message in an encrypted
  room now logs one plain line saying it cannot be read. Each agent's Matrix id
  is its own `@agent_<passport>` from its passport, not a shared
  `@windyfly` bot user; an old config that still sets `bot_user` keeps working.

- **Memory conflicts wait for the owner.** When something new contradicts
  what the agent already remembers, the old value stays and the new one is
  held until the owner chooses (before, the new value silently replaced the
  old one, and "keep old" could not bring it back). On the owner's turns the
  agent sees up to three held conflicts (before vs new) and can record the
  owner's choice with one tool, `memory.resolve_conflict`; the owner can also
  type `/conflicts keep <id> new|old`. Saying the same thing again is not a
  conflict. The agent's own notes (turnover letters, journal days) are still
  simply rewritten. Your own direct words are never held, because you are the
  one who chooses: `/remember`, and facts read from your own messages, apply
  at once. What is held: contradictions that come from anyone else's
  messages, mail, SMS, imports or the agent's own guesses. The agent sees held
  values as quoted data, not instructions, and records a choice only when you
  said which one in your latest message.
  **Database change (migration 15) at the first start after upgrading:** it
  only adds four columns to the `conflicts` table and changes no existing
  rows. Back up first anyway: send `/backup now`, or copy `windyfly.db` from
  the agent's data folder while the agent is stopped. Going back to 0.7.6 is
  safe: the old version ignores the new columns. A conflict still held at
  that point stays unapplied (the old value remains); choosing "new" in the
  old version does not apply it, so upgrade again and choose there.

- **Intents now infer, decay, and stress trends are seen.** The model-based
  intent guess (which fills the Intent Inbox, `/intents`) crashed silently on
  every try because of a prompt formatting bug; it is fixed, but it adds one
  small model call on most turns, so it stays off unless you set
  `WINDY_INTENT_LLM=1`. The mood trend now looks only at your own messages,
  so three stressed messages in a row count as sustained stress even with the
  agent's replies in between.
- **Old goals fade on their own (daily maintenance; that step was never
  called before).** Once a day, an active intent nobody has mentioned for 7
  days loses 5% of its weight; below 0.3 it is paused, so roughly a month
  after it was last mentioned. Mentioning it again resets it to full weight.
  What changes: a paused intent no longer shows in the dashboard's active
  list or the active counts (dashboard, `windy status`), and it is no longer
  passed to a collaborator you allowed to see your goals. `/intents` (the
  last 24 hours of new guesses) is not affected. It stays in the database as
  paused and is never deleted. The agent's own prompt does not include
  intents, so nothing in its replies changes, with raw mode on or off.

- **Personality drift detection now works, and the owner sees it.** Once a
  day the agent compares every personality slider with what it was about 30
  days ago (or the oldest record, on a newer install). If a slider moved more
  than 2 points and the owner did not move it, the dashboard's Personality
  page shows a warning with the old and new values and a button to restore
  them. The owner's own slider, preset and rollback changes never count as
  drift. Before this, the daily check recorded today's values before
  comparing, so it never found anything. The Personality page also lists the
  slider history with a "Restore to here" button. Before anything changes, a
  restore shows exactly which sliders it will set and to what (for example
  "humor: 9 → 5"); nothing changes unless you confirm that list, and a
  failed restore says so. Rollback changes only the personality sliders and
  puts back the exact value from that time.

- **Windy Pro tools appear only when they can work.** The four Windy Pro tools (translation history, recordings, clone status, translate) register only when the agent has a Windy Pro credential (`WINDY_JWT`) and Windy Pro answers it at start. Before, they were always offered, pointed at `localhost:8098`, and failed when Windy Pro wasn't there. To use them: set `WINDY_JWT` (and `WINDY_API_URL` if Windy Pro is not on this machine) and restart.

## 0.7.6

- **The agent tells Eternitas which machine it lives on (housing report, ON).**
  Once per start, on a background thread, the agent sends Eternitas
  (`housing.v1`) a hashed machine id, the machine class (laptop, desktop, ...)
  and a snapshot: OS, CPU, GPU, disk, connection type, virtual or not, and the
  device serial (over TLS; Eternitas stores a hash). One attempt, 3 seconds in
  all; it never slows or blocks the agent; the serial is
  never written to a local log. Turn it off with `WINDY_HOUSING_REPORT=0`.

- **A Windy agent (one with an Eternitas passport) gets its AI only through
  Windy Mind**, or the local backup model when Mind is down; it no longer falls
  back to provider keys or a Max login. The owner's stop in Windy Mind is a real
  stop. The agent can read and switch its own Mind model, and vision and
  `windy model test` go through Mind too. Installs without a passport keep
  using their own keys.

- **Email: one message to everyone.** `send_email` takes to/cc/bcc and sends
  ONE email with one confirmation. With `WINDY_SEND_CONFIRM=1` an outbound email
  is held as a draft until the owner says send, with tappable [send, wait]
  buttons. New `my_mailbox` tool: the agent's own address, aliases and plan
  limits, as Windy Mail states them.

- **One shared set of owner commands** (`commands.v1`: /help, /status,
  /whoami, /model, /usage, /pause, /resume, /new, /memory, /forget, /agents),
  the same names as Windy Chat's agents, case-insensitive, honest when a
  command can't do something; slash and recovery commands are owner-only.
  `windy commands --json` exports the command list.

- **Agent teams (off unless `WINDY_TEAMS=1`).** `list_my_agents` and
  `message_agent` let an owner's agents message each other; another agent's
  message is labelled with who sent it, and an agent may stay silent on a
  greeting. With Windy Chat's session-free pair rooms, the calling agent makes
  the room itself (nobody signs in to the owner's account). `/agents on|off`
  lets the owner stop other agents' messages. No first-contact tour is sent to
  an agent.

- **Invite gate (off unless `WINDY_INVITE_GATE=1`).** The agent joins a Matrix
  room only when invited by its owner or by one of the owner's own agents
  (Windy Chat confirms); other invites get no answer.

- **Fewer tools (128 -> about 80).** An owner turn had grown to 128 tools, and one
  more pushed it past Windy Mind's hard limit (it refuses more than 128), so every
  owner message fell to the local model. Now: one website builder (`windycodeweb_*`;
  the old `create_site`/`publish_site` set is gone), no calculator/dice/unit tools
  (the model does these), no fleet or in-process collaborator tools (real sibling
  agents replace them), `set_timer` folded into `set_reminder`, and `mind.*` is three
  tools (`mind.status` also carries usage and why paused; `mind.switch_model("reset")`).
  GitHub, Cloudflare, SSH, Windy Word, health and the Windy Code IDE tools appear only
  when they are set up AND their credential answers at start (one quick check; a
  refused token keeps them off, a network blip does not). Saving a credential from
  chat turns its tools on in the running agent, and says so plainly when it could not.
  Each start writes the owner-turn tool count to `owner-tools.json` in the state
  folder; tests fail the build above 88 tools (typical) or 118 (everything set up).

- **Off unless switched on:** Windy Calendar tools (`WINDY_CALENDAR=1`), Windy
  Code filing-cabinet tools (`WINDY_CODE_CABINET=1`).

- **Fixes.** The Matrix token is sent in a header, never in a URL (URLs can be
  logged), and the chat trust check now really runs (it used to be skipped).
  DPoP proofs on GET for Windy Calendar and Windy Chat. The hatch key is kept
  when a ticket is lost or a Ctrl-C cancel fails. Plain-word refusals and SMS
  errors. `windy_plans` reads prices only from Windy's live plans page. Tool
  descriptions shortened to one line each.


- **Calls to Windy Mind use the agent's own short-lived token.** Every chat call
  carries an `EPT+agent` for `windy-mind` (bound to the agent's registered key)
  plus a fresh DPoP proof; the owner-stop check and the recovery probe send the
  token without a proof. If the token can't be minted or Mind refuses it, the
  agent falls back to its long-lived Eternitas token, which Mind stops accepting
  on writes from 2026-11-03. Set `WINDY_MIND_EPT_AGENT=0` to turn it off.

- **Agents send email only from their own Windy Mail mailbox, through Windy Mail.**
  The Resend, Gmail (`email.send`, `windy setup-gmail`) and SendGrid senders are
  removed, so From and Reply-To are always the agent's own address; with no
  mailbox, nothing is sent. Sends authenticate with the agent's Eternitas token
  (was `WINDY_MAIL_SEND_EPT=1`); `WINDY_MAIL_STRICT` and
  `WINDY_MAIL_ALLOW_RESEND` are gone.

## 0.7.5

**Default changes in this release**

- **One hallway.** The old terminal hatch is gone: `windy go` always opens the
  Windy hatch ceremony in the browser (ADR-059); `WINDY_HATCH_VIA_HUB=0`, the
  terminal key-paste/kiosk hatch, CLI-side Matrix/mail provisioning, the mock mail
  service, birth SMS and phone-number provisioning, and the gateway's remote hatch
  are removed, along with the unused opt-out helpers. (#430, #438)
- **The offline queue is never replayed through the model.** Messages that arrive
  while the agent is offline are no longer re-run on reconnect (a replay ran with no
  sender band, so a stranger's queued message could act as the owner); the owner is
  told what was missed instead. (#434)
- **Reading a page is obvious.** `web_search` finds pages and `fetch_url` reads one;
  a link in the owner's message adds a hint to read it before answering. (#429)
- **Windy Search limits are spoken plainly.** The daily free-search allowance means
  "stop for today" (no retry loop), and a site whose robots.txt refuses automated
  readers is final: no retry and no direct-fetch workaround. (#431, #433)
- **No double-counted model calls at HQ.** Calls routed through Windy Mind no longer
  emit a local `llm.call`; Mind is the one source of those events. (#437)

**Built in, off by default (each enabled only on Windy Hub's word)**

- `WINDY_TEXT_BYO=1` now follows Windy Text's rules: the recipient consent check
  fails closed, the first text to a new number uses Text's approved template, the
  server adds the `[Name · Windy]` label, and sends authenticate with the agent's
  mode-B Eternitas token plus a DPoP proof. (#436)
- `WINDY_SKILL_DROPS=1`: install SIGNED skill bundles from Windy Drops as playbook
  skills: signature and live trust band checked, the owner confirms in code, no
  permission widening, refreshed every 6 h. (#435)

## 0.7.4

**Default changes in this release**

- **Windy Code is where agents build websites.** With an Eternitas token the
  `windycodeweb_*` tools now reach the live builder (cloud.windycloud.com) by
  default, and the agent is told to build there: a one-file save keeps the
  site's other files, there are templates, click-to-edit text and binary files
  (images), and the builder's daily limits come back in plain words.
  `WINDY_CODE_WEB_DEFAULT=0` opts out. (#419, #424, #427)
- **Nothing goes live without the owner's own yes.** Publish, unpublish and
  connect-a-domain hold the builder's confirmation in code; the model never sees
  it, and only the owner's reply ("yes, publish" / "yes, unpublish" /
  "yes, connect") spends it. A token the model sends back is refused, and the
  trust check now fails closed. Builder-side Inbox approvals are relayed and never
  retried. (#425)
- **Site writes are refused on tainted turns** (content from an untrusted page or
  email in the same turn). (#419)
- **SMS and phone numbers are parked.** The birth SMS, the Twilio SMS channel and
  phone-number buying all need `WINDY_ENABLE_SMS=1`; a parked or mock SMS is no
  longer recorded as sent. (#411)
- **Replies carry the model that answered** (`uk.windypro.model`, Windy Chat's
  contract), set only from Windy Mind's own answer. (#402, #404)
- **The backup encryption key can't drift with branding.** The passport-derived
  backup key's default name is a frozen constant with known-answer tests, so
  existing backups keep decrypting. (#407)

**Built in, off by default (each enabled only on Windy Hub's word)**

- `WINDY_SEND_CONFIRM=1`: the agent only drafts outbound email; the owner's own
  "send" (or "cancel") sends it, and the mail says who approved it. (#415)
- `WINDY_MAIL_STRICT=1`: send only from the agent's own Windy mailbox; no silent
  Resend or Gmail fallback. `WINDY_MAIL_SEND_EPT=1`: sends authenticate with the
  agent's own Eternitas token. (#410, #417)
- `WINDY_TEXT_BYO=1`: text through Windy Text on the owner's own Twilio account
  (first text to a new number needs the owner's yes, handled in code), and read
  inbound texts: contacts are only relayed to the owner, the owner's texts can't
  trigger side effects. (#416, #420)
- `WINDY_LOOP_GUARD=1`: at most 6 agent-only messages in a row per room, and 120
  replies an hour to non-owners pauses the agent until the owner sends /resume.
  (#414)
- `WINDY_PARITY_BANDS=1`: an agent with a valid Eternitas passport gets the user
  band (safe tools), a revoked or suspended one is refused, and an agent is never
  taken for the owner. (#426)
- `WINDY_MIND_HELPER=1`: Windy Mind's shared resilience helper (one retry, then a
  standby, then a local model, always marked). (#405, #408)
- `model = "auto"`: send Mind no model so its owner policy picks. (#409)

**Fixes**

- A greeting no longer flips a healthy agent into the lifeboat. (#398)
- Mind-routed agents recover from the lifeboat, a 4xx from Mind no longer cools
  Mind down, and `/model opus` means Opus 5.5. (#399)
- A thinking model that runs out of tokens before answering is retried once with
  the full budget. (#400)
- Tests can never reach a real local Ollama (CI runs on a GPU host). (#421)
- Test fixtures use obviously fake credentials; a secret-shapes lint guards CI.
  (#395, #412, #413)

### Earlier in 0.7.4

- **A backup-brain reply is never unmarked.** When the model chain was exhausted and auto-resurrect declined (cooldown, disabled, or the post-recovery grace), the offline reply went out with no notice; a tripped Mind breaker hid this for 4 days on Windy Zero. The first such reply per session now says so in a sentence, later ones carry `🛟`. A 403/breaker is named in the auto-switch banner, and `offline.chain_exhausted` logs `notice_shown` separately from `auto_resurrected`.
- **Every reply logs where its time went.** One line per turn:
  `[req:…] timing total=… queued=… prompt=… (memory_search=… embed=… embed_wait=…) llm=…×n tools=…×n other=…`.
  `queued` is the wait for the agent's single turn thread, `embed_wait` is time
  blocked behind a background memory save, and helper model calls (facts,
  journal, …) show separately as `llm:<purpose>`. Durations only, and timing
  can never break a reply.

- **Short-lived, key-bound service tokens (Eternitas mode B).**
  `agent_keys.request_agent_token(aud)` trades a DPoP proof by the agent's
  registered key for a ≤5-minute `EPT+agent` scoped to one Windy service
  (`windy-calendar`, `windy-mail`, …), bound to the key (`cnf.jkt`) and
  carrying the agent's live Integrity Index. No bearer is sent, so a leaked
  EPT can't mint one. Tokens are cached per service until 30 s before expiry,
  refusals raise `AgentTokenError` with Eternitas's code (and `retry_after`
  on 429), and a token bound to any other key is rejected. `service_dpop()`
  makes the per-request proof that services require for writes.

- **Tool calls through Windy Mind reach the model you asked for.** Tool names
  like `vision.describe` are made safe for every provider behind Mind (letters,
  digits, `_`, `-`, at most 64 characters) and mapped back in the reply, and
  Anthropic-only server tools are no longer sent to Mind. Before, a full toolset
  made every named provider refuse the request, and Mind answered on a small
  local model instead of the one the agent asked for.

## 0.7.3

- **A busy Windy Mind no longer drops the agent into the lifeboat with the wrong
  reason.** When Mind answers 429/502/503/504 (busy: a free quota spent, a lane
  down), the agent waits (Retry-After, else 15 s, at most 30 s) and retries once.
  If Mind is still busy, the lifeboat notice now says "Windy Mind is busy right
  now" instead of blaming a missing credential (a Mind-routed agent has no
  direct key by design, so the fallback chain always reported `no-key`).

- **A terminal hatch through Eternitas's auto-hatch door sends `X-Windy-Hatch-Id`**
  (a fresh uuid4 per hatch attempt; Eternitas #185), so Windy Admin can join one
  birth across services. It's an opaque id and carries nothing about the owner.
  The hub ceremony (the default `windy go`) already stamps its own session id.

- **`windy go` opens the one hub hatch ceremony (ADR-059) — now the default.**
  `windy go` creates a hatch ticket at the hub (nothing is minted), prints
  "Open this to hatch your agent: <link> (code ABCD-1234)", opens your browser
  and waits while you check the code, name your agent and hatch it on the
  ceremony page. At "It's alive!" it prints "Say hi: <Windy Chat link>". The
  agent is born and lives in the cloud: nothing is written to `.env`, and this
  machine only remembers it (0600, beside your Windy sign-in), so the next
  `windy go` says you already have it. Ctrl-C cancels the ceremony at the hub.
  If your account already has an agent, `windy go` names it and offers to use
  it (never mints another). `windy bring-home`, to run the agent on this
  machine, is a placeholder until the hub's handover ships.
  **Opt-out for one release:** `WINDY_HATCH_VIA_HUB=0` runs the old terminal
  hatch, with a notice that it goes away in the next release.

- **Cost accounting is honest now.** Claude models were missing from the
  price table, and an unknown model fell back to gpt-4o-mini's price, so
  claude-opus-5 was billed at $0.15/M input: Windy 0's ledger read $0.14
  for about $4.70 of list-price work.
  - **Real prices:** Anthropic's current rate card for Opus 5 / 5.5 / 4.8,
    Fable 5 / 5.1, Sonnet 5 and Haiku 4.5, including cache writes (5 min
    and 1 h) and cache reads, which Anthropic reports separately from
    `input_tokens`. The longest matching model id wins.
  - **Never a guess:** an unknown model, or an unpublished price for a
    token class the call used, records the cost as unknown (NULL; no
    `cost_microcents` on the telemetry row) rather than a made-up number.
  - **Every call, once:** the ledger gets one row per LLM call, recorded
    inside `call_llm` for every path (turn rounds, retries, the voice
    bridge, intent/journal/goal/sub-agent helpers). Failed calls get a row
    too, with an error code (`rate_limited`, `auth`, `provider_http`,
    `timeout`, `network`, `no_provider`, `internal`). It used to be one
    row per turn from the loop only, with no failures and no `request_id`.
  - **Billing:** each row says `max_subscription` (list-price equivalent on
    a Max plan; the marginal cost is $0), `metered` or `local`.
  - Admin `llm.call` telemetry is now per call as well (successful calls).

- **Field health telemetry, disclosed and switchable off.** New
  `service.boot`, a 15-minute `service.health` (turn, tool, 429 and lifeboat
  counts, p95 turn time, and how many of our own rows the ingest quarantined
  or we dropped), `agent.run_failed` (one per turn where the human got no
  real answer, with a declared code), and `agent.model_demoted` (once per
  switch to a weaker or local model: the silent `llama3.2:3b` demotion is now
  a row).
  - Rows are validated against the enums declared at Windy Admin before they
    leave; an invalid row is dropped and counted, never sent to be
    quarantined. A 202 that quarantines anyway is logged as a warning.
  - **Customer installs send via the agent's own passport token.** Sources,
    in order: the fleet's `WINDY_ADMIN_INGEST_TOKEN`, then
    `WINDY_TELEMETRY_CLIENT_TOKEN` (empty by default; no token is built into
    the package), then the agent's EPT (default on; windy-admin #294 verifies
    it and pins rows to that passport). `WINDY_TELEMETRY_EPT_AUTH=0` turns the
    EPT path off. An agent with no passport sends and counts nothing.
  - Rows are **batched**: flushed at most every ~10 s, 100 per request (the
    ingest's cap), plus a best-effort flush at exit, well under the 30
    requests/min per passport limit.
  - A **429** waits out `Retry-After` (default 60 s) and keeps a bounded
    buffer (oldest dropped and counted); it never trips the breaker. A 413
    splits the batch and logs a warning.
  - A **401** for an expired EPT or an unpublished key renews the EPT once
    and retries. A 401 that persists, a revoked passport, an issuer mismatch
    or a 403 trips the breaker: one warning, no more sends this run, and a
    24h marker in `~/.windy` (0600) across restarts.
  - **Disclosed before anything is sent.** One line says what's sent at the
    end of a successful hatch and at the first `windy login` / `windy go`,
    and writes a marker in `~/.windy`. The passport and client-token paths
    stay closed until that marker exists (or `WINDY_TELEMETRY=1` is set, or
    someone runs `windy telemetry status|on`); before that nothing is built
    or counted. The fleet's own emitter token is exempt.
  - New `windy telemetry status|on|off`: the credential in use, whether the
    line was shown, and whether sending is paused; on/off saves the
    preference. `WINDY_TELEMETRY=0` still turns everything off. See
    docs/PRIVACY.md.
  - `WINDY_SYNTHETIC=1` (our probes only) marks rows `synthetic` and adds
    `X-Windy-Synthetic: 1` to requests to Windy hosts only (never third parties). The fire drill and the
    continuity battery mark their rows synthetic and warn on a quarantine.

- **`windy go` no longer crashes without a terminal.** Run from a script, CI
  or `docker exec` without `-i`, an already set-up `windy go` died with an
  `EOFError` traceback at "Already set up! Launch Windy Fly?". Every setup
  prompt now takes its stated default when stdin is at EOF, and says so in one
  line. (Found by the 0.7.2.1 clean-machine proof.)
- **No brain claims for a dead passport.** After `windy deregister` (or a
  suspension), `windy go --keyless` no longer says the agent is "powered by
  your agent's Windy passport" / "Windy Mind brain", and plain `windy go` no
  longer says "Windy Mind (free, keyless) configured". Both say the passport is
  revoked (start over with `windy go --force`) or suspended (reversible: the
  agent keeps its identity; no `--force`).

- **Consent for messages to other people** (legal review, 2026-09-23).
  - **Texts:** the first text to any new number now needs the owner's yes. `send_sms` returns `confirm_required` with a question the agent must relay word for word, and only `confirm_sms` (single-use, 10 minutes, bound to the exact text) sends it and remembers the number.
  - Every text ends with "— <agent>, AI assistant for <owner>. Reply STOP to opt out."; a `recipient_opted_out` answer is final and never retried.
  - **SMS itself stays off** until a sender that enforces STOP exists: without one, the tool says "SMS isn't available yet for Windy Fly agents."
  - **Emails:** every email now ends "Sent by <agent>, an AI agent acting for <owner>.", and the Resend path adds `X-Windy-Agent: <passport>`.

- **Texting, calls and phone numbers are parked until after launch.** `send_sms`
  says "Texting isn't available yet; I can reach them by email or you can
  message them in Windy Chat.", `make_call` says the same for calls, and the
  hatch no longer assigns a phone number: it could otherwise BUY a Twilio
  number on the owner's own account, or hand out a fake +1555 mock.
  (`WINDY_ENABLE_PHONE_PROVISION=1` re-enables it for development.)

**Every agent gets its own signing key (Eternitas agent-keys v1).** A
passport number shows that a passport exists; a key shows that the caller
holds it. See `docs/AGENT_KEYS.md`.

- The agent generates an ES256 (P-256) key pair on its own machine and
  registers only the public key with Eternitas, with a proof of possession.
  The private key never leaves the device and is not in logs or cloud
  backups.
- One credentials file per agent: `WINDY_CREDENTIALS_FILE`, otherwise
  `<state dir>/credentials.json`. It is mode 0600, written atomically, keeps
  a timestamped backup, and leaves other keys in the file alone.
- Registration runs as a background boot step (`eternitas.agent_keys`), a
  daily job, after `windy login`, and at the end of a successful hatch. Until
  Eternitas ships the routes it logs one INFO line and does nothing else.
- New `windy agent-key status|rotate|reset|revoke`. `reset` is the owner's
  recovery for a lost key: it runs a fresh browser sign-in (`prompt=login`),
  registers a new key (`reason: recovery`, or `--reason handover` when moving
  the agent), and revokes the old ones. Eternitas allows 2 per passport per
  24h. (`windy keys` stays the wk_ bot credential.)
- **EPT refresh by key.** Once the agent has a registered key, the EPT refresh
  first proves itself with an `Eternitas-Agent-Proof` header signed by that
  key. It needs no bearer, so it works even after the EPT has lapsed. The
  agent's own EPT and the owner's `windy login` session remain the fallbacks.
- Eternitas error codes (`detail.code`, e.g. `too_many_active_keys`,
  `stale_auth_time`, `owner_registration_limit`) are shown as they are.
- `sign_artifact()` returns a detached JWS carrying `kid` and `passport`,
  for Windy Drops signed publish.
- `hub_login.login()` gains `reauth=` (sends `prompt=login` and `max_age=0`)
  and `store=` (return the token without saving the session).

- **`windy bring-home` — move your cloud agent onto this machine** (ADR-059,
  hub handover AGENT_HANDOVER.md §9). One fresh sign-in; the agent's own
  ES256 key is registered at Eternitas on the owner path (`reason:
  handover`, old keys untouched so a failed handover leaves the cloud agent
  working, and the new key is revoked). The hub hands over Windy Chat and
  Windy Mail credentials in a one-time pickup, which is sealed to a 0600 file
  the moment it arrives, so a crash never loses it (re-running resumes; after
  the pickup is gone the same key starts a rotate round). The agent's memory
  (`ndjson-v1`) is checked against its sha256/event count, kept verbatim and
  imported as episodes; its turnover letter is the first thing the local
  agent reads. The passport token comes from Eternitas by the new key's
  proof. The body gets the keyless Windy Mind brain; the Matrix bot uses the
  handed-over device (`MATRIX_DEVICE_ID`) instead of minting another; Windy
  Mail sends authenticate with the agent's passport token.
- **`windy start --channel matrix`** runs one chat channel in the foreground
  (how a body brought home answers in Windy Chat; plain `windy start` runs
  the voice bridge + gateway). The runtime now loads the project `.env`
  explicitly: a bare `load_dotenv()` searched from the installed module's
  folder, so a pip install never read the agent's own settings.

## 0.7.2.1

Found by the 0.7.2 clean-machine proof from PyPI:

- **`windy deregister` works on a pip install.** It now loads the agent's own
  env file (WINDY_ENV_FILE, else the project `.env`), like `windy ept refresh`,
  so it finds the agent's passport without `--passport`, and after revoking it
  comments the dead token out of `.env` (with a backup) as intended.
- **`windy go` no longer shows a revoked passport as active.** It checks for the
  `# REVOKED` mark `windy deregister` leaves, then asks Eternitas's public
  registry (one 4-second call, fails open), and says plainly that the passport
  is revoked or suspended and that `windy go --force` hatches a new identity,
  instead of "✓ already has a passport" and "✓ Free Windy Mind brain connected".
  A **suspended** passport is a reversible lock: `windy go` says the agent keeps
  its identity until the suspension is lifted and never suggests `--force`.
- `.env` is written owner-only (0600): it holds the passport token.
- `windy login` flushes the sign-in link, so it shows up when output is piped
  (containers, CI).
- The post-hatch screens stop asking a customer for `SYNAPSE_REGISTRATION_SECRET`,
  stop printing a gateway log path when no gateway runs, and no longer tell the
  user to "click the link" when there's no link.

- **Security: a remote hatch can no longer take over the host agent's
  identity** (hatch hallway audit §2e #5). `POST /hatch/remote` now refuses
  an empty `passport_number` with a 400. The hatch subprocess no longer
  inherits the gateway host's `ETERNITAS_PASSPORT`, `ETERNITAS_PASSPORT_TOKEN`,
  `ETERNITAS_OPERATOR_JWT`, `WINDY_HUB_JWT`, `WINDY_ENV_FILE` or
  `WINDY_CREDENTIALS_FILE`. `windyfly.hatch_remote` drops those itself too,
  and no longer defaults `--passport-number` from the environment. Before
  this, an empty passport made the new agent adopt the host's passport.

## 0.7.2

**Terminal hatching now signs in with your Windy account.** Eternitas is
closing the anonymous hatch door (`/bots/auto-hatch` will require a
credential), so hatching from the terminal needs the owner's Windy sign-in.

- New `windy login` (browser sign-in via loopback + PKCE; works over SSH
  with the printed link and a port forward), `windy logout`, and
  `windy whoami`. The sign-in is stored in `~/.windy/hub_session.json`
  (mode 0600) and refreshed automatically.
- An interactive `windy go` / hatch asks you to sign in when it needs to.
  Non-interactive hatches fail with a clear "run `windy login`" message
  instead of a bare 401.
- auto-hatch credential order: the owner's hub token always wins —
  `WINDY_HUB_JWT` → the `windy login` session → `ETERNITAS_OPERATOR_JWT`
  (only when there is no hub token). Eternitas binds the owner from a hub
  token; an operator JWT would file the agent under a placeholder operator.
- Identity link-back uses the `windy_identity_id` claim and never falls
  back to `sub` on a hub login token (there `sub` is a different id).
- Agents that are already hatched are unaffected: nothing re-hatches or
  asks you to sign in at startup.
- Agents now renew their Eternitas passport token automatically (at boot
  and daily; or run `windy ept refresh`), using Eternitas's new
  self-refresh endpoint. A token is renewed with its own still-valid EPT,
  or, once expired, with your `windy login`. This also picks up new
  claims (e.g. the link to your Windy account) without a re-hatch. For
  systemd installs, set `WINDY_ENV_FILE` to your unit's `EnvironmentFile`
  so the renewed token survives restarts.

- **Web search works on real installs again.** The Windy Search client read
  `WINDY_PASSPORT_EPT` and required `WINDY_SEARCH_BASE_URL`, but hatch and
  `windy ept refresh` store the passport token in `ETERNITAS_PASSPORT_TOKEN`.
  So `web_search`/`fetch_url` refused every call. It now reads
  `ETERNITAS_PASSPORT_TOKEN` (the old name still works), defaults to
  https://api.windysearch.com, uses the canonical `POST /v1/search`, explains
  401/429/503 plainly, and stops calling until the 1st after the monthly
  budget runs out (instead of retrying a 429 all month).
- New `windy deregister [--passport …] [--yes]`: the owner permanently revokes
  the agent's Eternitas passport (owner's Windy sign-in → Eternitas operator
  session → revoke, with the full revocation cascade). It asks y/N (default
  No), refuses non-interactively without `--yes`, and afterwards comments out
  the dead `ETERNITAS_PASSPORT_TOKEN` line in the agent's env file (atomic,
  with a backup). Memory and files are left alone.

**First run, fixed end to end** (from a clean-machine test of `pip install
windyfly` → `windy login` → `windy go` → chat → `windy ept refresh`):

- **A fresh `windy go` now gets a real passport.** With no `ETERNITAS_URL`
  set, the hatch used to have no issuer and gave up. It now defaults to
  https://api.eternitas.ai and writes `ETERNITAS_URL` into the generated
  `.env`. Set `ETERNITAS_URL` to use a different issuer, or `ETERNITAS_URL=off`
  to switch Eternitas off.
- **One agent, one passport.** The hatch now saves the passport number
  (`ETERNITAS_PASSPORT`) next to its token. Re-running `windy go` keeps both
  instead of blanking them, and won't hatch an agent that already has a
  passport; `windy go --force` (or `windy deregister`) starts over.
  Previously a re-run minted a second passport.
- **No more ✓ for things that didn't happen.** "✓ uv installed" only prints
  when uv really is installed (the installer pipe now uses `pipefail`).
  A pip-installed Windy Fly doesn't need uv or bun at all and starts with the
  current Python. The local mock mail server only runs with the explicit
  dev/test opt-in, and a placeholder inbox or phone is shown as
  "not set up yet" rather than "✓". A real passport is no longer labelled
  "(local)".
- **You can chat right after hatching.**
  - `windy go` ends with the exact command: `windy stop && windy chat`.
  - The "already running" refusal says the same thing.
  - `windy stop` now waits for the agent to exit and releases its runtime
    slot, so the next `windy chat` isn't refused as "already hosting this
    agent". An exited-but-unreaped (zombie) process no longer counts as
    running, which made `windy stop` sit out its whole timeout in containers.
  - `/quit`, `/exit` and Ctrl-D leave the chat.
  - INFO logs go to `data/cli.log` instead of scrolling through the
    conversation.
- **`windy ept refresh` reports every outcome honestly.** It reads the same
  env file the refresh writes (`WINDY_ENV_FILE`, else the project `.env`).
  A token Eternitas keeps (`reissued: false`, or any status this version
  doesn't know yet) is reported as "kept — still valid". Only an HTTP error or
  refusal, or an unreachable issuer, prints "Refresh failed", and it says
  which. (It used to print "Refresh failed (None)".)
- `windy --version` works (same output as `windy version`). Help text and the
  guided Anthropic signup now use a current model instead of the retired
  `claude-3-5-sonnet-latest`, and the Anthropic key check no longer rejects
  good keys by probing a retired model. Chat's "never provisioned" hint names
  `windy go` (there is no `windy hatch`).

## 0.7.1

**Action required: set your Telegram owner explicitly.** Through 0.7.0,
an install with no `AGENT_OWNER_TELEGRAM_ID` silently defaulted its
Telegram allowlist to one maintainer's personal account. That account
was treated as the owner of your agent, and your own messages were
dropped. The default is gone. Set `AGENT_OWNER_TELEGRAM_ID=<your
numeric Telegram id>` (or `WINDY_OWNER_IDS="telegram:<id>"`) in your
env. If you leave it unset, the first person to message the bot is
bound as its owner (trust-on-first-use, as on every other channel), so
message it yourself first.

Also since 0.7.0:
- Telegram: a rejected bot token is no longer written to the log and
  event ledger in full (#364)
- Liveness probe: no longer restarts the agent every 15 min when
  Telegram rejects the token, since a restart can't fix that (#364)
- Auth: fixed a race that could drop a turn to the local lifeboat model
  under concurrency (#363)
- Context gauge: no longer drains N× on an N-tool turn (#362)
- Native web search re-enabled on claude-opus-5; voice replies work
  again (#361)
- Gateway: tests no longer bind :3000 on import (#365)

## 0.7.0

Ten weeks of work that never reached the wheel. 0.6.1 shipped
2026-07-06; master ran 108 commits past it (PRs #259–#358) while
`windy update` told every installed copy it was already current.

By 2026-08-11 that had stopped being a packaging problem: #353 had to
warn, in the README and in `docs/DISTRIBUTION.md`, that `pip install
windyfly` shipped a client which could mint a **second passport** for
an agent that already held one, and 401'd on a fresh machine with no
operator key — because the published wheel predated the
adopt-don't-mint guard (#345) and the terminal door's move onto the
consumer endpoint (#349). Both warnings said the fix was "a release
≥ 0.7.0". This is that release; the warnings are removed with it.

Also here: two new subsystems (MCP, the cross-platform supervisor), the
Chronicle memory plane, and a security pass that closed real holes.

### MCP — both directions (ADR-060)
- Native MCP control server over the Capability Plane (#282), later
  widened to expose the FULL plane (#286)
- `mcp.*` client — the Fly consumes external MCP servers, Fly-as-doctor
  (Route C) (#285)

### Supervisor — the timer zoo is gone (Tiers 1–3)
- Cross-platform guardian sidecar + heartbeat file (#318); OS keep-alive
  backends + guardian entry point (#319)
- In-process maintenance scheduler replaces the per-job systemd timers
  (#320); continuity battery folded into it (#323)
- `install-service` now installs the supervisor (#322)
- Retry-once on a torn heartbeat read (#321); launchd `gui/user` domain
  fallback + XML escaping (#325)

### Memory — the Chronicle plane
- `memory.search` + `memory.read_range`: the model gets the key to its
  own past (#313)
- The dated Journal index over the Chronicle (#314), with its own node
  type so it stops colliding with the reflective diary (#315)
- Decay may dim, never erase — Doctrine Law 1 (#312)
- The agent reads its own history when it wakes up (#336)
- Semantic recall was dead, then fatal, then near-pointless — fixed
  (#330); `soul_history` bloat capped with retention + VACUUM (#271);
  migration 11 no longer crashes a concurrent open (#343); recall stops
  dropping the one word that identified the memory (#341)

### Security
- Agents owner-bind by default; owner PII and tooling band-gated from
  strangers (#265)
- Taint model closes injected-content → exfil/RCE (#280)
- Inbound SMS + email resolve a real sender band instead of OWNER (#279)
- SSRF guard on `fetch_url`'s direct-httpx fallback (#278)
- Capability deny-list matches Windows separators too — `\` used to walk
  straight past it (#311)
- Nothing in the registry required OWNER, so TRUSTED bought `shell.exec`
  (#344)
- Bridge: SO_PEERCRED doorman + off-thread dispatch (#305); a non-object
  JSON frame gets a structured error, not a silent disconnect (#297)

### Recovery, and proving it works
- Weekly fire drill — the agent rehearses its own death (#303), robust
  to a pending systemd job (#308)
- Weekly continuity score — Principle #7 as a number (#307)
- Chaos harness: kill it, break its world (#334), runnable on Windows
  (#335)
- Auth survival: self-heal a mid-run OAuth rotation (#262), unfreeze
  rotated tokens (#276), OAuth-aware paid-health probe so recovery is
  possible on Max-plan boxes (#291), and an auth-dead agent keeps a
  local brain instead of "try again later" (#296)
- Probe-heal watches the real per-channel units — the heal path had been
  dead 15h (#294)
- Turnover letters on graceful shutdown, not just `/new` (#301)
- Engine transparency: the serving model shows in the gas-tank panel
  (#298); rescue-kit menu cuts 91 commands to the 15 that matter when
  the brain is broken (#302); welcome-spam race latched (#300)

### Channels
- Signal, IRC and Teams are launchable; Teams silent-drop send fixed
  (#273)
- Matrix: real identity resolved from the access token — Windy 0 had
  been dark on Windy Chat (#260); `/sync` backoff, honest heartbeat, and
  it joins the hatch DM room (#357)
- Mail inbox watch — the agent notices mail sent to it (#358)
- `/remember` actually persists on Matrix (#259)

### Hands
- `windycode` via the Agent Bus (#275) and `windycode_web` in the browser
  builder (#287, #288, base64 on the wire #289)
- `windy_domains` + `windy_sites` on the Cloud cells (#283), following
  redirects through nginx trailing-slash routing (#284)
- `fetch_url` renders JS pages via windy-search Browserbase (#266)
- The agent turns the dials on the user's Windy Word app (#263) and
  carries its per-install control token (#281)
- The voice path gets the tool registry — grandma can DO things by voice
  (#292)

### Identity / hatch
- Eternitas issues the signed certificate of record; the local mint is
  retired (ADR-064) (#293), footer overprint fixed (#290)
- The terminal door hatches through the consumer door (#349); the browser
  door no longer mints a second passport and orphans the first (#345)
- An unconfigured run no longer invents a passport and calls it success
  (#346)
- Revocation survives an Eternitas outage (#353); two invented revocation
  receivers dropped (#354)

### Portability
- Every file read and write in `src/` names its encoding (#332); tests
  too (#324) — the cp1252 landmine
- The skill sandbox was dead on Windows; `stop()` dropped memory
  silently (#333)
- `os.replace` for the offline queue (#310)
- start/stop found the pid file relative to CWD (#339); commands
  silently operated on the wrong folder when run from elsewhere (#342)
- An optional extra could crash the agent on Intel Macs (#340)

### Models
- `claude-opus-5` in the catalog — the patch Windy 0 had run uncommitted
  since 2026-08-18 (#356); `claude-opus-4-8` (#261)
- Mind retries model-less on 422, so catalog drift stops knocking agents
  off their primary brain (#295)

### Personality
- Raw-model mode, and adaptive-mode deprecated (#316); raw mode is the
  default on purpose, not by accident (#331)
- Steering → substrate: hardcoded emotion injections and fs/shell
  keyword nudges retired in favor of self-descriptions (#304)

### Telemetry
- `llm.call` events to Windy Admin — the last un-ledgered burn point
  (ADR-WA-001) (#274), with real `duration_ms` (#277)

### Housekeeping
- CI moved to the self-hosted Kit 0 runner (#328), with uv pinned so
  `setup-uv` skips the forge releases API (#355)
- Test suite stopped reading the machine's real credentials (#338) and
  leaking a resident `.env` (#309, #317); WriteQueue leak that reddened
  master fixed (#329)
- The suite can no longer take a live agent's systemd unit down: the
  guard now covers `systemctl_stop`, the `os.system` `pkill`
  fall-throughs, and `subprocess.run` itself, so an unenumerated caller
  is caught too (#359)
- Post-AWS-exit dead weight deleted (#306)

## 0.6.1

The keyless release (PRs #247–#256): 0.6.0 shipped hours before the
keyless grandma path landed, so the published package had none of it.
This release brings PyPI up to the runtime the ecosystem actually runs.

### Keyless / Windy Mind brain
- `windy go` option 1 = "Free — no key needed": keyless config, hatch,
  and launch with the Windy Mind free-compute brain (#248)
- Fly→Mind brain path is load-bearing: EPT captured + persisted at
  hatch, Mind responses translated to the loop's shape, Mind on the
  provider circuit breaker (#247); tool-bearing calls flow to Mind by
  default now that Mind's tool-calling is live (#254)
- Kiosk honesty: piped/no-TTY `windy go --keyless` no longer aborts the
  hatch on EOF, installs uv/bun itself, and reports truthfully when the
  free cloud brain could not be connected instead of claiming success
  (#256)

### One-soul chat identity
- The Fly logs into Windy Chat as its own `@agent_<passport>` identity
  minted from its Eternitas passport, with the hatch's DM room and the
  minted device id; the roster midwife yields while the real Fly holds
  the runtime claim (#252)
- Runtime claim accepts the agent's EPT as bearer (#253) and recovers
  the passport id from the EPT itself, so keyless agents actually claim
  their slot — no more double replies from midwife + Fly (#255)

### Reliability
- Matrix channel shuts down cleanly on SIGTERM: the sync loop is
  cancelled, presence goes offline, pending messages flush, and the
  runtime claim is released immediately (was: process hung until the
  supervisor SIGKILLed it and the claim orphaned for its full TTL) (#255)
- The grandma rescue kit (`/normal`, `/resurrect`, `/pause`, `/resume`,
  panic phrases) now works on the CLI channel too — previously the CLI
  bypassed the rescue layer and reported "Unknown command" for the exact
  commands the lifeboat banner suggests (#255)
- `--channel matrix` without credentials prints a friendly pointer to
  `windy go` instead of a raw traceback (#255)

### Provisioning contracts
- Agent provisions its own mailbox with its EPT; send-tool ambiguity
  fixed (#249)
- Cloud backup conforms to the canonical archive contract with
  AES-256-GCM (#250); `windy deploy --vps` uses the canonical
  deploy-fly contract (#251)

## 0.6.0

Ten weeks of merged work (PRs #151–#233) finally reaches the release
channel — PyPI had been frozen at 0.5.1 since April while master moved on.

### Stability ("tank" sprint, from the 2026-07-04 architecture audit)
- Recovery-layer portability: lifeboat/pause/guest/panic flags now default
  to `~/.windy` via `windy_state_dir()` instead of a hardcoded dev-box path
  (they silently did nothing on customer machines) — with a tripwire test
- Corrupt `windyfly.toml` boots on safe defaults + loud warning + `/status`
  notice instead of crash-looping under systemd
- Updates record a rollback version, verify the new install in a fresh
  interpreter, and auto-roll back on a broken release; `windy rollback`
  works with no argument; pre-release version strings compare correctly

### Channels
- Discord + Slack as first-class BYO-token channels; per-channel runtime
  claim so one agent can live on several channels at once
- Telegram menu curation (destructive commands hidden from autocomplete)

### Models
- Opus 4.8 support: temperature deprecation handled across the 4.7+ line,
  reasoning-depth wired to extended thinking, OAuth x-api-key collision fix

### Fixes
- `/lifeboat` "Since:" line rendered (wrong state key since May)
- WindyMailAdapter sends `body_text`; `ETERNITAS_URL` canonicalized
- Dashboard chat protocol + systemd status detection fixes


## 0.5.1

### Bug Fixes
- Fixed `matrix_provision.py` — missing `import logging` and `logger` caused NameError on provisioning failure
- Changed bare `except Exception:` to log warnings in matrix provisioning

### Tests
- Comprehensive E2E hatch test suite (`test_hatch_e2e.py`) — 30+ tests covering orchestrator flow, naming ceremony, birth certificate generation, retry/recovery, email PDF attachment, and SMS formatting

### Dead Code Cleanup
- Removed 6 unused integration stubs from `integrations/`: windy_word, windy_cloud, contact_discovery, windy_traveler, windy_clone, push_gateway
- Removed `test_integrations.py` (tested only dead stubs)
- Trimmed `test_hardening_integrations.py` to keep only live `tools/windy_api` and agent loop resilience tests

## 0.5.0

### Birth & Identity
- Naming ceremony — agent asks for its name after hatching
- Birth certificate shows hardware specs (CPU, RAM, GPU, OS)
- "Creator" label replaces "Owner" on birth certificate
- Birth certificate PDF attached to announcement email
- Post-hatch nudge to Windy Chat

### Daemon Mode
- `windy start --daemon` runs brain + gateway as fully detached background processes
- `windy go` defaults to daemon mode — agent survives terminal close
- macOS launchd service install (`windy install-service`)
- Linux systemd service install (`windy install-service`)

### Unified Command Registry
- 140 unified commands (108 core + 32 ecosystem-exclusive)
- Same commands work on terminal, Telegram, Discord, Slack, Matrix, WhatsApp, Signal, Teams, IRC
- Dangerous command gating (kill, reset, forget require confirmation)
- BotFather script for Telegram command autocomplete

### Channel Adapters
- 7 new adapters: Telegram, Discord, Slack, WhatsApp, Signal, Teams, IRC
- Matrix adapter refactored to extend ChannelAdapter
- Auto-detection from environment variables
- `windy channels` shows configured/unconfigured platforms

### Industrial-Grade CLI
- 35+ terminal commands: start/stop/restart/kill/ps, doctor/debug/logs, model/soul/budget, memory/skills, export/import/reset, repl
- PID file with key=value format (brain, gateway, started timestamp)
- `windy kill` escape hatch — always works even if everything else is broken

### Birth Announcement
- SMS via Twilio (with mock fallback) — "IT'S ALIVE!" template
- HTML email via Windy Mail with agent identity card
- Dashboard link for instant chat

### CI/CD
- GitHub Actions workflow: lint, type check, test (Python 3.12 + 3.13), build

## 0.4.0

- Unified command registry (117 commands)
- Channel adapter system
- Industrial-grade CLI

## 0.1.0

- Initial release
- ReAct agent loop with memory, personality, skills
- Matrix bot integration
- Eternitas passport system
- Birth certificate generation
