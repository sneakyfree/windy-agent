# "Windy Fly" in the code, by meaning

Boss TILE RULINGS (BOARD 2026-11-06T19:19:22Z): "Windy Fly" = the app (Windy Fly Mobile + Windy Fly Desktop); the agent
= "your Windy Fly agent". Change only where the AGENT speaks as itself. Measured on master 2026-11-07: every "Windy Fly"
in a Python string literal under src/ (docstrings and comments left out), not followed by Mobile / Desktop / agent: **97**
(the TODO line said 102 on 11-06; a few have changed since).

## 1. The AGENT speaking as itself: CHANGED in this PR to "your Windy Fly agent" (4)

`agent/welcome.py:149`, `channels/matrix_bot.py:1581`, `commands/registry.py:319`, `hatching.py:198`

## 2. The agent's own short label or record: NOT changed yet, a wording call for Boss (5)

Context-header signature on replies (`[🪰 Windy Fly · time · …]`, 2), the Matrix presence lines "Windy Fly is ready" / "shutting down" (2), and the passport description sent at hatch (1).

`agent/context_header.py:140`, `agent/context_header.py:143`, `channels/matrix_bot.py:2015`, `channels/matrix_bot.py:2169`, `eternitas/models.py:15`

## 3. The DEFAULT AGENT NAME (used until the owner names the agent): NOT changed, an identity decision (12)

Changing these renames every unnamed agent. If Boss wants it, it is one constant (branding / config default) plus the env defaults, in its own PR.

`agent/first_interaction.py:42`, `agent/welcome.py:164`, `cli.py:363`, `cli_status.py:164`, `config.py:38`, `observability/version_info.py:218`, `quickstart.py:179`, `quickstart.py:269`, `setup_wizard.py:496`, `tools/outbound_identity.py:24`, `vps_deploy.py:127`, `vps_deploy.py:129`

## 4. The default name inside the cloud-backup KEY DERIVATION: NEVER change (old backups would not decrypt) (2)

The default name is mixed into the backup key. A new default here would make every existing unnamed agent's backup unreadable.

`cloud_backup.py:101`, `cloud_backup.py:328`

## 5. The PRODUCT / brand (the app, the product list, the tagline, the HiFly-exclusive marks): stays "Windy Fly" (8)

`agent/your_windy.py:35`, `branding.py:29`, `cli.py:1260`, `cli.py:1493`, `commands/core.py:2755`, `commands/registry.py:348`, `commands/registry.py:355`, `hub_login.py:322`

## 6. The SOFTWARE on the machine (CLI, processes, service, logs, setup, doctor, backups, config headers): stays "Windy Fly" (66)

`agent/capabilities/fleet.py:163`, `agent/capabilities/mcp_client.py:288`, `backup_restore.py:142`, `backup_restore.py:152`, `bridge/uds_server.py:798`, `bring_home.py:152`, `channels/cli.py:57`, `channels/matrix_bot.py:365`, `channels/matrix_bot.py:422`, `channels/matrix_bot.py:2031`, `channels/matrix_bot.py:2182`, `cli.py:207`, `cli.py:219`, `cli.py:374`, `cli.py:622`, `cli.py:651`, `cli.py:658`, `cli.py:722`, `cli.py:1102`, `cli.py:1287`, `cli.py:1394`, `cli.py:1414`, `cli.py:1448`, `cli.py:1472`, `cli.py:1538`, `cli_selftest.py:225`, `cli_status.py:264`, `commands/_legacy.py:124`, `commands/_legacy.py:446`, `commands/_legacy.py:561`, `commands/_legacy.py:611`, `commands/_legacy.py:661`, `commands/_legacy.py:793`, `commands/_legacy.py:869`, `commands/_legacy.py:881`, `commands/_legacy.py:901`, `commands/_legacy.py:914`, `commands/_legacy.py:923`, `commands/_legacy.py:1479`, `commands/_legacy.py:2251`, `commands/core.py:89`, `commands/core.py:124`, `commands/core.py:129`, `commands/core.py:160`, `commands/core.py:167`, `commands/core.py:169`, `commands/core.py:187`, `commands/core.py:225`, `commands/core.py:284`, `commands/core.py:286`, `commands/core.py:598`, `main.py:272`, `main.py:317`, `main.py:482`, `main.py:748`, `observability/disclosure.py:27`, `observability/version_info.py:193`, `quickstart.py:142`, `quickstart.py:239`, `setup_wizard.py:139`, `setup_wizard.py:456`, `setup_wizard.py:590`, `setup_wizard.py:592`, `soul_import/preview.py:82`, `supervisor/install.py:56`, `vps_deploy.py:318`

