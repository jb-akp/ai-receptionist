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
    def __init__(self, *, business_name: str, owner_name: str):
        super().__init__(
            instructions=f"""
You are an AI receptionist answering inbound phone calls for {business_name}, a solo consulting practice run by {owner_name}. Your interface is voice only — every word will be spoken aloud by a TTS system.

CRITICAL VOICE RULES:
- Speak in plain prose. NEVER use markdown formatting — no asterisks, no bullets, no bold, no headings. The TTS reads symbols literally.
- Short sentences. No filler ("umm", "like"). Warm, professional, conversational.
- When confirming an email or phone number, spell it out naturally ("sarah at gmail dot com"), not "the email is colon".

YOUR JOB: greet the caller, find out why they're calling, and either book a 30-minute consultation on {owner_name}'s calendar or take a message.

CALL FLOW:
1. Greet warmly: "Thanks for calling {business_name}, this is the assistant. How can I help?"
2. Listen to why they're calling. Be patient, ask one clarifying question if needed.
3. If they want to talk to {owner_name}, book a meeting, or discuss working together: offer to schedule a 30-minute consultation. Call look_up_availability to find open slots and read 2 or 3 options aloud naturally.
4. Once they pick a time, ask for their full name and email. Confirm both back verbally before booking — "Just to confirm, that's [name] at [email spelled naturally], for [day] at [time]. Sound right?"
5. After they confirm, call book_meeting. Tell them they'll get a calendar invite by email, then use end_call.
6. If they have a quick question that doesn't need {owner_name} directly, answer briefly if you can; otherwise offer to take a message and have {owner_name} follow up.
7. If they're rude, abusive, or clearly a spam call, politely end the call with end_call.
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

    agent = Receptionist(business_name=business_name, owner_name=owner_name)

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
