# AI Receptionist — Starter Branch

This is the **starter scaffold** for the [Build an AI Phone Receptionist tutorial](https://www.youtube.com/@JamesAkapulu). It's the empty version of the project — you'll build `agent.py` yourself during the video using Claude Code.

> Looking for the finished version? Check out the [`main` branch](https://github.com/jb-akp/ai-receptionist/tree/main) for the reference implementation.

**Stack:** LiveKit Agents (incl. native phone numbers) · Claude (Anthropic) · Deepgram (STT) · Cartesia (TTS) · Cal.com API v2

## Quick start

```bash
git clone -b starter https://github.com/jb-akp/ai-receptionist
cd ai-receptionist
uv sync
cp .env.example .env.local  # fill in your keys
```

Then follow the video tutorial to build `agent.py` with Claude Code, register your dispatch rule, and run the agent.

## What's in this starter

- `pyproject.toml` — uv-managed deps (`livekit-plugins-anthropic>=1.2.6` is pinned for you)
- `dispatch-rule.json` — LiveKit SIP dispatch rule (already configured for `receptionist` agent name)
- `.env.example` — environment variables template (5 provider keys + business name + Cal.com event-type ID)
- `.claude/skills/livekit-agents/` — LiveKit Agent Skills bundle (Claude Code uses this for architectural guidance while building)

What's *not* here on purpose: `agent.py`. You'll create that during the build.

## The 6 build prompts

Copy these into Claude Code in order. Each one builds a specific part of the agent. Walk through what's generated, fix anything off, then move to the next.

### Prompt 1 — Skeleton
```
Create agent.py for a LiveKit voice agent receptionist project.
Set up the skeleton with:
- Imports for livekit.agents, livekit.plugins (anthropic, cartesia, deepgram, silero, turn_detector english), httpx, dotenv
- Load .env.local at the top
- A Receptionist class extending Agent with an empty system prompt for now
- Three @function_tool stubs: look_up_availability, book_meeting, end_call (just signatures with TODO comments)
- An async entrypoint(ctx: JobContext) function that connects to the room
- WorkerOptions with agent_name="receptionist" at the bottom
- Use Claude Sonnet 4.6 for the LLM
```

### Prompt 2 — System prompt
```
Now write the system prompt for the Receptionist class. Structure it with section headers:
- # Identity & Tone — AI receptionist for a solo consulting practice; speak warmly and professionally
- # Context — inject {now_iso} and {today_weekday} as variables so the agent knows what "tomorrow" means
- # Voice Rules — plain prose only, NEVER use markdown formatting (asterisks, bullets, headings) because Cartesia TTS reads symbols literally; speak times naturally ("Thursday morning at nine thirty Pacific" not ISO timestamps); spell emails naturally ("sarah at gmail dot com")
- # Goals — greet caller, figure out what they need, book a 30-minute meeting on Cal.com OR take a message
- # Conversation Flow — greet, listen, look up availability FIRST (never invent times), offer EXACTLY 2 options (never more, phone callers can't remember a list), capture name and email, confirm verbally, call book_meeting, end with end_call
- # Boundaries — never invent times, never promise pricing/deliverables, never pretend to be a human
Use {business_name} and {owner_name} as f-string variables.
```

### Prompt 3 — `look_up_availability`
```
Implement the look_up_availability tool. It should:
- Take an optional days_ahead parameter (default 7)
- Hit Cal.com's GET /v2/slots endpoint with the cal-api-version header set to "2024-09-04"
- Pass eventTypeId from CAL_EVENT_TYPE_ID env var, and start/end dates as ISO strings
- Use httpx with bearer auth from CAL_API_KEY env var
- Return up to 6 available slots flattened from the response
- Wrap in try/except and return a clean error if the API fails
```

### Prompt 4 — `book_meeting`
```
Implement the book_meeting tool. It should:
- Take start_iso, attendee_name, attendee_email parameters
- POST to Cal.com /v2/bookings with cal-api-version header "2024-08-13"  (NOTE: this is a DIFFERENT version header than slots, this is intentional — Cal.com is mid-migration)
- Payload: start (ISO), eventTypeId (from env), attendee object with name, email, timeZone "America/Los_Angeles", language "en"
- Return booking confirmation with the booking ID and meeting URL on success
- Wrap in try/except — return a clean failure dict so the model can gracefully recover
```

### Prompt 5 — `end_call`
```
Implement the end_call tool. It should:
- Be called when the conversation is done after the agent says goodbye
- Use ctx.wait_for_playout() (NOT ctx.session.current_speech.wait_for_playout — that throws a circular wait error inside a tool)
- Then call self.hangup() which deletes the LiveKit room
Add a hangup() method on the Receptionist class that uses get_job_context().api.room.delete_room(...) to clean up.
```

### Prompt 6 — Entrypoint
```
Now flesh out the entrypoint(ctx: JobContext) function:
- After ctx.connect(), parse ctx.job.metadata as JSON if present (default to {} if missing)
- Pull business_name and owner_name from metadata (defaults from env vars BUSINESS_NAME and OWNER_NAME)
- Compute now_iso (formatted like "Wednesday, May 6, 2026 at 9:30 AM PDT") and today_weekday from datetime.now(timezone.utc).astimezone()
- Build the Receptionist with all four fields injected
- Build AgentSession with: turn_detection=EnglishModel(), vad=silero.VAD.load(), stt=deepgram.STT(), tts=cartesia.TTS(), llm=anthropic.LLM(model="claude-sonnet-4-6")
- Use noise_cancellation.BVCTelephony() in RoomInputOptions
- Start the session and call session.generate_reply with a greeting instruction
```

## After building

1. Buy a phone number in your LiveKit Cloud project (Telephony → Phone numbers → + Buy a number)
2. Register the dispatch rule:
   ```bash
   lk sip dispatch create dispatch-rule.json
   ```
3. Attach the dispatch rule to your phone number in the LiveKit dashboard
4. Set up Cal.com — create a 30-min event type, get an API key, fetch the event-type ID via the API:
   ```bash
   curl -H "Authorization: Bearer $CAL_API_KEY" \
     -H "cal-api-version: 2024-06-14" \
     "https://api.cal.com/v2/event-types?username=YOUR_USERNAME"
   ```
5. Fill in the rest of `.env.local`
6. Run:
   ```bash
   uv run python agent.py download-files
   uv run python agent.py dev
   ```
7. Dial your LiveKit phone number — your agent picks up.

## Notes

- The `livekit-plugins-anthropic>=1.2.6` pin is required — older versions break with Claude 4.6 due to a prefill regression ([livekit/agents#4907](https://github.com/livekit/agents/issues/4907))
- Cal.com API v2 uses different version headers per endpoint: `2024-09-04` for `/v2/slots`, `2024-08-13` for `/v2/bookings`
- LiveKit-native phone numbers ship inbound only at the moment. For outbound (cold calling, callbacks), you'll need to BYO a SIP trunk via Telnyx or Twilio — out of scope for this build.
