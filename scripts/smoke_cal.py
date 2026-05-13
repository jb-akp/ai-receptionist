"""Manual smoke test for the Cal.com tools — hits the real API.

Run with: uv run python scripts/smoke_cal.py
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv(".env.local")

from agent import Receptionist, _cal_request, CAL_BOOKINGS_API_VERSION  # noqa: E402


async def main() -> None:
    a = Receptionist()

    # Pick a date 7 days out so we comfortably clear the min-notice window
    from datetime import date, timedelta

    target = (date.today() + timedelta(days=7)).isoformat()

    print(f"\n--- check_availability for {target} ---")
    avail = await a.check_availability.__wrapped__(a, None, target)  # type: ignore[attr-defined]
    print(avail)

    # Pull the first slot out of the availability string
    if "Available" not in avail:
        print("No slots; aborting booking test.")
        return
    first_slot = avail.split(": ", 1)[1].split(", ")[0]

    print(f"\n--- book_meeting at {first_slot} ---")
    result = await a.book_meeting.__wrapped__(  # type: ignore[attr-defined]
        a,
        None,
        first_slot,
        "Smoke Test (please delete)",
        "smoketest+ignore@example.com",
        notes="Automated smoke test — safe to cancel.",
    )
    print(result)

    # Try to cancel the booking we just made, so we don't leave junk on the calendar
    if "Confirmation id: " in result:
        uid = result.split("Confirmation id: ")[1].rstrip(".")
        print(f"\n--- cancel booking {uid} ---")
        status, body = await _cal_request(
            "POST",
            f"/bookings/{uid}/cancel",
            api_version=CAL_BOOKINGS_API_VERSION,
            json_body={"cancellationReason": "smoke test cleanup"},
        )
        print(status, body)


if __name__ == "__main__":
    asyncio.run(main())
