# AI Cold Caller — Books Meetings on Cal.com

A LiveKit voice agent that makes outbound phone calls, qualifies leads, and books real 30-minute discovery meetings on a Cal.com calendar — all via tool calling with Claude Sonnet 4.6.

**Stack:** LiveKit Agents · Claude (Anthropic) · Deepgram (STT) · Cartesia (TTS) · Telnyx (SIP trunk) · Cal.com API v2

## Quick start

```bash
uv sync
cp .env.example .env.local  # fill in your keys
uv run python agent.py download-files  # one-time model download
uv run python agent.py dev
```

Then dispatch a call:

```bash
lk dispatch create \
  --new-room \
  --agent-name cold-caller \
  --metadata '{"phone_number": "+15551234567", "lead_name": "Sarah"}'
```

## Required setup

### 1. LiveKit Cloud
Project + API key/secret from [cloud.livekit.io](https://cloud.livekit.io).

### 2. Telnyx SIP trunk (outbound)
1. [portal.telnyx.com](https://portal.telnyx.com) → Voice Suite → SIP Trunking → Create SIP Connection (Credentials auth)
2. Buy a US local number under Voice Suite → Phone Numbers
3. Attach number to the connection
4. Add a destination as a **Verified Number** (trial accounts only — required to dial that number)
5. Save the SIP credentials, phone number, and `sip.telnyx.com` SIP address to `.env.local`

### 3. Create the LiveKit outbound trunk
After Telnyx is configured, create the LiveKit-side trunk:

```bash
lk sip outbound create \
  --address sip.telnyx.com \
  --auth-username "$TELNYX_SIP_USERNAME" \
  --auth-password "$TELNYX_SIP_PASSWORD" \
  --number "$TELNYX_PHONE_NUMBER"
```

The command returns a trunk ID — paste it as `SIP_OUTBOUND_TRUNK_ID` in `.env.local`.

### 4. Cal.com event-type
1. Create a 30-min event type at [app.cal.com](https://app.cal.com)
2. Get an API key from Settings → Developer → API Keys
3. Find the event-type ID in the URL when editing (`/event-types/<ID>`)

### 5. Provider keys
- Anthropic: [console.anthropic.com](https://console.anthropic.com)
- Deepgram: [console.deepgram.com](https://console.deepgram.com)
- Cartesia: [play.cartesia.ai](https://play.cartesia.ai)

## What the agent does

The agent's call flow (driven by Claude Sonnet 4.6 + the system prompt in `agent.py`):

1. Greets the lead by name
2. Checks if they have 30 seconds
3. One-sentence pitch
4. Single qualifying question
5. If interested → calls `look_up_availability()` (Cal.com `/v2/slots`), proposes a time
6. On confirmation → calls `book_meeting()` (Cal.com `/v2/bookings`), real meeting lands on the calendar
7. If voicemail → `detected_answering_machine()` → hangs up immediately (no message)
8. Graceful end via `end_call()`

## Tooling for development

- **Claude Code skill**: `livekit-agents` skill is installed in `.claude/skills/` — invoke with `/livekit-agents` for architectural guidance
- **LiveKit Docs MCP**: scoped to this project — Claude Code can search live API docs while editing

## File map

```
cold-caller/
├── agent.py             # Agent definition + entrypoint
├── pyproject.toml       # uv-managed deps (livekit-plugins-anthropic>=1.2.6 pinned)
├── .env.example         # Template for required env vars
├── .env.local           # Your local secrets (gitignored)
├── .claude/skills/      # LiveKit Agent Skills bundle
└── README.md
```

## Notes

- The `livekit-plugins-anthropic>=1.2.6` pin is required — older versions break with Claude 4.6 due to a prefill regression ([livekit/agents#4907](https://github.com/livekit/agents/issues/4907))
- Trial Telnyx accounts can only dial verified numbers — upgrade before recording the public tutorial demo
- Cal.com API v2 requires the header `cal-api-version: 2024-08-13` (handled in `agent.py`)
