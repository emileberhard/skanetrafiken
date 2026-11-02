import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, time, timedelta
from typing import Never
from zoneinfo import ZoneInfo

from skane import (
    AmbiguousPlaceError,
    ArriveBy,
    DepartAt,
    InvalidResponseError,
    Place,
    SkaneError,
    StopTime,
    Timing,
    Trips,
    UnavailableError,
    UsageError,
    VehicleTrip,
    leg_stops,
    stops,
    trips,
)
from skane._wire import VERSION

ZONE = ZoneInfo("Europe/Stockholm")


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise UsageError(f"{message}; see skane --help.")


def parser() -> Parser:
    p = Parser(
        description="Plan Skåne public transport journeys.",
        epilog="Exit codes: 0 success, 2 usage or ambiguous place, 3 API unavailable or deadline exceeded, 4 upstream response changed. Example: skane trips 'Lund Jupitergatan' 'Malmö Kungsgatan' --line 171 --json",
    )
    p.add_argument("--version", action="version", version=f"skane {VERSION}")
    sub = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    places = sub.add_parser(
        "stops", help="Find places", epilog="Example: skane stops Jupitergatan --json"
    )
    places.add_argument("query", metavar="QUERY")
    journey = sub.add_parser(
        "trips",
        help="Plan journeys",
        epilog="Example: skane trips 'Lund C' 'Malmö C' --at '2026-11-03 08:00' --count 3",
    )
    journey.add_argument("origin", metavar="FROM")
    journey.add_argument("destination", metavar="TO")
    times = journey.add_mutually_exclusive_group()
    times.add_argument(
        "--at", metavar="TIME", help="Departure in Stockholm time; HH:MM means its next occurrence"
    )
    times.add_argument(
        "--arrive-by",
        metavar="TIME",
        help="Arrival in Stockholm time; HH:MM means its next occurrence",
    )
    journey.add_argument(
        "--line", help="Keep trips whose transit legs all match this line; trains use run numbers"
    )
    journey.add_argument("--count", type=int, default=5, help="Maximum trips (default 5)")
    leg = sub.add_parser(
        "leg",
        help="Stops of one vehicle trip",
        epilog="Example: skane leg 'ST¤SE¤276¤ServiceJourney¤…|2026-11-02' --json",
    )
    leg.add_argument("leg_id", metavar="LEG_ID")
    for child in (places, journey, leg):
        child.epilog = (
            (child.epilog or "")
            + " Exit codes: 0 success; 2 usage or ambiguous place; 3 API failure or deadline exceeded; 4 invalid upstream response."
        )
        child.add_argument("--json", action="store_true", help="Output JSON, including errors")
    return p


def _next_occurrence(value: time, now: datetime) -> datetime:
    current = now.astimezone(UTC)
    day = now.astimezone(ZONE).date()
    for offset in range(3):
        wall = datetime.combine(day + timedelta(days=offset), value)
        choices = []
        for fold in (0, 1):
            candidate = wall.replace(tzinfo=ZONE, fold=fold)
            instant = candidate.astimezone(UTC)
            if instant.astimezone(ZONE).replace(tzinfo=None) == wall and instant >= current:
                choices.append(instant)
        if choices:
            return min(choices).astimezone(ZONE)
    raise UsageError("No valid occurrence of the requested time.")


def local_time(value: str) -> datetime:
    try:
        if re.fullmatch(r"\d{2}:\d{2}", value):
            return _next_occurrence(time.fromisoformat(value), datetime.now(ZONE))
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?", value
        ):
            raise ValueError("unsupported format")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(ZONE)
        a = parsed.replace(tzinfo=ZONE, fold=0)
        b = parsed.replace(tzinfo=ZONE, fold=1)
        if a.utcoffset() != b.utcoffset():
            raise ValueError("ambiguous or nonexistent local time; supply an ISO 8601 offset")
        return a
    except ValueError as exc:
        raise UsageError(
            f"Invalid time {value!r}: {exc}. Use HH:MM, YYYY-MM-DD HH:MM or ISO 8601 with offset."
        ) from exc


def _clock(value: StopTime | Timing | None) -> str:
    if value is None:
        return "—"
    scheduled = value.scheduled.strftime("%H:%M")
    if isinstance(value, Timing) and value.time_omitted:
        return f"--:-- (scheduled {scheduled})"
    if value.delay_minutes is None:
        return f"{scheduled} (scheduled)"
    if value.delay_minutes == 0:
        return f"{scheduled} (on time)"
    expected = value.expected.strftime("%H:%M") if value.expected else "--:--"
    return f"{scheduled}→{expected} ({value.delay_minutes:+d} min)"


def _status(text: str) -> str:
    return (
        f"\033[1;31m{text}\033[0m" if sys.stdout.isatty() and "NO_COLOR" not in os.environ else text
    )


def _places(places: tuple[Place, ...]) -> None:
    for place in places:
        print(
            f"{place.id}  {place.name}  {place.type}  {place.area or '—'}  {place.lat},{place.lon}"
        )


def _trips(result: Trips) -> None:
    for journey in result.trips:
        parts = []
        for leg in journey.legs:
            label = " ".join(str(v) for v in (leg.name, leg.line, leg.towards) if v)
            start = " ".join(
                v for v in (leg.origin.name, leg.origin.platform, _clock(leg.origin)) if v
            )
            end = " ".join(
                v
                for v in (leg.destination.name, leg.destination.platform, _clock(leg.destination))
                if v
            )
            cancelled = " " + _status("CANCELLED") if leg.cancelled else ""
            parts.append(f"{label} ({start} → {end}){cancelled}")
        notices = journey.notices + tuple(notice for leg in journey.legs for notice in leg.notices)
        print(
            f"{journey.departure:%Y-%m-%d %H:%M}–{journey.arrival:%H:%M}  {' · '.join(parts)}"
            + ("  ⚠ " + "; ".join(notices) if notices else "")
        )


def _leg(result: VehicleTrip) -> None:
    for stop in result.stops:
        cancelled = " " + _status("CANCELLED") if stop.cancelled else ""
        print(
            f"{_clock(stop.arrival)} / {_clock(stop.departure)}  {stop.name}  {stop.platform or ''}{cancelled}"
        )


def _report_error(error: SkaneError, as_json: bool) -> Never:
    if isinstance(error, AmbiguousPlaceError):
        choices = "; ".join(f"{p.name} ({p.id}, {p.type})" for p in error.candidates)
        message = f"{error} Candidates: {choices or 'none'}; choose an exact name, stop ID or coordinates."
    else:
        message = str(error)
    code = (
        2 if isinstance(error, UsageError) else 4 if isinstance(error, InvalidResponseError) else 3
    )
    if as_json:
        print(json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False))
    else:
        print(f"skane: {message}", file=sys.stderr)
    sys.exit(code)


def _run() -> None:
    wants_json = "--json" in sys.argv[1:]
    try:
        args = parser().parse_args()
        if args.command == "stops":
            result = stops(args.query)
            if args.json:
                print(json.dumps([place.to_dict() for place in result], ensure_ascii=False))
            else:
                _places(result)
        elif args.command == "trips":
            when = (
                DepartAt(local_time(args.at))
                if args.at
                else ArriveBy(local_time(args.arrive_by))
                if args.arrive_by
                else None
            )
            result = trips(args.origin, args.destination, when, args.line, args.count)
            print(result.to_json()) if args.json else _trips(result)
        else:
            result = leg_stops(args.leg_id)
            print(result.to_json()) if args.json else _leg(result)
    except SkaneError as exc:
        _report_error(exc, wants_json)


def main() -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-c", "from skane.cli import _run; _run()", *sys.argv[1:]],
            timeout=29,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _report_error(
            UnavailableError(
                "Skånetrafiken request exceeded the 30-second command deadline; try again later."
            ),
            "--json" in sys.argv[1:],
        )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
