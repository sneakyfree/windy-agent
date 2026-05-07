#!/usr/bin/env bash
# Register the bot's commands with Telegram via the setMyCommands
# API. After this runs, when a user taps "/" in Telegram they see
# a menu of available commands instead of having to know magic
# words like /reset.
#
# Run once per bot deployment. Idempotent — safe to re-run after
# updating the command list.
#
# Usage:
#   bash scripts/setup-telegram-commands.sh
#
# CATEGORIZATION (PR #139, 2026-05-07):
# Telegram's setMyCommands API gives us a flat list — no native
# category grouping. So we encode categories as the leading emoji
# of each description. Grandma's eye scans down the left column,
# sees clusters of identical emojis, and her gaze lands on the
# right group fast even when squinting.
#
# Order of categories (top to bottom, urgency-weighted):
#   🆘 Rescue        — bot broken / panic
#   💬 Conversation  — flow control
#   💰 Money         — spend control
#   🧠 Memory        — what I know
#   🎭 Personality   — how I behave
#   ℹ  Status        — am I OK?
#   🤖 Model         — which brain
#   🪪 Identity      — who I am
#   👵 Demo          — tour mode
#
# MULTI-LANGUAGE (PR #146, 2026-05-07):
# Telegram's setMyCommands accepts a language_code parameter so
# different localized command sets can be registered. The bot
# client picks based on the user's Telegram language setting. A
# Spanish-speaking grandma whose phone is in Spanish sees
# `🆘 Reiníciame si me trabo o actúo raro` automatically.
#
# Default (English) registered with NO language_code — that's the
# fallback for users whose Telegram language doesn't match any
# explicit registration.
#
# Languages chosen (Telegram-audience-weighted):
#   en (default) — English
#   es           — Spanish (US Latino + LatAm + Spain)
#   pt-BR        — Portuguese, Brazilian (Brazil is one of
#                   Telegram's top markets)
#   ru           — Russian (Telegram's home country, largest
#                   single-country user base)
#
# Out of scope for this round (worthy of a follow-up with native-
# speaker review):
#   hi (Hindi), id (Indonesian), zh (Mandarin — Telegram blocked
#   in mainland China), ar (Arabic — RTL rendering needs care),
#   fr (French)
#
# Telegram caps descriptions at 256 chars; we keep them under 60
# for clean rendering on phones.
#
# Voice messages are handled by the channel adapter automatically;
# no command needed. Install voice support with:
#   pip install windyfly[voice]
# Then restart the bot. Without it, voice notes get a polite "voice
# isn't installed" reply rather than the silent drop pre-PR #129.

set -uo pipefail

ENV_FILE="${WINDY_ENV_FILE:-/home/grantwhitmer/.windy/windy-0.env}"
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    echo "FATAL: TELEGRAM_BOT_TOKEN not set" >&2
    exit 2
fi

# ── ENGLISH (default) ─────────────────────────────────────────────

read -r -d "" COMMANDS_EN <<'JSON' || true
[
  {"command": "reset",     "description": "🆘 Restart me if I'm stuck or acting weird"},
  {"command": "resurrect", "description": "🆘 Save me — switch to a free local model"},
  {"command": "normal",    "description": "🆘 Back to my usual brain after /resurrect"},
  {"command": "autoresurrect", "description": "🆘 Toggle auto-switch to free model on rate limit"},
  {"command": "help",      "description": "🆘 Show what I can do for you"},
  {"command": "health",    "description": "🆘 How am I doing right now?"},

  {"command": "new",       "description": "💬 Start a fresh conversation (memory stays)"},
  {"command": "history",   "description": "💬 Show the last 10 messages"},
  {"command": "summarize", "description": "💬 Summarize this conversation"},
  {"command": "undo",      "description": "💬 Undo the last exchange"},
  {"command": "retry",     "description": "💬 Regenerate the last reply"},
  {"command": "continue",  "description": "💬 Continue if a reply got cut off"},

  {"command": "spend",     "description": "💰 Today's spending by provider"},
  {"command": "pause",     "description": "💰 Stop me from spending money"},
  {"command": "resume",    "description": "💰 Wake me up after a pause"},
  {"command": "yolo",      "description": "💰 Let me cook hard (24h, no auto-pause)"},
  {"command": "yolo24",    "description": "💰 YOLO mode for 24 hours"},
  {"command": "yolo48",    "description": "💰 YOLO mode for 48 hours"},

  {"command": "facts",     "description": "🧠 What I remember about you"},
  {"command": "memory",    "description": "🧠 Memory tools (stats and search)"},
  {"command": "intents",   "description": "🧠 Your active goals and intents"},

  {"command": "mood",      "description": "🎭 What mood I'm picking up from you"},
  {"command": "soul",      "description": "🎭 Show my personality"},
  {"command": "preset",    "description": "🎭 Switch personality preset"},
  {"command": "sliders",   "description": "🎭 Show all personality sliders"},

  {"command": "status",    "description": "ℹ️ Quick status summary"},
  {"command": "version",   "description": "ℹ️ Git SHA, branch, uptime — am I latest?"},
  {"command": "uptime",    "description": "ℹ️ How long I've been running"},
  {"command": "ping",      "description": "ℹ️ Am I responsive?"},
  {"command": "pulse",     "description": "ℹ️ Live runtime diagnostics"},

  {"command": "fast",      "description": "🤖 Switch to my fastest / cheapest model"},
  {"command": "model",     "description": "🤖 Show or switch my LLM"},
  {"command": "tokens",    "description": "🤖 Token usage this session"},

  {"command": "whoami",    "description": "🪪 My identity (passport, role)"},

  {"command": "guest",     "description": "👵 Switch into grandma-mode for a demo"}
]
JSON

# ── ESPAÑOL (es) ──────────────────────────────────────────────────

read -r -d "" COMMANDS_ES <<'JSON' || true
[
  {"command": "reset",     "description": "🆘 Reiníciame si me trabo o actúo raro"},
  {"command": "resurrect", "description": "🆘 Sálvame — cambia a un modelo gratis"},
  {"command": "normal",    "description": "🆘 Volver a mi modelo normal tras /resurrect"},
  {"command": "autoresurrect", "description": "🆘 Activar/desactivar auto-cambio en límite"},
  {"command": "help",      "description": "🆘 Muéstrame lo que puedo hacer"},
  {"command": "health",    "description": "🆘 ¿Cómo estoy ahora mismo?"},

  {"command": "new",       "description": "💬 Empezar conversación nueva (memoria intacta)"},
  {"command": "history",   "description": "💬 Mostrar los últimos 10 mensajes"},
  {"command": "summarize", "description": "💬 Resumir esta conversación"},
  {"command": "undo",      "description": "💬 Deshacer el último intercambio"},
  {"command": "retry",     "description": "💬 Regenerar la última respuesta"},
  {"command": "continue",  "description": "💬 Continuar si la respuesta se cortó"},

  {"command": "spend",     "description": "💰 Gasto de hoy por proveedor"},
  {"command": "pause",     "description": "💰 Para de gastar dinero"},
  {"command": "resume",    "description": "💰 Despiértame tras una pausa"},
  {"command": "yolo",      "description": "💰 Modo intensivo (24h, sin auto-pausa)"},
  {"command": "yolo24",    "description": "💰 Modo YOLO durante 24 horas"},
  {"command": "yolo48",    "description": "💰 Modo YOLO durante 48 horas"},

  {"command": "facts",     "description": "🧠 Lo que recuerdo de ti"},
  {"command": "memory",    "description": "🧠 Herramientas de memoria (estadísticas y búsqueda)"},
  {"command": "intents",   "description": "🧠 Tus objetivos e intenciones activos"},

  {"command": "mood",      "description": "🎭 Qué ánimo estoy detectando en ti"},
  {"command": "soul",      "description": "🎭 Mostrar mi personalidad"},
  {"command": "preset",    "description": "🎭 Cambiar perfil de personalidad"},
  {"command": "sliders",   "description": "🎭 Mostrar todos los ajustes"},

  {"command": "status",    "description": "ℹ️ Resumen rápido de estado"},
  {"command": "version",   "description": "ℹ️ Versión, rama, tiempo activo"},
  {"command": "uptime",    "description": "ℹ️ Cuánto tiempo llevo corriendo"},
  {"command": "ping",      "description": "ℹ️ ¿Estoy respondiendo?"},
  {"command": "pulse",     "description": "ℹ️ Diagnóstico en vivo"},

  {"command": "fast",      "description": "🤖 Cambiar a mi modelo más rápido / barato"},
  {"command": "model",     "description": "🤖 Mostrar o cambiar mi LLM"},
  {"command": "tokens",    "description": "🤖 Uso de tokens en esta sesión"},

  {"command": "whoami",    "description": "🪪 Mi identidad (pasaporte, rol)"},

  {"command": "guest",     "description": "👵 Cambiar a modo abuela (para demos)"}
]
JSON

# ── PORTUGUÊS BRASILEIRO (pt-BR) ──────────────────────────────────

read -r -d "" COMMANDS_PT <<'JSON' || true
[
  {"command": "reset",     "description": "🆘 Me reinicia se eu estiver travado ou estranho"},
  {"command": "resurrect", "description": "🆘 Me salva — troca para um modelo gratuito"},
  {"command": "normal",    "description": "🆘 Voltar ao meu modelo normal após /resurrect"},
  {"command": "autoresurrect", "description": "🆘 Ligar/desligar auto-troca em limite"},
  {"command": "help",      "description": "🆘 Mostre o que posso fazer por você"},
  {"command": "health",    "description": "🆘 Como estou agora?"},

  {"command": "new",       "description": "💬 Começar conversa nova (memória mantida)"},
  {"command": "history",   "description": "💬 Mostrar as últimas 10 mensagens"},
  {"command": "summarize", "description": "💬 Resumir esta conversa"},
  {"command": "undo",      "description": "💬 Desfazer a última troca"},
  {"command": "retry",     "description": "💬 Regenerar a última resposta"},
  {"command": "continue",  "description": "💬 Continuar se a resposta foi cortada"},

  {"command": "spend",     "description": "💰 Gasto de hoje por provedor"},
  {"command": "pause",     "description": "💰 Pare de gastar dinheiro"},
  {"command": "resume",    "description": "💰 Me acorde após uma pausa"},
  {"command": "yolo",      "description": "💰 Modo intenso (24h, sem auto-pausa)"},
  {"command": "yolo24",    "description": "💰 Modo YOLO por 24 horas"},
  {"command": "yolo48",    "description": "💰 Modo YOLO por 48 horas"},

  {"command": "facts",     "description": "🧠 O que eu lembro sobre você"},
  {"command": "memory",    "description": "🧠 Ferramentas de memória"},
  {"command": "intents",   "description": "🧠 Seus objetivos ativos"},

  {"command": "mood",      "description": "🎭 Que humor estou percebendo em você"},
  {"command": "soul",      "description": "🎭 Mostrar minha personalidade"},
  {"command": "preset",    "description": "🎭 Trocar perfil de personalidade"},
  {"command": "sliders",   "description": "🎭 Mostrar todos os ajustes"},

  {"command": "status",    "description": "ℹ️ Resumo rápido de status"},
  {"command": "version",   "description": "ℹ️ Versão, branch, tempo ativo"},
  {"command": "uptime",    "description": "ℹ️ Quanto tempo estou rodando"},
  {"command": "ping",      "description": "ℹ️ Estou respondendo?"},
  {"command": "pulse",     "description": "ℹ️ Diagnóstico ao vivo"},

  {"command": "fast",      "description": "🤖 Trocar para o modelo mais rápido / barato"},
  {"command": "model",     "description": "🤖 Mostrar ou trocar meu LLM"},
  {"command": "tokens",    "description": "🤖 Uso de tokens nesta sessão"},

  {"command": "whoami",    "description": "🪪 Minha identidade (passaporte, papel)"},

  {"command": "guest",     "description": "👵 Modo vovó (para demonstrações)"}
]
JSON

# ── РУССКИЙ (ru) ──────────────────────────────────────────────────

read -r -d "" COMMANDS_RU <<'JSON' || true
[
  {"command": "reset",     "description": "🆘 Перезапустить меня, если я застрял"},
  {"command": "resurrect", "description": "🆘 Спаси меня — переключить на бесплатную модель"},
  {"command": "normal",    "description": "🆘 Вернуться к обычной модели после /resurrect"},
  {"command": "autoresurrect", "description": "🆘 Авто-переключение при превышении лимита"},
  {"command": "help",      "description": "🆘 Что я умею делать"},
  {"command": "health",    "description": "🆘 Как я себя чувствую сейчас?"},

  {"command": "new",       "description": "💬 Начать новый разговор (память сохранится)"},
  {"command": "history",   "description": "💬 Показать последние 10 сообщений"},
  {"command": "summarize", "description": "💬 Подытожить этот разговор"},
  {"command": "undo",      "description": "💬 Отменить последний обмен"},
  {"command": "retry",     "description": "💬 Перегенерировать последний ответ"},
  {"command": "continue",  "description": "💬 Продолжить, если ответ оборвался"},

  {"command": "spend",     "description": "💰 Сегодняшние расходы по провайдеру"},
  {"command": "pause",     "description": "💰 Остановить трату денег"},
  {"command": "resume",    "description": "💰 Разбудить меня после паузы"},
  {"command": "yolo",      "description": "💰 Жгу-мод (24ч, без авто-паузы)"},
  {"command": "yolo24",    "description": "💰 YOLO-режим на 24 часа"},
  {"command": "yolo48",    "description": "💰 YOLO-режим на 48 часов"},

  {"command": "facts",     "description": "🧠 Что я помню о тебе"},
  {"command": "memory",    "description": "🧠 Инструменты памяти"},
  {"command": "intents",   "description": "🧠 Твои активные цели и намерения"},

  {"command": "mood",      "description": "🎭 Какое настроение я улавливаю"},
  {"command": "soul",      "description": "🎭 Показать мою личность"},
  {"command": "preset",    "description": "🎭 Сменить профиль личности"},
  {"command": "sliders",   "description": "🎭 Все настройки личности"},

  {"command": "status",    "description": "ℹ️ Краткая сводка"},
  {"command": "version",   "description": "ℹ️ Версия, ветка, время работы"},
  {"command": "uptime",    "description": "ℹ️ Сколько я уже работаю"},
  {"command": "ping",      "description": "ℹ️ Отвечаю ли я?"},
  {"command": "pulse",     "description": "ℹ️ Диагностика в реальном времени"},

  {"command": "fast",      "description": "🤖 Переключить на быструю / дешёвую модель"},
  {"command": "model",     "description": "🤖 Показать или сменить LLM"},
  {"command": "tokens",    "description": "🤖 Использование токенов в этой сессии"},

  {"command": "whoami",    "description": "🪪 Моя личность (паспорт, роль)"},

  {"command": "guest",     "description": "👵 Режим бабушки (для демо)"}
]
JSON

# ── Register each language ────────────────────────────────────────

# Helper: post a command set to Telegram setMyCommands. First arg
# is the language code (use "" for default). Second arg is the
# JSON string.
_post_commands() {
    local lang="$1"
    local json="$2"
    local body
    if [[ -z "$lang" ]]; then
        body=$(printf '{"commands":%s}' "$json")
    else
        body=$(printf '{"commands":%s,"language_code":"%s"}' "$json" "$lang")
    fi
    local out="/tmp/setmycommands.${lang:-default}.out"
    local code
    code=$(curl -sS -o "$out" -w "%{http_code}" --max-time 15 \
        -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setMyCommands" \
        -H "Content-Type: application/json" \
        --data "$body" 2>/dev/null || echo "000")
    if [[ "$code" != "200" ]] || ! grep -q '"ok":true' "$out"; then
        echo "FAIL [${lang:-default}]: http=$code"
        cat "$out" | head -c 200
        echo
        rm -f "$out"
        return 1
    fi
    rm -f "$out"
    return 0
}

declare -a FAILED=()

if _post_commands "" "$COMMANDS_EN"; then
    echo "✅ default (English) registered"
else
    FAILED+=("default")
fi

if _post_commands "es" "$COMMANDS_ES"; then
    echo "✅ es (Español) registered"
else
    FAILED+=("es")
fi

if _post_commands "pt" "$COMMANDS_PT"; then
    echo "✅ pt (Português) registered"
else
    FAILED+=("pt")
fi

if _post_commands "ru" "$COMMANDS_RU"; then
    echo "✅ ru (Русский) registered"
else
    FAILED+=("ru")
fi

if (( ${#FAILED[@]} > 0 )); then
    echo
    echo "⚠️  Failed: ${FAILED[*]}"
    exit 1
fi

echo
echo "Default (English) menu preview:"
python3 -c "
import json
cmds = json.loads('''$COMMANDS_EN''')
for c in cmds:
    print(f\"  /{c['command']:14}  {c['description']}\")
"
echo
echo "Telegram caches per-language menus — may take ~30 seconds to"
echo "appear in the chat UI. Restart the Telegram app if it does not"
echo "refresh."
echo
echo "A user with their Telegram client set to es / pt / ru sees the"
echo "translated menu automatically. Default (English) shows for any"
echo "other language."
