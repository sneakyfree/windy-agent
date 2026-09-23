#!/usr/bin/env bash
# windy-deliver.sh — shared delivery for Windy 0's scheduled messages
# (red-alarm, weekly brief, evening recap, QA brief).
#
# Sourced, not executed:  source windy-deliver.sh; windy_deliver <name> <text> [always|fallback]
#
# Telegram used to be the ONLY channel. When @Windy_0_bot was frozen
# (2026-09-17 → 23) every one of these failed with http=401 and the
# red-alarm could not fire for six days, silently. Now:
#   1. Telegram (Markdown) to $OWNER_ID with $TELEGRAM_BOT_TOKEN.
#   2. Email via Resend to $WINDY_ALERT_EMAIL when Telegram fails
#      ("fallback", the default), or every time ("always", used by the
#      red-alarm). Needs WINDY_ALERT_EMAIL plus a Resend key:
#      WINDY_ALERT_RESEND_KEY if set (kept separate so alerts don't depend on
#      the agent's own mail setup), else RESEND_API_KEY. Optional
#      WINDY_ALERT_FROM (default "Windy 0 <noreply@windyword.ai>").
#   3. A status file ${WINDY_ALERT_STATUS_DIR:-~/.windy/alerts}/<name>.status
#      (ts, result, telegram, email) that windy-uptime can read. result is
#      "delivered" if ANY channel succeeded, else "failed".
# Returns 0 if any channel delivered, 1 otherwise. Never prints secrets.

windy_deliver() {
    local name="$1" text="$2" mode="${3:-fallback}"
    local tg="skipped" em="skipped" out
    out="$(mktemp)"
    if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${OWNER_ID:-}" ]]; then
        tg=$(curl -sS -o "$out" -w "%{http_code}" --max-time 15 \
            -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${OWNER_ID}" \
            --data-urlencode "parse_mode=Markdown" \
            --data-urlencode "text=${text}" 2>/dev/null || echo "000")
        [[ "$tg" == "200" ]] || logger -t "$name" \
            "telegram delivery failed: http=$tg body=$(head -c 160 "$out" 2>/dev/null)"
    fi
    if [[ "$mode" == "always" || "$tg" != "200" ]]; then
        local rkey="${WINDY_ALERT_RESEND_KEY:-${RESEND_API_KEY:-}}"
        if [[ -n "$rkey" && -n "${WINDY_ALERT_EMAIL:-}" ]]; then
            local subject
            subject="[Windy 0] ${name#windy-}: $(printf '%s' "$text" | head -1 | tr -d '*_`' | cut -c1-80)"
            em=$(WINDY_DELIVER_KEY="$rkey" python3 - "$text" "$subject" <<'PY' 2>/dev/null || echo "000"
import html, json, os, sys, urllib.request, urllib.error
text, subject = sys.argv[1], sys.argv[2]
body = {"from": os.environ.get("WINDY_ALERT_FROM", "Windy 0 <noreply@windyword.ai>"),
        "to": [os.environ["WINDY_ALERT_EMAIL"]], "subject": subject,
        "text": text, "html": "<pre style='font-family:inherit'>" + html.escape(text) + "</pre>"}
req = urllib.request.Request("https://api.resend.com/emails", data=json.dumps(body).encode(),
      headers={"Authorization": "Bearer " + os.environ["WINDY_DELIVER_KEY"], "Content-Type": "application/json",
               "User-Agent": "windy-deliver"}, method="POST")
try:
    print(urllib.request.urlopen(req, timeout=20).status)
except urllib.error.HTTPError as e:
    print(e.code)
PY
)
            [[ "$em" =~ ^2 ]] || logger -t "$name" "email delivery failed: http=$em"
        else
            em="unconfigured"
            [[ "$tg" == "200" ]] || logger -t "$name" \
                "no second channel: set WINDY_ALERT_EMAIL + WINDY_ALERT_RESEND_KEY"
        fi
    fi
    rm -f "$out"
    local result="failed"
    [[ "$tg" == "200" || "$em" =~ ^2 ]] && result="delivered"
    local dir="${WINDY_ALERT_STATUS_DIR:-$HOME/.windy/alerts}"
    mkdir -p "$dir"
    printf 'ts=%s\nresult=%s\ntelegram=%s\nemail=%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$result" "$tg" "$em" > "$dir/$name.status.tmp" \
        && mv "$dir/$name.status.tmp" "$dir/$name.status"
    logger -t "$name" "delivery: result=$result telegram=$tg email=$em"
    [[ "$result" == "delivered" ]]
}
