import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from skane import _wire
from skane._errors import AmbiguousPlaceError, InvalidResponseError, UsageError
from skane._models import (
    ArriveBy,
    DepartAt,
    Leg,
    Place,
    PlaceRef,
    StopTime,
    Timing,
    Trip,
    Trips,
    VehicleStop,
    VehicleTrip,
)

ZONE = ZoneInfo("Europe/Stockholm")


@dataclass(frozen=True, slots=True)
class _Endpoint:
    id: str
    name: str | None
    wire_id: str
    wire_type: Literal["STOP_AREA", "LOCATION"]


def _coordinates(lat: float, lon: float) -> str:
    return f"{lat},{lon}"


def _stop_area(id: str, name: str | None) -> _Endpoint:
    return _Endpoint(id, name, id, "STOP_AREA")


def _location(lat: float, lon: float, name: str | None) -> _Endpoint:
    return _Endpoint(_coordinates(lat, lon), name, f"{lat}#{lon}", "LOCATION")


def stops(query: str) -> tuple[Place, ...]:
    return _stops(query, time.monotonic() + _wire.DEADLINE_SECONDS)


def _stops(query: str, deadline: float) -> tuple[Place, ...]:
    return tuple(
        Place(
            p.id2 if p.type == "STOP_AREA" else _coordinates(p.lat, p.lon),
            p.name,
            p.type,
            p.area,
            p.lat,
            p.lon,
        )
        for p in _wire.points(query, deadline)
    )


def _resolve(query: str | Place, deadline: float) -> _Endpoint:
    if isinstance(query, Place):
        if query.type == "STOP_AREA":
            return _stop_area(query.id, query.name)
        return _location(query.lat, query.lon, query.name)
    if re.fullmatch(r"\d+", query):
        return _stop_area(query, None)
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", query)
    if match:
        lat, lon = map(float, match.groups())
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise UsageError(f"Invalid coordinates {query!r}.")
        return _location(lat, lon, None)
    candidates = _stops(query, deadline)
    exact = [p for p in candidates if p.name.casefold() == query.casefold()]
    if len(exact) != 1:
        raise AmbiguousPlaceError(query, candidates)
    return _resolve(exact[0], deadline)


def _stop_time(stop: _wire.StopTime) -> StopTime:
    deviation = None if stop.timeOmitted else stop.deviation
    expected = stop.time + timedelta(minutes=deviation) if deviation is not None else None
    return StopTime(
        stop.id2,
        stop.name,
        stop.pos,
        stop.scheduledPos,
        stop.time.astimezone(ZONE),
        expected.astimezone(ZONE) if expected else None,
        deviation,
        stop.timeOmitted,
    )


def _distance_meters(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(m|km)\s*", value, re.IGNORECASE)
    if match:
        try:
            distance = Decimal(match.group(1).replace(",", ".")) * (
                1000 if match.group(2).lower() == "km" else 1
            )
            if distance == distance.to_integral_value():
                return int(distance)
        except InvalidOperation:
            pass
    raise InvalidResponseError(f"Invalid upstream walk distance {value!r}; skane needs repair.")


def _leg(link: _wire.Link) -> Leg:
    line = link.line
    mode = "train" if line.type.lower().startswith("train") else line.type.lower()
    return Leg(
        id=link.id,
        mode=mode,
        name=line.name,
        line=line.no,
        towards=re.sub(r"^mot\s+", "", line.towards or "", flags=re.IGNORECASE) or None,
        cancelled=link.canceled,
        notices=tuple(n.text for n in link.deviations),
        origin=_stop_time(link.origin),
        destination=_stop_time(link.destination),
        distance_meters=_distance_meters(line.distance) if mode == "walk" else None,
    )


def _timing(value: _wire.Timing | None) -> Timing | None:
    if value is None:
        return None
    deviation = None if value.timeOmitted else value.deviation
    expected = value.time + timedelta(minutes=deviation) if deviation is not None else None
    return Timing(
        value.time.astimezone(ZONE),
        expected.astimezone(ZONE) if expected else None,
        deviation,
        value.timeOmitted,
    )


def trips(
    origin: str | Place,
    destination: str | Place,
    when: DepartAt | ArriveBy | None = None,
    *,
    line: str | None = None,
    count: int = 5,
) -> Trips:
    deadline = time.monotonic() + _wire.DEADLINE_SECONDS
    if count < 1:
        raise UsageError("count must be at least 1.")
    moment = when.time if when is not None else None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_resolve, q, deadline) for q in (origin, destination)]
        start, end = (future.result() for future in futures)
    arrive_by = isinstance(when, ArriveBy)
    params = {
        "fromPointId": start.wire_id,
        "fromPointType": start.wire_type,
        "toPointId": end.wire_id,
        "toPointType": end.wire_type,
        "arrival": str(arrive_by).lower(),
        "priority": "SHORTEST_TIME",
        "journeysAfter": "10",
        "walkSpeed": "NORMAL",
        "maxWalkDistance": "2000",
        "allowWalkToOtherStop": "true",
    }
    boundary = moment.astimezone(UTC) if moment else datetime.now(UTC)
    if moment:
        params["journeyDateTime"] = boundary.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
    found: list[Trip] = []
    seen: set[tuple[tuple[str | None, datetime], ...]] = set()
    cursors: set[str] = set()
    for _ in range(10):
        page = _wire.journey(params, deadline)
        for journey in page.journeys:
            links = journey.routeLinks
            transit = [link for link in links if link.line.type.lower() != "walk"]
            if line and (not transit or any(link.line.no != line for link in transit)):
                continue
            legs = tuple(_leg(link) for link in links)
            departure = legs[0].origin.expected or legs[0].origin.scheduled
            arrival = legs[-1].destination.expected or legs[-1].destination.scheduled
            if (arrive_by and arrival > boundary) or (not arrive_by and departure < boundary):
                continue
            key = tuple((link.id, link.origin.time) for link in links)
            if key in seen:
                continue
            seen.add(key)
            notices = tuple(tag.text for tag in journey.deviationTags)
            found.append(Trip(departure, arrival, journey.noOfChanges, notices, legs))
        if len(found) >= count:
            break
        cursor = page.prevCursor if arrive_by else page.nextCursor
        if not cursor or cursor in cursors:
            break
        cursors.add(cursor)
        params["prevCursor" if arrive_by else "nextCursor"] = cursor
    found.sort(key=lambda item: item.departure.timestamp())
    found = found[-count:] if arrive_by else found[:count]
    first = found[0] if found else None
    return Trips(
        _place_ref(start, first.legs[0].origin if first else None),
        _place_ref(end, first.legs[-1].destination if first else None),
        tuple(found),
    )


def _place_ref(end: _Endpoint, stop: StopTime | None) -> PlaceRef:
    if end.name is None and end.wire_type == "STOP_AREA" and stop is not None:
        return PlaceRef(end.id, stop.name)
    return PlaceRef(end.id, end.name)


def _vehicle_stop(stop: _wire.TripStop) -> VehicleStop:
    return VehicleStop(
        stop.id,
        stop.name,
        stop.pos,
        stop.scheduledPos,
        _timing(stop.arrival),
        _timing(stop.departure),
        any(t.canceled for t in (stop.arrival, stop.departure) if t),
    )


def leg_stops(leg_id: str) -> VehicleTrip:
    response = _wire.leg_stops(leg_id, time.monotonic() + _wire.DEADLINE_SECONDS)
    return VehicleTrip(tuple(_vehicle_stop(stop) for stop in response.stops))
