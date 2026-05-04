from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from livekit import api, rtc
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
logger = logging.getLogger("cold-caller")
logger.setLevel(logging.INFO)

outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID")
cal_api_key = os.getenv("CAL_API_KEY", "").strip('"')
cal_event_type_id = os.getenv("CAL_EVENT_TYPE_ID")

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


class ColdCaller(Agent):
    def __init__(self, *, lead_name: str, dial_info: dict[str, Any]):
        super().__init__(
            instructions=f"""
            You are an AI sales development representative making an outbound call to a lead. Your interface is voice only.

            Your goal: introduce yourself briefly, qualify the lead, and book a 30-minute discovery call on the calendar if they're interested.

            The lead's name is {lead_name}. Be friendly, concise, and respect their time. Speak naturally — short sentences, no jargon.

            Call flow:
            1. Greet them by name and introduce yourself in one sentence
            2. Ask if they have 30 seconds — if not, offer to call back another time and end the call
            3. If yes, briefly state why you're calling (one sentence pitch)
            4. Qualify: ask one question to gauge interest
            5. If interested, offer to book a discovery call. Use look_up_availability to find open slots, then book_meeting once they pick a time and give you their email
            6. If not interested, thank them and end the call gracefully
            7. If they reach voicemail, do NOT leave a long message — use detected_answering_machine

            Always confirm the date, time, and email back to them before calling book_meeting. If anything fails, apologize briefly and offer to follow up by email.
            """
        )
        self.participant: rtc.RemoteParticipant | None = None
        self.dial_info = dial_info
        self.lead_name = lead_name

    def set_participant(self, participant: rtc.RemoteParticipant):
        self.participant = participant

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
        """Book a 30-minute discovery call on the calendar.

        Args:
            start_iso: Meeting start time in ISO 8601 format (e.g., 2026-05-08T15:00:00Z)
            attendee_name: The lead's full name
            attendee_email: The lead's email address
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
        """Called when the user wants to end the call or after a graceful goodbye."""
        logger.info(f"ending call for {self.participant.identity if self.participant else 'unknown'}")
        current_speech = ctx.session.current_speech
        if current_speech:
            await current_speech.wait_for_playout()
        await self.hangup()

    @function_tool()
    async def detected_answering_machine(self, ctx: RunContext):
        """Called when the call reaches voicemail. Hang up immediately — do NOT leave a message."""
        logger.info("voicemail detected, hanging up")
        await self.hangup()


async def entrypoint(ctx: JobContext):
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect()

    dial_info = json.loads(ctx.job.metadata)
    participant_identity = phone_number = dial_info["phone_number"]
    lead_name = dial_info.get("lead_name", "there")

    agent = ColdCaller(lead_name=lead_name, dial_info=dial_info)

    session = AgentSession(
        turn_detection=EnglishModel(),
        vad=silero.VAD.load(),
        stt=deepgram.STT(),
        tts=cartesia.TTS(),
        llm=anthropic.LLM(model="claude-sonnet-4-6"),
    )

    session_started = asyncio.create_task(
        session.start(
            agent=agent,
            room=ctx.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            ),
        )
    )

    try:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=outbound_trunk_id,
                sip_call_to=phone_number,
                participant_identity=participant_identity,
                wait_until_answered=True,
            )
        )
        await session_started
        participant = await ctx.wait_for_participant(identity=participant_identity)
        logger.info(f"participant joined: {participant.identity}")
        agent.set_participant(participant)
        await session.generate_reply(
            instructions=f"Greet {lead_name} by name in one short friendly sentence and ask if they have 30 seconds."
        )
    except api.TwirpError as e:
        logger.error(
            f"SIP error: {e.message} status={e.metadata.get('sip_status_code')} "
            f"{e.metadata.get('sip_status')}"
        )
        ctx.shutdown()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="cold-caller"))
