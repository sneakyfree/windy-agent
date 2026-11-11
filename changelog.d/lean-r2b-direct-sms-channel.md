- **Your agent texts through Windy Text only.** The agent's own direct Twilio connection is removed: its channel,
  the `/sms` command, the `--channel sms` start option and the two `/api/sms` routes of the local gateway. Nothing
  about who may be texted is loosened. The do-not-text list stays and is asked before every text; a person who
  texted STOP is still never texted.
- **By default your agent texts you and nobody else.** Texting another person through Windy Text now also needs
  `WINDY_SMS_ALLOW_NON_OWNER=1`, the same switch the direct connection had. With it off, a text to anyone else is
  refused before any request leaves your computer.
- **If the key that protects the do-not-text list goes missing, you are told once by mail**, as before; your agent
  then texts only you until the key is back.
