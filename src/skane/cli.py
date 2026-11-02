import argparse
import json
import os
import re
import sys
from datetime import datetime, time
from importlib.metadata import version
from typing import Any, Never
from zoneinfo import ZoneInfo

from skane import Client, SkaneError, Trips, UsageError, VehicleTrip

ZONE = ZoneInfo("Europe/Stockholm")
VERSION = version("skane")


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise UsageError(f"{message}; see skane --help.")


def parser() -> Parser:
    p = Parser(
        description="Plan Skåne public transport journeys.",
        epilog="Exit codes: 0 success, 2 usage or ambiguous place, 3 API unavailable, 4 upstream response changed. Example: skane trips 'Lund Jupitergatan' 'Malmö Kungsgatan' --line 171 --json",
    )
    p.add_argument("--version", action="version", version=f"skane {VERSION}")
    sub = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    stops = sub.add_parser(
        "stops", help="Find places", epilog="Example: skane stops Jupitergatan --json"
    )
    stops.add_argument("query", metavar="QUERY")
    trips = sub.add_parser(
        "trips",
        help="Plan journeys",
        epilog="Example: skane trips 'Lund C' 'Malmö C' --at '2026-11-03 08:00' --count 3",
    )
    trips.add_argument("origin", metavar="FROM")
    trips.add_argument("destination", metavar="TO")
    times = trips.add_mutually_exclusive_group()
    times.add_argument("--at", metavar="TIME", help="Depart at local time")
    times.add_argument("--arrive-by", metavar="TIME", help="Arrive by local time")
    trips.add_argument("--line", help="Keep trips whose transit legs are all on this line")
    trips.add_argument("--count", type=int, default=5, help="Maximum trips (default 5)")
    trip = sub.add_parser(
        "trip",
        help="Stops of one vehicle trip",
        epilog="Example: skane trip 'ST¤SE¤276¤ServiceJourney¤…|2026-11-02' --json",
    )
    trip.add_argument("leg_id", metavar="LEG_ID")
    for child in (stops, trips, trip):
        child.epilog = (
            (child.epilog or "")
            + " Exit codes: 0 success; 2 usage or ambiguous place; 3 API failure; 4 invalid upstream response."
        )
        child.add_argument("--json", action="store_true", help="Output JSON, including errors")
    return p


def local_time(value: str) -> datetime:
    try:
        if re.fullmatch(r"\d{2}:\d{2}", value):
            parsed = datetime.combine(datetime.now(ZONE).date(), time.fromisoformat(value))
        elif re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?", value
        ):
            parsed = datetime.fromisoformat(value)
        else:
            raise ValueError("unsupported format")
        if parsed.tzinfo is not None:
            return parsed.astimezone(ZONE)
        a = parsed.replace(tzinfo=ZONE, fold=0)
        b = parsed.replace(tzinfo=ZONE, fold=1)
        if a.utcoffset() != b.utcoffset():
            raise ValueError("ambiguous or nonexistent local time; supply an ISO 8601 offset")
        return a
    except ValueError as exc:
        raise UsageError(
            f"Invalid time {value!r}: {exc}. Use HH:MM, YYYY-MM-DD HH:MM or ISO 8601 with offset.",
        ) from exc


def output(data: list[dict] | Trips | VehicleTrip, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False) if isinstance(data, list) else data.to_json())
        return
    rendered: Any = data if isinstance(data, list) else data.to_dict()
    if isinstance(rendered, list):
        for point in rendered:
            print(
                f"{point['id']}  {point['name']}  {point['type']}  {point['area'] or '—'}  {point['lat']},{point['lon']}"
            )
    elif isinstance(rendered, dict) and "trips" in rendered:
        for item in rendered["trips"]:
            parts = []
            for link in item["legs"]:
                start, end = link["from"], link["to"]
                delay = start["delayMinutes"]
                status = (
                    " CANCELLED" if link["cancelled"] else (f" {delay:+d} min" if delay else "")
                )
                if status and sys.stdout.isatty() and "NO_COLOR" not in os.environ:
                    status = f"\033[1;31m{status}\033[0m"
                label = " ".join(str(v) for v in (link["name"], link["line"], link["towards"]) if v)

                def shown_time(stop: dict) -> str:
                    scheduled = datetime.fromisoformat(stop["scheduled"]).strftime("%H:%M")
                    expected = stop["expected"]
                    if expected and expected != stop["scheduled"]:
                        return f"{scheduled}→{datetime.fromisoformat(expected).strftime('%H:%M')}"
                    return scheduled

                parts.append(
                    f"{label} ({start['name']} {start['platform'] or ''} {shown_time(start)} → "
                    f"{end['name']} {end['platform'] or ''} {shown_time(end)}){status}".replace(
                        "  ", " "
                    )
                )
            dep = datetime.fromisoformat(item["departure"]).strftime("%Y-%m-%d %H:%M")
            arr = datetime.fromisoformat(item["arrival"]).strftime("%H:%M")
            notices = item["notices"] + [
                notice for link in item["legs"] for notice in link["notices"]
            ]
            print(
                f"{dep}–{arr}  {' · '.join(parts)}"
                + ("  ⚠ " + "; ".join(notices) if notices else "")
            )
    elif isinstance(rendered, dict):
        for stop in rendered["stops"]:

            def clock(t: dict | None) -> str:
                return (
                    datetime.fromisoformat(t["expected"] or t["scheduled"]).strftime("%H:%M")
                    if t
                    else "—"
                )

            print(
                f"{clock(stop['arrival'])} / {clock(stop['departure'])}  {stop['name']}  {stop['platform'] or ''}"
            )


def main() -> None:
    wants_json = "--json" in sys.argv[1:]
    try:
        args = parser().parse_args()
        client = Client()
        if args.command == "stops":
            data = [p.to_dict() for p in client.stops(args.query)]
        elif args.command == "trips":
            data = client.trips(
                args.origin,
                args.destination,
                at=local_time(args.at) if args.at else None,
                arrive_by=local_time(args.arrive_by) if args.arrive_by else None,
                line=args.line,
                count=args.count,
            )
        else:
            data = client.trip(args.leg_id)
        output(data, args.json)
    except SkaneError as exc:
        if wants_json:
            print(
                json.dumps({"error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False)
            )
        else:
            print(f"skane: {exc}", file=sys.stderr)
        sys.exit(exc.code)


if __name__ == "__main__":
    main()
