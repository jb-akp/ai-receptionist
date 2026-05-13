import logging
import os
import textwrap
from datetime import datetime
from typing import Any

import aiohttp
from dotenv import load_dotenv
from livekit import api
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    get_job_context,
    inference,
    room_io,
)
from livekit.plugins import ai_coustics, anthropic, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

load_dotenv(".env.local")

CAL_API_BASE = "https://api.cal.com/v2"
CAL_SLOTS_API_VERSION = "2024-09-04"
CAL_BOOKINGS_API_VERSION = "2024-08-13"


async def _cal_request(
    method: str,
    path: str,
    *,
    api_version: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    api_key = os.environ["CAL_API_KEY"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "cal-api-version": api_version,
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    async with (
        aiohttp.ClientSession() as session,
        session.request(
            method,
            f"{CAL_API_BASE}{path}",
            headers=headers,
            params=params,
            json=json_body,
        ) as resp,
    ):
        return resp.status, await resp.json()


async def _hangup_call() -> None:
    ctx = get_job_context()
    if ctx is None:
        return
    await ctx.api.room.delete_room(api.DeleteRoomRequest(room=ctx.room.name))


class Receptionist(Agent):
    def __init__(self) -> None:
        super().__init__(
            # A Large Language Model (LLM) is your agent's brain, processing user input and generating a response
            # See all available models at https://docs.livekit.io/agents/models/llm/
            llm=anthropic.LLM(model="claude-sonnet-4-6"),
            # To use a realtime model instead of a voice pipeline, replace the LLM
            # with a RealtimeModel and remove the STT/TTS from the AgentSession
            # (Note: This is for the OpenAI Realtime API. For other providers, see https://docs.livekit.io/agents/models/realtime/)
            # 1. Install livekit-agents[openai]
            # 2. Set OPENAI_API_KEY in .env.local
            # 3. Add `from livekit.plugins import openai` to the top of this file
            # 4. Replace the llm argument with:
            #     llm=openai.realtime.RealtimeModel(voice="marin")
            instructions=textwrap.dedent(
                f"""\
                Never use markdown. Do not use asterisks, hashes, dashes as bullets, backticks, or any other formatting symbols. The text-to-speech engine reads them out loud literally. Everything you produce is spoken aloud, so write the way a real receptionist would talk.

                Today is {datetime.now().strftime("%A, %B %d, %Y")}. Default to Pacific time unless the caller says otherwise.


                IDENTITY

                You are James Bradford's voice receptionist. You answer the phone, help callers book a thirty minute meeting with James, and politely steer everything else back to scheduling.

                Your personality is warm, efficient, and a little dry. You sound like a sharp human assistant who has done this a thousand times, not a chatbot. Confident, never gushing. Skip filler like "absolutely", "of course", or "I would be happy to". Do not apologize unless something actually went wrong.


                VOICE RULES

                Speak in plain conversational sentences. No lists, no JSON, no symbols, no markdown of any kind.

                Keep each turn to one or two sentences and ask one question at a time.

                Speak times in plain English, never in ISO format. For example, say "ten thirty in the morning" or "two in the afternoon", not "ten thirty A M" only when needed for clarity, and never "twenty twenty six dash zero five dash eighteen T ten". Mention the day of the week when it helps.

                Spell numbers and phone numbers out naturally. For email addresses, say them the way a person would: the part before the at sign, then "at", then the domain. For example, "jamie dot smith at gmail dot com". When you read an email back to confirm, go a little slower and say each piece clearly, but do not spell every letter unless the caller asks or the address is unusual.

                Never read tool names, parameters, confirmation ids, URLs, or any raw output. Do not invent details about James, his business, or his calendar that the tools have not given you.


                CONVERSATION FLOW AND BOUNDARIES

                Open the call with one short line, for example "Hi, this is James's assistant, how can I help?" Then stop and listen. Do not list your capabilities.

                When the caller wants to book, first figure out what day works for them. Look up availability for that day, then offer exactly two times. Always two. Never three, never the full list, even if more slots came back. Pick a sensible spread, like one earlier in the day and one later, and present them in plain English.

                Make sure that inbetween when the user asks to book and appointment and you asking for a day/time, ask politely what the appointment is for.

                Once they pick a time, collect their full name and email address. Read the email back to confirm it, spoken naturally as described in the voice rules. Then book the meeting and confirm the day and time back to them in plain English. Do not read the confirmation id.

                After the booking is set, ask if there is anything else, and if not, end the call.

                If the caller wants a time that is not available, offer the two closest alternatives without making it a big deal. If they are vague, like "sometime next week", pick a reasonable day and ask "how about Tuesday?" rather than making them do the work. If a tool fails, say you are having trouble reaching the calendar and try once more, and if it fails again offer to take a message and end the call. If they ask something off topic, like about James personally, pricing, or his work history, say you can only help with scheduling and bring it back to that.

                Also, dont say "tomorrow", or attach a relative date, just state the dates
                """
            ),
        )

    @function_tool
    async def check_availability(
        self,
        context: RunContext,
        date: str,
        time_zone: str = "America/Los_Angeles",
    ) -> str:
        """Look up open 30-minute meeting slots on James's calendar for a given date.

        Args:
            date: The date to check, in ISO format YYYY-MM-DD (e.g. "2026-05-12").
            time_zone: IANA time zone name. Defaults to "America/Los_Angeles".
        """
        event_type_id = os.environ["CAL_EVENT_TYPE_ID"]
        logger.info(f"Checking Cal.com availability for {date} ({time_zone})")
        status, body = await _cal_request(
            "GET",
            "/slots",
            api_version=CAL_SLOTS_API_VERSION,
            params={
                "eventTypeId": event_type_id,
                "start": date,
                "end": date,
                "timeZone": time_zone,
            },
        )
        if status != 200 or body.get("status") != "success":
            return f"Availability lookup failed: {body}"

        day_slots = body.get("data", {}).get(date, [])
        if not day_slots:
            return f"No available slots on {date} in {time_zone}."

        times = [slot["start"] for slot in day_slots]
        return f"Available 30-minute slots on {date} in {time_zone}: " + ", ".join(
            times
        )

    @function_tool
    async def book_meeting(
        self,
        context: RunContext,
        start: str,
        attendee_name: str,
        attendee_email: str,
        time_zone: str = "America/Los_Angeles",
        notes: str = "",
    ) -> str:
        """Book a 30-minute meeting on James's calendar at a specific start time.

        Only call this after you have confirmed the slot, the attendee's full name, and their email with the caller.

        Args:
            start: The exact slot start time, as returned by check_availability (ISO 8601 with offset, e.g. "2026-05-12T10:00:00.000-07:00"). UTC ISO strings ending in "Z" are also accepted.
            attendee_name: The caller's full name.
            attendee_email: The caller's email address.
            time_zone: IANA time zone name for the attendee. Defaults to "America/Los_Angeles".
            notes: Optional notes or context for the meeting.
        """
        event_type_id = int(os.environ["CAL_EVENT_TYPE_ID"])
        payload: dict[str, Any] = {
            "start": start,
            "eventTypeId": event_type_id,
            "attendee": {
                "name": attendee_name,
                "email": attendee_email,
                "timeZone": time_zone,
            },
        }
        if notes:
            payload["bookingFieldsResponses"] = {"notes": notes}

        logger.info(f"Booking Cal.com meeting at {start} for {attendee_email}")
        status, body = await _cal_request(
            "POST",
            "/bookings",
            api_version=CAL_BOOKINGS_API_VERSION,
            json_body=payload,
        )
        if status >= 400 or body.get("status") != "success":
            return f"Booking failed: {body}"

        data = body.get("data", {})
        return (
            f"Booking confirmed for {attendee_name} at {data.get('start', start)}. "
            f"Confirmation id: {data.get('uid', 'unknown')}."
        )

    @function_tool
    async def end_call(self, context: RunContext) -> None:
        """End the call after saying goodbye. Use this when the caller is done or asks to hang up."""
        logger.info("Ending call at agent request")
        await context.wait_for_playout()
        await _hangup_call()


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="receptionist")
async def receptionist(ctx: JobContext):
    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Set up a voice AI pipeline using OpenAI, Cartesia, Deepgram, and the LiveKit turn detector
    session = AgentSession(
        # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
        # See all available models at https://docs.livekit.io/agents/models/stt/
        stt=inference.STT(model="deepgram/nova-3", language="multi"),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=inference.TTS(
            model="cartesia/sonic-3", voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
        ),
        # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation=True,
    )

    await ctx.connect()

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=Receptionist(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_S
                ),
            ),
        ),
    )

    await session.generate_reply(
        instructions="Greet the caller saying you are James' assistant, offer help."
    )

    # # Add a virtual avatar to the session, if desired
    # # For other providers, see https://docs.livekit.io/agents/models/avatar/
    # avatar = anam.AvatarSession(
    #     persona_config=anam.PersonaConfig(
    #         name="...",
    #         avatarId="...",  # See https://docs.livekit.io/agents/models/avatar/plugins/anam
    #     ),
    # )
    # # Start the avatar and wait for it to join
    # await avatar.start(session, room=ctx.room)

    # Join the room and connect to the user


if __name__ == "__main__":
    cli.run_app(server)
