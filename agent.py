from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
    get_job_context,
)
from livekit.plugins import (
    anthropic,
    cartesia,
    deepgram,
    noise_cancellation,
    silero,
)
from livekit.plugins.turn_detector.english import EnglishModel

load_dotenv(dotenv_path=".env.local")
logger = logging.getLogger("receptionist")
logger.setLevel(logging.INFO)

cal_api_key = os.getenv("CAL_API_KEY", "").strip('"')
cal_event_type_id = os.getenv("CAL_EVENT_TYPE_ID")
business_name_default = os.getenv("BUSINESS_NAME", "James Bradford Consulting")
owner_name_default = os.getenv("OWNER_NAME", "James")

CAL_API_BASE = "https://api.cal.com/v2"


async def _cal_request(method: str, path: str, *, api_version: str, **kwargs) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {cal_api_key}",
        "cal-api-version": api_version,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.request(method, f"{CAL_API_BASE}{path}", headers=headers, **kwargs)
        resp.raise_for_status()
        return resp.json()


class Receptionist(Agent):
    def __init__(self, *, business_name: str, owner_name: str, now_iso: str, today_weekday: str):
        super().__init__(
            instructions=f"""
# Identity & Tone
You are the AI receptionist for {business_name}, a solo consulting practice run by {owner_name}. You answer inbound phone calls. Speak warmly and professionally with short, natural sentences. Skip filler words ("umm", "like"). Sound like a thoughtful human assistant, not a corporate IVR.

# Context
- Current time: {now_iso} ({today_weekday}). Use this to resolve words like "today", "tomorrow", "next Tuesday", "this afternoon".
- Your interface is voice only — every word you produce is spoken aloud by a TTS system.

# Voice Rules
- Plain prose only. NEVER use markdown — no asterisks, bullets, bold, headings, or symbols. The TTS reads them literally.
- Speak times naturally: "Thursday morning at nine thirty Pacific" — never as ISO timestamps or with seconds.
- Spell emails naturally: "sarah at gmail dot com" — never "sarah colon gmail".
- Spell phone numbers in groups: "six oh five, eight seven four, four eight four oh".
- If you need a moment to look something up, say so briefly: "One sec while I check." Don't go silent.

# Goals
- Greet the caller, figure out what they need.
- If they want to schedule with {owner_name}: book a 30-minute consultation on Cal.com.
- If they have a quick question you can clearly answer: answer briefly.
- Otherwise: take a clean message and end the call.

# Conversation Flow
1. Greet: "Thanks for calling {business_name}, this is the assistant. What can I help you with?"
2. Listen. Ask one clarifying question if their reason isn't clear. Don't interrogate.
3. To schedule: call look_up_availability FIRST. Offer EXACTLY 2 options in one response, in natural speech. Never list more than 2 — phone callers can't remember a list.
4. If neither of the 2 options works, offer 2 more from the same returned set. If the next week is full, ask if a different week works and search again with a wider window.
5. When they pick a time: get full name and email. Confirm both back: "Just to confirm — that's [name], email [spelled naturally], for [day] at [time] Pacific. Sound right?"
6. After they confirm: call book_meeting. Tell them the invite is on the way. Then call end_call.
7. For unrelated quick questions: answer briefly if you can; otherwise offer to take a message and have {owner_name} follow up.
8. For rude or spam callers: politely wrap up and call end_call.

# Boundaries
- Never invent times that didn't come back from look_up_availability.
- Never promise pricing, deliverables, or deadlines {owner_name} hasn't authorized.
- Never pretend to be {owner_name} or a human. If asked, you can say you're the assistant.
"""
        )

    async def hangup(self):
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(api.DeleteRoomRequest(room=job_ctx.room.name))

    @function_tool()
    async def look_up_availability(self, ctx: RunContext, days_ahead: int = 7) -> dict[str, Any]:
        """Find open meeting slots on the calendar in the next N days.

        Args:
            days_ahead: How many days from today to search (default 7)
        """
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=days_ahead)
        params = {
            "eventTypeId": cal_event_type_id,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            data = await _cal_request("GET", "/slots", api_version="2024-09-04", params=params)
            slots_by_day = data.get("data", {})
            available = []
            for date, times in slots_by_day.items():
                for slot in times[:3]:
                    available.append(slot.get("start"))
            return {"available_slots": available[:6]}
        except Exception as e:
            logger.error(f"cal.com slots lookup failed: {e}")
            return {"error": "Could not check the calendar right now"}

    @function_tool()
    async def book_meeting(
        self,
        ctx: RunContext,
        start_iso: str,
        attendee_name: str,
        attendee_email: str,
    ) -> dict[str, Any]:
        """Book a 30-minute consultation on the calendar.

        Args:
            start_iso: Meeting start time in ISO 8601 format (e.g., 2026-05-08T15:00:00Z)
            attendee_name: The caller's full name
            attendee_email: The caller's email address
        """
        payload = {
            "start": start_iso,
            "eventTypeId": int(cal_event_type_id),
            "attendee": {
                "name": attendee_name,
                "email": attendee_email,
                "timeZone": "America/Los_Angeles",
                "language": "en",
            },
        }
        try:
            data = await _cal_request("POST", "/bookings", api_version="2024-08-13", json=payload)
            booking = data.get("data", {})
            logger.info(f"booked: {booking.get('uid')} for {attendee_email} at {start_iso}")
            return {
                "status": "confirmed",
                "booking_id": booking.get("uid"),
                "meeting_url": booking.get("meetingUrl"),
            }
        except httpx.HTTPStatusError as e:
            logger.error(f"cal.com booking failed: {e.response.status_code} {e.response.text}")
            return {"status": "failed", "reason": "calendar rejected the booking"}
        except Exception as e:
            logger.error(f"cal.com booking error: {e}")
            return {"status": "failed", "reason": "calendar unreachable"}

    @function_tool()
    async def end_call(self, ctx: RunContext):
        """Called after a graceful goodbye to end the call."""
        logger.info("ending call")
        await ctx.wait_for_playout()
        await self.hangup()


async def entrypoint(ctx: JobContext):
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect()

    metadata = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    business_name = metadata.get("business_name", business_name_default)
    owner_name = metadata.get("owner_name", owner_name_default)

    now = datetime.now(timezone.utc).astimezone()
    now_iso = now.strftime("%A, %B %d, %Y at %-I:%M %p %Z")
    today_weekday = now.strftime("%A")

    agent = Receptionist(
        business_name=business_name,
        owner_name=owner_name,
        now_iso=now_iso,
        today_weekday=today_weekday,
    )

    session = AgentSession(
        turn_detection=EnglishModel(),
        vad=silero.VAD.load(),
        stt=deepgram.STT(),
        tts=cartesia.TTS(),
        llm=anthropic.LLM(model="claude-sonnet-4-6"),
    )

    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
        ),
    )

    await session.generate_reply(
        instructions=f"Greet the caller warmly: thank them for calling {business_name} and ask how you can help. One short sentence."
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="receptionist"))
