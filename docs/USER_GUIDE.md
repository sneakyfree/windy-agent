# Windy Fly User Guide

Welcome! This guide covers everything you need to know to use your Windy Fly agent.

---

## Getting Started

### Installation

```bash
pip install windyfly
```

Or from source:

```bash
git clone https://github.com/sneakyfree/windy-agent
cd windy-agent
uv sync
```

### First Setup

Run the setup wizard:

```bash
windy go
```

This will:
1. Ask for your AI provider API key
2. Let you choose a personality preset
3. Name your agent
4. Play the "IT'S ALIVE!" hatch ceremony
5. Open the dashboard in your browser

---

## Talking to Your Agent

### In the Terminal

```bash
windy chat
```

Type naturally. Your agent understands questions, requests, and conversation.

### In the Dashboard

Visit `http://localhost:3000` (opens automatically when you run `windy start`).

The Chat tab lets you talk to your agent from any browser — including your phone.

### Via Windy Chat (Matrix)

If Windy Chat is configured, your agent lives at `@yourfly:chat.windychat.ai`. Message it from Windy Pro desktop or mobile.

---

## What Can Your Agent Do?

### Weather
> "What's the weather in Fort Anne?"
> "Is it going to rain tomorrow?"

Works anywhere in the world. No setup needed.

### Reminders
> "Remind me to take my medicine at 2pm"
> "Set a timer for 20 minutes"
> "Remind me every Monday to call Mom"

Supports: "in X minutes", "at 3pm", "tomorrow at 9am", daily/weekly/monthly repeats.

### To-Do List
> "Add 'buy groceries' to my list"
> "What's on my to-do list?"
> "Mark 'buy groceries' as done"

### News
> "What's the latest news?"
> "Any tech news today?"

### Web Search
> "Search for flights to Miami"
> "Read this article for me: https://..."

### Calculator
> "What's 15% of 230?"
> "Calculate sqrt(144)"

### Unit Conversion
> "How many miles is 10 km?"
> "Convert 72°F to Celsius"

### Calendar (requires Google Calendar setup)
> "What's on my calendar today?"
> "Schedule a meeting tomorrow at 2pm"

Run `windy setup-calendar` to connect Google Calendar.

### Fun
> "Flip a coin"
> "Roll a d20"
> "Pick a random number between 1 and 100"

---

## Personality

Your agent has 10 personality sliders you can adjust:

| Slider | What It Changes |
|--------|----------------|
| **Humor** | How often the agent cracks jokes |
| **Warmth** | Emotional tone — cold and professional to warm and friendly |
| **Formality** | Casual ("hey!") vs formal ("Good afternoon") |
| **Autonomy** | How much the agent does without asking |
| **Verbosity** | Short answers vs detailed explanations |
| **Proactivity** | Does the agent suggest things unprompted? |
| **Reasoning Depth** | Surface-level vs deep analytical thinking |
| **Epistemic Strictness** | How carefully the agent qualifies uncertainty |
| **Creativity** | Conservative vs creative responses |
| **Assertiveness** | Agreeable vs opinionated |

### Presets

- **Companion** — warm, humorous, proactive (great for daily use)
- **Focused** — serious, analytical, precise (great for work)
- **Neutral** — balanced defaults

Adjust in the dashboard (Personality tab) or via:
```bash
windy slider humor 8
windy preset companion
```

---

## The Dashboard

Visit `http://localhost:3000` to manage your agent visually.

### Pages

| Page | What It Shows |
|------|--------------|
| **Home** | Agent status, costs, ecosystem health |
| **Chat** | Real-time conversation |
| **Personality** | Slider controls + presets |
| **Memory** | Search memories, view knowledge graph |
| **Skills** | Learned skills, code view |
| **Identity** | Eternitas passport, contact info |
| **Costs** | Spending charts, budget progress |
| **Settings** | Configuration, ecosystem URLs, danger zone |

---

## Costs & Budget

Your agent tracks every LLM API call. You set a daily budget in `windyfly.toml`:

```toml
[costs]
daily_budget_usd = 5.0
warn_at_usd = 0.50
```

When the budget is reached, the agent tells you and stops making LLM calls until the next day.

Check spending: `windy budget` or the Costs tab in the dashboard.

---

## Cloud Backup

Back up your agent's memory to Windy Cloud:

```bash
windy backup now       # Manual backup
windy backup list      # See all backups
windy backup restore   # Restore from latest
```

Automatic backups happen every 24 hours if `[cloud] auto_backup = true` in your config.

---

## Updating

```bash
windy update           # Check and install latest version
windy version          # See current version + update availability
```

Your agent checks for updates once per day and tells you when a new version is available.

---

## Troubleshooting

### Agent won't start
```bash
windy doctor           # Diagnoses common issues
```

### No response from agent
- Check your API key is set in `.env`
- Check budget hasn't been exceeded: `windy budget`
- Check logs: `windy logs`

### Dashboard won't load
- Ensure gateway is running: `windy status`
- Check port 3000 is free
- Try: `windy restart`

---

## Getting Help

- **Issues**: https://github.com/sneakyfree/windy-agent/issues
- **Status check**: `windy doctor`
- **Ecosystem**: `windy ecosystem`
