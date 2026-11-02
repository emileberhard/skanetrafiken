import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Never
from zoneinfo import ZoneInfo

from skane import (
    ArriveBy,
    DepartAt,
    InvalidResponseError,
    Place,
    SkaneError,
    StopTime,
    Timing,
    Trips,
    UnavailableError,
    UnresolvedPlaceError,
    UsageError,
    VehicleTrip,
    stops,
    trips,
    vehicle_trip,
)
from skane._models import JSONValue
from skane._wire import DEADLINE_SECONDS, VERSION

ZONE = ZoneInfo("Europe/Stockholm")


@dataclass(frozen=True, slots=True)
class _Failure:
    kind: str
    exit_code: int
    meaning: str


_FAILURES: dict[type[SkaneError], _Failure] = {
    UnresolvedPlaceError: _Failure("unresolved_place", 2, "a place matches no single name"),
    UsageError: _Failure("usage", 2, "invalid arguments"),
    UnavailableError: _Failure(
        "unavailable",
        3,
        f"Skånetrafiken unavailable or the {DEADLINE_SECONDS}-second deadline passed",
    ),
    InvalidResponseError: _Failure(
        "invalid_response", 4, "Skånetrafiken's response changed and skane needs repair"
    ),
}
EXIT_CODES = (
    "Exit codes: 0 success; 1 internal error; "
    + "; ".join(f"{f.exit_code} {f.kind}: {f.meaning}" for f in _FAILURES.values())
    + '. With --json, failures print {"error": {"kind", "message"}} to stdout.'
)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise UsageError(f"{message}; see skane --help.")


def parser() -> Parser:
    p = Parser(
        prog="skane",
        description="Plan Skåne public transport journeys.",
        epilog="Example: skane trips 'Lund Jupitergatan' 'Malmö Kungsgatan' --line 171 --json. "
        + EXIT_CODES,
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
        "--at", metavar="TIME", help="Departure in Stockholm time; HH:MM means today, even if past"
    )
    times.add_argument(
        "--arrive-by",
        metavar="TIME",
        help="Arrival in Stockholm time; HH:MM means today, even if past",
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
        child.epilog = f"{child.epilog}. {EXIT_CODES}"
        child.add_argument("--json", action="store_true", help="Output JSON, including errors")
    return p


def local_time(value: str, today: date) -> datetime:
    try:
        if re.fullmatch(r"\d{2}:\d{2}", value):
            parsed = datetime.combine(today, time.fromisoformat(value))
        elif not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?", value
        ):
            raise ValueError("unsupported format")
        else:
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
    if value.time_omitted:
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
    failure = next(f for kind, f in _FAILURES.items() if isinstance(error, kind))
    if as_json:
        report: dict[str, JSONValue] = {"kind": failure.kind, "message": str(error)}
        if isinstance(error, UnresolvedPlaceError):
            report["candidates"] = [place.to_dict() for place in error.candidates]
        print(json.dumps({"error": report}, ensure_ascii=False))
    elif isinstance(error, UnresolvedPlaceError):
        choices = "; ".join(f"{p.name} ({p.id}, {p.type})" for p in error.candidates)
        print(f"skane: {error} Candidates: {choices or 'none'}.", file=sys.stderr)
    else:
        print(f"skane: {error}", file=sys.stderr)
    sys.exit(failure.exit_code)


def _run(args: argparse.Namespace) -> None:
    if args.command == "stops":
        places = stops(args.query)
        if args.json:
            print(json.dumps({"places": [p.to_dict() for p in places]}, ensure_ascii=False))
        else:
            _places(places)
    elif args.command == "trips":
        today = datetime.now(ZONE).date()
        when = (
            DepartAt(local_time(args.at, today))
            if args.at
            else ArriveBy(local_time(args.arrive_by, today))
            if args.arrive_by
            else None
        )
        journeys = trips(args.origin, args.destination, when, line=args.line, count=args.count)
        if args.json:
            print(journeys.to_json())
        else:
            _trips(journeys)
    else:
        vehicle = vehicle_trip(args.leg_id)
        if args.json:
            print(vehicle.to_json())
        else:
            _leg(vehicle)


def main() -> None:
    wants_json = "--json" in sys.argv[1:]
    try:
        _run(parser().parse_args())
    except SkaneError as exc:
        _report_error(exc, wants_json)


if __name__ == "__main__":
    main()
