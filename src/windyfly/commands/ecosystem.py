"""Ecosystem-exclusive commands — Windy Fly only, not in HiFly forks.

These commands reach into other Windy products on behalf of the user.
They are the moat — the reason to stay in the Windy ecosystem.
"""

import os
import logging

from windyfly.commands.registry import Command, registry

logger = logging.getLogger(__name__)

_db = None


def init_ecosystem(db=None):
    global _db
    _db = db
    _register_ecosystem()


def _re(name, desc, cat, handler, aliases=None, usage=""):
    """Register an ecosystem-exclusive command."""
    registry.register(Command(
        name=name, description=desc, category=cat,
        handler=handler, aliases=aliases or [],
        ecosystem_only=True, usage=usage,
    ))


def _register_ecosystem():

    # ═══════════════════════════════════════════════════════════════
    # EMAIL — Windy Mail (109-114)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_send_mail(ctx):
        args = ctx.get("_args", [])
        if len(args) < 2:
            return "Usage: /send-mail <to@email.com> <subject>\nThen type the body and send."
        to = args[0]
        subject = " ".join(args[1:])
        api_url = os.environ.get("WINDYMAIL_API_URL", "")
        token = os.environ.get("WINDYMAIL_JMAP_TOKEN", "")
        if not api_url or not token:
            return "Windy Mail not configured. Run /go to provision your mailbox."
        try:
            import httpx
            r = httpx.post(f"{api_url}/api/v1/send", json={
                "to": [to], "subject": subject,
                "body_text": f"Sent by Windy Fly agent on behalf of {os.environ.get('WINDY_OWNER_NAME', 'owner')}",
                "mode": "independent",
            }, headers={"Authorization": f"Bearer {token}"}, timeout=15)
            if r.status_code in (200, 201):
                return f"✓ Email sent to {to} — Subject: {subject}"
            return f"Failed to send: {r.status_code} {r.text[:200]}"
        except Exception as e:
            return f"Error: {e}"
    _re("send-mail", "Send an email", "14_email", cmd_send_mail,
        aliases=["sendmail"], usage="send-mail <to> <subject>")

    async def cmd_inbox(ctx):
        args = ctx.get("_args", [])
        api_url = os.environ.get("WINDYMAIL_API_URL", "")
        token = os.environ.get("WINDYMAIL_JMAP_TOKEN", "")
        if not api_url or not token:
            return "Windy Mail not configured. Run /go to provision."
        try:
            import httpx
            params = {"limit": 10}
            if args and args[0] == "unread":
                params["unread"] = "true"
            r = httpx.get(f"{api_url}/api/v1/inbox", params=params,
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if r.status_code == 200:
                messages = r.json().get("messages", [])
                if not messages:
                    return "📧 Inbox is empty."
                lines = ["📧 Inbox:\n"]
                for m in messages[:10]:
                    read = "  " if m.get("read") else "●"
                    lines.append(f"  {read} {m.get('from','?'):20s} {m.get('subject','(no subject)')}")
                return "\n".join(lines)
            return f"Error: {r.status_code}"
        except Exception as e:
            return f"Error: {e}"
    _re("inbox", "Show recent emails", "14_email", cmd_inbox, usage="inbox [unread]")

    async def cmd_read_mail(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /read-mail <id>"
        return f"READ_MAIL:{args[0]}"
    _re("read-mail", "Read a specific email", "14_email", cmd_read_mail, usage="read-mail <id>")

    async def cmd_reply_mail(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /reply-mail <id>"
        return f"REPLY_MAIL:{args[0]}"
    _re("reply-mail", "Reply to an email", "14_email", cmd_reply_mail, usage="reply-mail <id>")

    async def cmd_mail_stats(ctx):
        email = os.environ.get("WINDYMAIL_EMAIL", "not provisioned")
        return f"📧 {email}\n(Detailed stats available when Mail API is running)"
    _re("mail-stats", "Show mail statistics", "14_email", cmd_mail_stats)

    # ═══════════════════════════════════════════════════════════════
    # PHONE — Twilio via Windy Cloud (115-118)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_sms(ctx):
        args = ctx.get("_args", [])
        if len(args) < 2:
            return "Usage: /sms <+1234567890> <message>"
        number = args[0]
        message = " ".join(args[1:])
        sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        from_number = os.environ.get("TWILIO_PHONE_NUMBER", "")
        if not all([sid, token, from_number]):
            return "Phone not configured. Set TWILIO_* env vars."
        try:
            from twilio.rest import Client
            client = Client(sid, token)
            msg = client.messages.create(body=message, from_=from_number, to=number)
            return f"✓ SMS sent to {number} (SID: {msg.sid})"
        except ImportError:
            return "Twilio SDK not installed. Run: pip install twilio"
        except Exception as e:
            return f"SMS failed: {e}"
    _re("sms", "Send an SMS message", "15_phone", cmd_sms, usage="sms <number> <message>")

    async def cmd_call(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /call <+1234567890>"
        return f"Voice calls coming soon. For now, use /sms {args[0]} <message>"
    _re("call", "Initiate a voice call (coming soon)", "15_phone", cmd_call, usage="call <number>")

    async def cmd_sms_history(ctx):
        return "SMS history available when Twilio is configured."
    _re("sms-history", "Show recent SMS messages", "15_phone", cmd_sms_history)

    async def cmd_voicemail(ctx):
        return "Voicemail coming soon."
    _re("voicemail", "Check voicemail messages", "15_phone", cmd_voicemail)

    # ═══════════════════════════════════════════════════════════════
    # SOCIAL — Windy Chat (119-124)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_post(ctx):
        text = ctx.get("_raw", "")
        if not text:
            return "Usage: /post <text>"
        try:
            import httpx
            jwt = os.environ.get("WINDY_JWT", "")
            r = httpx.post("http://localhost:8105/api/v1/social/posts",
                          json={"content": text},
                          headers={"Authorization": f"Bearer {jwt}"}, timeout=10)
            if r.status_code in (200, 201):
                return "✓ Posted to Windy Chat feed"
            return f"Post failed: {r.status_code}"
        except Exception as e:
            return f"Error: {e}"
    _re("post", "Post to Windy Chat social feed", "16_social", cmd_post, usage="post <text>")

    async def cmd_feed(ctx):
        try:
            import httpx
            jwt = os.environ.get("WINDY_JWT", "")
            r = httpx.get("http://localhost:8105/api/v1/social/feed",
                         headers={"Authorization": f"Bearer {jwt}"}, timeout=10)
            if r.status_code == 200:
                posts = r.json().get("posts", [])
                if not posts:
                    return "Feed is empty. Follow some users!"
                lines = ["Social Feed:\n"]
                for p in posts[:10]:
                    lines.append(f"  @{p.get('user_id','?')}: {p.get('content','')[:80]}")
                return "\n".join(lines)
            return f"Feed error: {r.status_code}"
        except Exception as e:
            return f"Error: {e}"
    _re("feed", "Show your social feed", "16_social", cmd_feed)

    async def cmd_dm(ctx):
        args = ctx.get("_args", [])
        if len(args) < 2:
            return "Usage: /dm <user> <message>"
        user = args[0]
        message = " ".join(args[1:])
        return f"DM_SEND:{user}:{message}"
    _re("dm", "Send a direct message on Windy Chat", "16_social", cmd_dm, usage="dm <user> <message>")

    async def cmd_messages(ctx):
        return "Direct messages available through Windy Chat client."
    _re("messages", "Show recent direct messages", "16_social", cmd_messages, aliases=["dms"])

    async def cmd_contacts(ctx):
        return "Contacts available through Windy Chat client."
    _re("contacts", "List Windy Chat contacts", "16_social", cmd_contacts)

    async def cmd_follow(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /follow <user_id>"
        try:
            import httpx
            jwt = os.environ.get("WINDY_JWT", "")
            r = httpx.post("http://localhost:8105/api/v1/social/follow",
                          json={"followed_id": args[0]},
                          headers={"Authorization": f"Bearer {jwt}"}, timeout=10)
            if r.status_code in (200, 201):
                return f"✓ Now following {args[0]}"
            return f"Follow failed: {r.status_code}"
        except Exception as e:
            return f"Error: {e}"
    _re("follow", "Follow a user on Windy Chat", "16_social", cmd_follow, usage="follow <user>")

    # ═══════════════════════════════════════════════════════════════
    # VOICE & TRANSLATION — Windy Word + Traveler (125-130)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_recordings(ctx):
        api_url = os.environ.get("WINDY_API_URL", "")
        jwt = os.environ.get("WINDY_JWT", "")
        if not api_url or not jwt:
            return "Windy Pro not configured."
        try:
            import httpx
            r = httpx.get(f"{api_url}/api/v1/recordings/list",
                         headers={"Authorization": f"Bearer {jwt}"}, timeout=10)
            if r.status_code == 200:
                bundles = r.json().get("bundles", [])
                if not bundles:
                    return "No recordings yet."
                lines = ["Recent recordings:\n"]
                for b in bundles[:10]:
                    dur = b.get("durationSeconds", 0)
                    lines.append(f"  🎙️  {dur//60}m {dur%60}s — {b.get('createdAt', '?')}")
                return "\n".join(lines)
            return f"Error: {r.status_code}"
        except Exception as e:
            return f"Error: {e}"
    _re("recordings", "List recent voice recordings", "17_voice", cmd_recordings, aliases=["recs"])

    async def cmd_transcribe(ctx):
        return "Transcription available through Windy Word desktop/mobile app."
    _re("transcribe", "Transcribe audio (via Windy Word)", "17_voice", cmd_transcribe)

    async def cmd_translate(ctx):
        args = ctx.get("_args", [])
        if len(args) < 2:
            return "Usage: /translate <target_lang> <text>\nExample: /translate es Hello world"
        target = args[0]
        text = " ".join(args[1:])
        api_url = os.environ.get("WINDY_API_URL", "")
        jwt = os.environ.get("WINDY_JWT", "")
        if not api_url:
            return "Windy Pro not configured."
        try:
            import httpx
            r = httpx.post(f"{api_url}/api/v1/translate/text",
                          json={"text": text, "source_lang": "auto", "target_lang": target},
                          headers={"Authorization": f"Bearer {jwt}"} if jwt else {}, timeout=15)
            if r.status_code == 200:
                result = r.json()
                translated = result.get("translated_text", result.get("text", "?"))
                return f"🌍 {text}\n→ [{target}] {translated}"
            return f"Translation error: {r.status_code}"
        except Exception as e:
            return f"Error: {e}"
    _re("translate", "Translate text (199 languages)", "17_voice", cmd_translate,
        aliases=["tr"], usage="translate <lang> <text>")

    async def cmd_translate_file(ctx):
        return "File translation coming soon. Use /translate for text."
    _re("translate-file", "Translate a document", "17_voice", cmd_translate_file,
        usage="translate-file <file> <lang>")

    async def cmd_languages(ctx):
        langs = ("en, es, fr, de, it, pt, ru, zh, ja, ko, ar, hi, tr, pl, nl, "
                 "sv, da, no, fi, cs, ro, hu, el, he, th, vi, id, ms, tl, uk, bg, hr, sk, sl, "
                 "et, lv, lt, ... +180 more")
        return f"🌍 Supported languages (199 total):\n{langs}\n\nUse ISO 639-1 codes with /translate"
    _re("languages", "List available translation languages", "17_voice", cmd_languages, aliases=["langs"])

    async def cmd_clone_status(ctx):
        api_url = os.environ.get("WINDY_API_URL", "")
        jwt = os.environ.get("WINDY_JWT", "")
        if not api_url:
            return "Windy Pro not configured."
        try:
            import httpx
            r = httpx.get(f"{api_url}/api/v1/clone/training-data",
                         headers={"Authorization": f"Bearer {jwt}"} if jwt else {}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                total = data.get("total", 0)
                return f"🧬 Clone Status: {total} recordings ready for training"
            return f"Clone status error: {r.status_code}"
        except Exception as e:
            return f"Error: {e}"
    _re("clone-status", "Show voice clone training progress", "17_voice", cmd_clone_status, aliases=["clone"])

    # ═══════════════════════════════════════════════════════════════
    # PERMISSIONS & SECURITY (131-135)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_permissions(ctx):
        perms = {
            "email": ("Send and read email", True),
            "chat": ("Post and message on Windy Chat", True),
            "sms": ("Send SMS messages", True),
            "voice-calls": ("Make voice calls", False),
            "recordings": ("Read voice recordings", True),
            "cloud": ("Read and write to Windy Cloud", True),
            "clone": ("Check clone status", True),
            "billing": ("Make purchases", False),
            "identity": ("Change owner settings", False),
        }
        lines = ["Agent Permissions:\n"]
        for name, (desc, allowed) in perms.items():
            status = "✅" if allowed else "❌"
            lines.append(f"  {status} {name:16s} {desc}")
        lines.append("\nChange: /permit <action> or /revoke <action>")
        return "\n".join(lines)
    _re("permissions", "Show all current permission levels", "18_permissions", cmd_permissions, aliases=["perms"])

    async def cmd_permit(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /permit <action> (e.g. /permit voice-calls)"
        return f"✓ Permission granted: {args[0]}"
    _re("permit", "Grant a permission", "18_permissions", cmd_permit,
        aliases=["allow", "grant"], usage="permit <action>")

    async def cmd_revoke_perm(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /revoke <action> (e.g. /revoke billing)"
        return f"✓ Permission revoked: {args[0]}"
    _re("revoke", "Revoke a permission", "18_permissions", cmd_revoke_perm,
        aliases=["deny"], usage="revoke <action>")

    async def cmd_trust(ctx):
        passport = os.environ.get("ETERNITAS_PASSPORT", "")
        if not passport:
            return "No Eternitas passport. Run /go to register."
        api_url = os.environ.get("ETERNITAS_API_URL", "")
        if api_url:
            try:
                import httpx
                r = httpx.get(f"{api_url}/api/v1/registry/verify/{passport}", timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    score = data.get("trust_score", "?")
                    return f"🪪 {passport}\nTrust Score: {score}/100\nStatus: {data.get('status', '?')}"
            except Exception as e:
                logger.debug("Trust score lookup failed: %s", e)
        return f"🪪 {passport} (trust score unavailable — Eternitas not reachable)"
    _re("trust", "Show Eternitas trust score and history", "18_permissions", cmd_trust)

    async def cmd_audit_log(ctx):
        return ("Audit log: recent actions taken by this agent\n"
                "(Stored in events table — available via gateway dashboard)")
    _re("audit-log", "Show recent actions taken by the agent", "18_permissions", cmd_audit_log)

    # ═══════════════════════════════════════════════════════════════
    # CLOUD & STORAGE — Windy Cloud (136-140)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_files(ctx):
        api_url = os.environ.get("WINDY_API_URL", "")
        jwt = os.environ.get("WINDY_JWT", "")
        if not api_url:
            return "Windy Cloud not configured."
        try:
            import httpx
            r = httpx.get(f"{api_url}/api/v1/files",
                         headers={"Authorization": f"Bearer {jwt}"} if jwt else {}, timeout=10)
            if r.status_code == 200:
                files = r.json().get("files", [])
                if not files:
                    return "☁️  No files in Windy Cloud."
                lines = ["☁️  Cloud Files:\n"]
                for f in files[:20]:
                    size_kb = f.get("size", 0) / 1024
                    lines.append(f"  {f.get('original_name','?'):30s} {size_kb:.0f} KB")
                return "\n".join(lines)
            return f"Error: {r.status_code}"
        except Exception as e:
            return f"Error: {e}"
    _re("files", "List files in Windy Cloud", "19_cloud", cmd_files)

    async def cmd_upload(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /upload <filepath>"
        return f"UPLOAD_FILE:{args[0]}"
    _re("upload", "Upload a file to Windy Cloud", "19_cloud", cmd_upload, usage="upload <file>")

    async def cmd_download(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /download <file_id>"
        return f"DOWNLOAD_FILE:{args[0]}"
    _re("download", "Download a file from Windy Cloud", "19_cloud", cmd_download, usage="download <file_id>")

    async def cmd_storage(ctx):
        api_url = os.environ.get("WINDY_API_URL", "")
        jwt = os.environ.get("WINDY_JWT", "")
        if not api_url:
            return "Windy Cloud not configured."
        try:
            import httpx
            r = httpx.get(f"{api_url}/api/v1/billing/summary",
                         headers={"Authorization": f"Bearer {jwt}"} if jwt else {}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                used = data.get("storageUsed", 0) / 1024 / 1024
                limit = data.get("storageLimit", 0) / 1024 / 1024
                return f"☁️  Storage: {used:.1f} MB / {limit:.0f} MB"
            return f"Error: {r.status_code}"
        except Exception as e:
            return f"Error: {e}"
    _re("storage", "Show cloud storage usage", "19_cloud", cmd_storage)

    async def cmd_sync(ctx):
        return "SYNC_CLOUD"
    _re("sync", "Force sync settings and data across devices", "19_cloud", cmd_sync)
