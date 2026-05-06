# AI Receptionist — Books Meetings on Cal.com

A LiveKit voice agent that answers inbound phone calls, qualifies the caller, and books real consultations on a Cal.com calendar — all via tool calling with Claude Sonnet 4.6.

**Stack:** LiveKit Agents (incl. native phone numbers) · Claude (Anthropic) · Deepgram (STT) · Cartesia (TTS) · Cal.com API v2

## Quick start

```bash
uv sync
cp .env.example .env.local  # fill in your keys
uv run python agent.py download-files  # one-time model download
uv run python agent.py dev
```

Then call your LiveKit-issued phone number and the agent picks up.

## Required setup

### 1. LiveKit Cloud project
[cloud.livekit.io](https://cloud.livekit.io) → create a project → grab the URL, API key, and API secret for `.env.local`.

### 2. Buy a phone number from LiveKit
Inside your LiveKit Cloud project: **Telephony → Phone numbers → + Buy a number**. Pick any US local area code (~$3–5/mo). LiveKit owns the number and routes calls into your project natively — no Twilio, no Telnyx, no SIP trunk to configure.

After purchase, the dialog asks "Configure with dispatch rules" — leave it open and create the dispatch rule next.

### 3. Dispatch rule
Tells LiveKit: when a call arrives, spin up a fresh room and assign the `receptionist` agent to it.

```bash
lk sip dispatch create dispatch-rule.json
```

Then go back to the LiveKit dashboard's "Configure with dispatch rules" dialog and select the rule you just created. Save.

### 4. Cal.com event-type
1. Create a 30-min event type at [app.cal.com](https://app.cal.com)
2. Get an API key from Settings → Developer → API Keys
3. Find the event-type ID via `GET /v2/event-types?username=<your-username>` (the editor URL number is *not* the API ID)

### 5. Provider keys
- Anthropic: [console.anthropic.com](https://console.anthropic.com)
- Deepgram: [console.deepgram.com](https://console.deepgram.com)
- Cartesia: [play.cartesia.ai](https://play.cartesia.ai)

## What the agent does

Driven by Claude Sonnet 4.6 + the system prompt in `agent.py`:

1. Caller dials your LiveKit-issued number
2. LiveKit's dispatch rule creates a fresh room and assigns the receptionist agent
3. Agent greets: *"Thanks for calling James Bradford Consulting, this is the assistant. How can I help?"*
4. Conversation; if the caller wants a meeting → `look_up_availability()` → offers exactly 2 times
5. On confirmation → `book_meeting()` → real meeting lands on Cal.com
6. Graceful end via `end_call()`

## Tooling for development

- **Playground mode**: dispatch the agent to a LiveKit Agents Playground room with agent name `receptionist` to test over WebRTC (no phone needed)
- **Claude Code skill**: the `livekit-agents` skill is in `.claude/skills/` — invoke with `/livekit-agents` for architectural guidance while editing

## File map

```
ai-receptionist/
├── agent.py             # Receptionist agent + entrypoint
├── pyproject.toml       # uv-managed deps (livekit-plugins-anthropic>=1.2.6 pinned)
├── dispatch-rule.json   # LiveKit SIP dispatch rule (auto-routes to receptionist agent)
├── .env.example         # Template for required env vars
├── .env.local           # Your local secrets (gitignored)
├── .claude/skills/      # LiveKit Agent Skills bundle
└── README.md
```

## Notes

- The `livekit-plugins-anthropic>=1.2.6` pin is required — older versions break with Claude 4.6 due to a prefill regression ([livekit/agents#4907](https://github.com/livekit/agents/issues/4907))
- Cal.com API v2 uses different version headers per endpoint: `2024-09-04` for `/v2/slots`, `2024-08-13` for `/v2/bookings` (handled in `agent.py`)
- LiveKit-native phone numbers ship inbound only at the moment. For outbound (cold calling, callbacks), you'll need to BYO a SIP trunk via Telnyx or Twilio — out of scope for this build.
