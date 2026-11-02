import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from importlib.metadata import version
from typing import Any, Never
from zoneinfo import ZoneInfo

from skt import api

ZONE = ZoneInfo("Europe/Stockholm")
VERSION = version("skanetrafiken-cli")


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise api.SktError(2, f"{message}; see skt --help.")


def parser() -> Parser:
    p = Parser(
        description="Plan Skåne public transport journeys.",
        epilog="Exit codes: 0 success, 2 usage or ambiguous place, 3 API unavailable, 4 upstream response changed. Example: skt trips 'Lund Jupitergatan' 'Malmö Kungsgatan' --line 171 --json",
    )
    p.add_argument("--version", action="version", version=f"skt {VERSION}")
    sub = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    stops = sub.add_parser(
        "stops", help="Find places", epilog="Example: skt stops Jupitergatan --json"
    )
    stops.add_argument("query", metavar="QUERY")
    trips = sub.add_parser(
        "trips",
        help="Plan journeys",
        epilog="Example: skt trips 'Lund C' 'Malmö C' --at '2026-11-03 08:00' --count 3",
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
        epilog="Example: skt trip 'ST¤SE¤276¤ServiceJourney¤…|2026-11-02' --json",
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
        raise api.SktError(
            2,
            f"Invalid time {value!r}: {exc}. Use HH:MM, YYYY-MM-DD HH:MM or ISO 8601 with offset.",
        ) from exc


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    type: str


def resolve(query: str) -> Place:
    if re.fullmatch(r"\d+", query):
        return Place(query, query, "STOP_AREA")
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", query)
    if match:
        lat, lon = map(float, match.groups())
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise api.SktError(2, f"Invalid coordinates {query!r}.")
        return Place(f"{lat}#{lon}", query, "LOCATION")
    candidates = api.points(query, VERSION)
    exact = [p for p in candidates if p.name.casefold() == query.casefold()]
    if len(exact) != 1:
        choices = "; ".join(f"{p.name} ({p.id2}, {p.type}, {p.lat},{p.lon})" for p in candidates)
        raise api.SktError(
            2,
            f"Place {query!r} does not resolve to exactly one name. Candidates: {choices or 'none'}; choose an exact name, stop id or coordinates.",
        )
    p = exact[0]
    return Place(
        p.id2 if p.type == "STOP_AREA" else f"{p.lat}#{p.lon}",
        p.name,
        "STOP_AREA" if p.type == "STOP_AREA" else "LOCATION",
    )


def iso(value: datetime) -> str:
    return value.astimezone(ZONE).isoformat()


def stop_time(stop: api.StopTime) -> dict:
    expected = stop.time + timedelta(minutes=stop.deviation) if stop.deviation is not None else None
    return {
        "id": stop.id2,
        "name": stop.name,
        "platform": stop.pos,
        "scheduledPlatform": stop.scheduledPos,
        "scheduled": iso(stop.time),
        "expected": iso(expected) if expected else None,
        "delayMinutes": stop.deviation,
    }


def rgb(value: str | None) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{8}", value):
        raise api.SktError(4, f"Invalid upstream line colour {value!r}; skt needs repair.")
    return "#" + value[2:].upper()


def leg(link: api.Link) -> dict:
    line = link.line
    result = {
        "id": link.id,
        "mode": "train" if line.type.lower().startswith("train") else line.type.lower(),
        "name": line.name,
        "line": line.no,
        "towards": re.sub(r"^mot\s+", "", line.towards or "", flags=re.IGNORECASE) or None,
        "color": rgb(line.color),
        "textColor": rgb(line.iconColor),
        "cancelled": link.canceled,
        "notices": [n.text for n in link.deviations],
        "from": stop_time(link.origin),
        "to": stop_time(link.destination),
    }
    if line.type.lower() == "walk":
        result["distance"] = link.distance if link.distance is not None else line.distance
    return result


def plan(args: argparse.Namespace) -> dict:
    if args.count < 1:
        raise api.SktError(2, "--count must be at least 1.")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(resolve, q) for q in (args.origin, args.destination)]
        origin, destination = (f.result() for f in futures)
    params = {
        "fromPointId": origin.id,
        "fromPointType": origin.type,
        "toPointId": destination.id,
        "toPointType": destination.type,
        "arrival": str(bool(args.arrive_by)).lower(),
        "priority": "SHORTEST_TIME",
        "journeysAfter": "10",
        "walkSpeed": "NORMAL",
        "maxWalkDistance": "2000",
        "allowWalkToOtherStop": "true",
    }
    when = args.arrive_by or args.at
    if when:
        params["journeyDateTime"] = (
            local_time(when)
            .astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    found: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    cursors: set[str] = set()
    for _ in range(10):
        page = api.journey(params, VERSION)
        for j in page.journeys:
            links = j.routeLinks
            transit = [link for link in links if link.line.type.lower() != "walk"]
            if args.line and (not transit or any(link.line.no != args.line for link in transit)):
                continue
            legs = [leg(link) for link in links]
            key = tuple((link.id, link.origin.time) for link in links)
            if key in seen:
                continue
            seen.add(key)
            notices = [j.deviationTag.text] if j.deviationTag else []
            found.append(
                {
                    "departure": legs[0]["from"]["expected"] or legs[0]["from"]["scheduled"],
                    "arrival": legs[-1]["to"]["expected"] or legs[-1]["to"]["scheduled"],
                    "changes": j.noOfChanges,
                    "notices": notices,
                    "legs": legs,
                }
            )
        if len(found) >= args.count:
            break
        cursor = page.prevCursor if args.arrive_by else page.nextCursor
        if not cursor or cursor in cursors:
            break
        cursors.add(cursor)
        params["prevCursor" if args.arrive_by else "nextCursor"] = cursor
    found.sort(key=lambda item: datetime.fromisoformat(item["departure"]))
    if args.arrive_by:
        found = found[-args.count :]
    else:
        found = found[: args.count]
    first = found[0] if found else None
    return {
        "from": {
            "id": origin.id,
            "name": first["legs"][0]["from"]["name"]
            if first and origin.name == origin.id
            else origin.name,
        },
        "to": {
            "id": destination.id,
            "name": first["legs"][-1]["to"]["name"]
            if first and destination.name == destination.id
            else destination.name,
        },
        "trips": found,
    }


def output(data: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False))
    elif isinstance(data, list):
        for point in data:
            print(
                f"{point['id2']}  {point['name']}  {point['type']}  {point['area'] or '—'}  {point['lat']},{point['lon']}"
            )
    elif isinstance(data, dict) and "trips" in data:
        for item in data["trips"]:
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
    elif isinstance(data, dict):
        for stop in data["stops"]:

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
        if args.command == "stops":
            data = [p.model_dump() for p in api.points(args.query, VERSION)]
        elif args.command == "trips":
            data = plan(args)
        else:
            response = api.trip(args.leg_id, VERSION)

            def timing(t: api.Timing | None) -> dict | None:
                if t is None:
                    return None
                expected = (
                    t.time + timedelta(minutes=t.deviation) if t.deviation is not None else None
                )
                return {
                    "scheduled": iso(t.time),
                    "expected": iso(expected) if expected else None,
                    "delayMinutes": t.deviation,
                }

            data = {
                "stops": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "platform": s.pos,
                        "scheduledPlatform": s.scheduledPos,
                        "arrival": timing(s.arrival),
                        "departure": timing(s.departure),
                    }
                    for s in response.stops
                ],
                "refreshRateSeconds": response.refreshRateSeconds,
            }
        output(data, args.json)
    except api.SktError as exc:
        if wants_json:
            print(
                json.dumps({"error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False)
            )
        else:
            print(f"skt: {exc}", file=sys.stderr)
        sys.exit(exc.code)


if __name__ == "__main__":
    main()
