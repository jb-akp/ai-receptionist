# AI Receptionist — Books Meetings on Cal.com

A LiveKit voice agent that answers inbound phone calls, qualifies the caller, and books real consultations on a Cal.com calendar — all via tool calling with Claude Sonnet 4.6.

**Stack:** LiveKit Agents · Claude (Anthropic) · Deepgram (STT) · Cartesia (TTS) · Telnyx (SIP trunk) · Cal.com API v2

## Quick start

```bash
uv sync
cp .env.example .env.local  # fill in your keys
uv run python agent.py download-files  # one-time model download
uv run python agent.py dev
```

When inbound is wired up (see below), call your Telnyx number and the agent picks up.

## Required setup

### 1. LiveKit Cloud
Project + API key/secret from [cloud.livekit.io](https://cloud.livekit.io).

### 2. Telnyx number for inbound
1. [portal.telnyx.com](https://portal.telnyx.com) → Voice Suite → buy a US local number
2. Voice Suite → SIP Trunking → create a SIP Connection of type **FQDN**
3. In the connection's outbound settings, add an FQDN destination:
   - **FQDN**: `<your-project>.sip.livekit.cloud` (e.g., `proctor-agent-8hbsep3b.sip.livekit.cloud`)
   - Port: `5060`, Transport: `UDP`
4. Numbers → click your number → set **Connection** to the FQDN connection

### 3. LiveKit-side inbound trunk + dispatch rule
Provided as JSON in this repo. Run once:

```bash
lk sip inbound create inbound-trunk.json
lk sip dispatch create dispatch-rule.json
```

The trunk says "accept inbound for `+16505874840`" and the dispatch rule says "create a fresh room per call and assign the `receptionist` agent to it."

### 4. Cal.com event-type
1. Create a 30-min event type at [app.cal.com](https://app.cal.com)
2. Get an API key from Settings → Developer → API Keys
3. Find the event-type ID via `GET /v2/event-types?username=<your-username>` (the editor URL number is unreliable)

### 5. Provider keys
- Anthropic: [console.anthropic.com](https://console.anthropic.com)
- Deepgram: [console.deepgram.com](https://console.deepgram.com)
- Cartesia: [play.cartesia.ai](https://play.cartesia.ai)

## What the agent does

The agent's call flow (driven by Claude Sonnet 4.6 + the system prompt in `agent.py`):

1. Caller dials your Telnyx number
2. Telnyx routes the call to LiveKit via the FQDN
3. LiveKit's dispatch rule creates a fresh room and assigns the receptionist agent
4. Agent greets immediately: *"Thanks for calling James Bradford Consulting, this is the assistant. How can I help?"*
5. Conversation; if the caller wants to talk to the owner → call `look_up_availability()` → propose times
6. On confirmation → call `book_meeting()` → real meeting lands on Cal.com
7. Graceful end via `end_call()`

## Why inbound, not outbound

Earlier versions of this repo dialed outbound, but LiveKit's SIP bridge currently has an open transcoding bug ([livekit/sip#608](https://github.com/livekit/sip/issues/608)) that causes audio artifacts on outbound calls to cellular numbers. **Inbound is unaffected** — Telnyx's audio reaches LiveKit cleanly. Outbound mode will return when LiveKit ships the fix.

## Tooling for development

- **Claude Code skill**: `livekit-agents` skill is installed in `.claude/skills/` — invoke with `/livekit-agents` for architectural guidance
- **LiveKit Docs MCP**: scoped to this project — Claude Code can search live API docs while editing
- **Playground mode**: dispatch the agent to a LiveKit Agents Playground room with no metadata to test over WebRTC (no phone trunk needed)

## File map

```
ai-receptionist/
├── agent.py             # Receptionist agent + entrypoint
├── pyproject.toml       # uv-managed deps (livekit-plugins-anthropic>=1.2.6 pinned)
├── inbound-trunk.json   # LiveKit inbound trunk config
├── dispatch-rule.json   # LiveKit SIP dispatch rule (auto-routes to receptionist agent)
├── .env.example         # Template for required env vars
├── .env.local           # Your local secrets (gitignored)
├── .claude/skills/      # LiveKit Agent Skills bundle
└── README.md
```

## Notes

- The `livekit-plugins-anthropic>=1.2.6` pin is required — older versions break with Claude 4.6 due to a prefill regression ([livekit/agents#4907](https://github.com/livekit/agents/issues/4907))
- Cal.com API v2 uses different version headers per endpoint: `2024-09-04` for `/v2/slots`, `2024-08-13` for `/v2/bookings` (handled in `agent.py`)
- Trial Telnyx accounts can receive inbound calls fine without an upgrade — the trial restriction is only on outbound destinations
