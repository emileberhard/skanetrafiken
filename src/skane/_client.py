import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from zoneinfo import ZoneInfo

from skane import _wire
from skane._errors import InvalidResponseError, UsageError
from skane._models import (
    Journey,
    Leg,
    Place,
    PlaceRef,
    StopTime,
    Timing,
    Trips,
    VehicleStop,
    VehicleTrip,
)

ZONE = ZoneInfo("Europe/Stockholm")
VERSION = version("skane")


@dataclass(frozen=True, slots=True)
class _Place:
    id: str
    name: str
    type: str


def _local(value: datetime) -> datetime:
    return value.astimezone(ZONE)


def _stop_time(stop: _wire.StopTime) -> StopTime:
    expected = stop.time + timedelta(minutes=stop.deviation) if stop.deviation is not None else None
    return StopTime(
        stop.id2,
        stop.name,
        stop.pos,
        stop.scheduledPos,
        _local(stop.time),
        _local(expected) if expected else None,
        stop.deviation,
    )


def _rgb(value: str | None) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{8}", value):
        raise InvalidResponseError(f"Invalid upstream line colour {value!r}; skane needs repair.")
    return "#" + value[2:].upper()


def _leg(link: _wire.Link) -> Leg:
    line = link.line
    return Leg(
        id=link.id,
        mode="train" if line.type.lower().startswith("train") else line.type.lower(),
        name=line.name,
        line=line.no,
        towards=re.sub(r"^mot\s+", "", line.towards or "", flags=re.IGNORECASE) or None,
        color=_rgb(line.color),
        text_color=_rgb(line.iconColor),
        cancelled=link.canceled,
        notices=tuple(n.text for n in link.deviations),
        origin=_stop_time(link.origin),
        destination=_stop_time(link.destination),
        distance=(link.distance if link.distance is not None else line.distance)
        if line.type.lower() == "walk"
        else None,
    )


def _timing(value: _wire.Timing | None) -> Timing | None:
    if value is None:
        return None
    expected = (
        value.time + timedelta(minutes=value.deviation) if value.deviation is not None else None
    )
    return Timing(_local(value.time), _local(expected) if expected else None, value.deviation)


class Client:
    def stops(self, query: str) -> tuple[Place, ...]:
        return tuple(
            Place(p.id2, p.name, p.type, p.area, p.lat, p.lon) for p in _wire.points(query, VERSION)
        )

    def _resolve(self, query: str) -> _Place:
        if re.fullmatch(r"\d+", query):
            return _Place(query, query, "STOP_AREA")
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", query)
        if match:
            lat, lon = map(float, match.groups())
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise UsageError(f"Invalid coordinates {query!r}.")
            return _Place(f"{lat}#{lon}", query, "LOCATION")
        candidates = self.stops(query)
        exact = [p for p in candidates if p.name.casefold() == query.casefold()]
        if len(exact) != 1:
            choices = "; ".join(f"{p.name} ({p.id}, {p.type}, {p.lat},{p.lon})" for p in candidates)
            raise UsageError(
                f"Place {query!r} does not resolve to exactly one name. Candidates: {choices or 'none'}; choose an exact name, stop id or coordinates."
            )
        p = exact[0]
        return _Place(
            p.id if p.type == "STOP_AREA" else f"{p.lat}#{p.lon}",
            p.name,
            "STOP_AREA" if p.type == "STOP_AREA" else "LOCATION",
        )

    def trips(
        self,
        origin: str,
        destination: str,
        *,
        at: datetime | None = None,
        arrive_by: datetime | None = None,
        line: str | None = None,
        count: int = 5,
    ) -> Trips:
        if count < 1:
            raise UsageError("count must be at least 1.")
        if at is not None and arrive_by is not None:
            raise UsageError("Choose either at or arrive_by.")
        when = arrive_by or at
        if when is not None and (when.tzinfo is None or when.utcoffset() is None):
            raise UsageError("Journey time must include a timezone.")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._resolve, q) for q in (origin, destination)]
            start, end = (future.result() for future in futures)
        params = {
            "fromPointId": start.id,
            "fromPointType": start.type,
            "toPointId": end.id,
            "toPointType": end.type,
            "arrival": str(arrive_by is not None).lower(),
            "priority": "SHORTEST_TIME",
            "journeysAfter": "10",
            "walkSpeed": "NORMAL",
            "maxWalkDistance": "2000",
            "allowWalkToOtherStop": "true",
        }
        boundary = when.astimezone(UTC) if when else datetime.now(UTC)
        if when:
            params["journeyDateTime"] = boundary.isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"
            )
        found: list[Journey] = []
        seen: set[tuple] = set()
        cursors: set[str] = set()
        for _ in range(10):
            page = _wire.journey(params, VERSION)
            for journey in page.journeys:
                links = journey.routeLinks
                transit = [link for link in links if link.line.type.lower() != "walk"]
                if line and (not transit or any(link.line.no != line for link in transit)):
                    continue
                legs = tuple(_leg(link) for link in links)
                departure = legs[0].origin.expected or legs[0].origin.scheduled
                arrival = legs[-1].destination.expected or legs[-1].destination.scheduled
                if arrive_by is not None:
                    if arrival.astimezone(UTC) > boundary:
                        continue
                elif departure.astimezone(UTC) < boundary:
                    continue
                key = tuple((link.id, link.origin.time) for link in links)
                if key in seen:
                    continue
                seen.add(key)
                notices = (journey.deviationTag.text,) if journey.deviationTag else ()
                found.append(Journey(departure, arrival, journey.noOfChanges, notices, legs))
            if len(found) >= count:
                break
            cursor = page.prevCursor if arrive_by is not None else page.nextCursor
            if not cursor or cursor in cursors:
                break
            cursors.add(cursor)
            params["prevCursor" if arrive_by is not None else "nextCursor"] = cursor
        found.sort(key=lambda item: item.departure.astimezone(UTC))
        found = found[-count:] if arrive_by is not None else found[:count]
        first = found[0] if found else None
        start_name = first.legs[0].origin.name if first and start.name == start.id else start.name
        end_name = first.legs[-1].destination.name if first and end.name == end.id else end.name
        return Trips(PlaceRef(start.id, start_name), PlaceRef(end.id, end_name), tuple(found))

    def trip(self, leg_id: str) -> VehicleTrip:
        response = _wire.trip(leg_id, VERSION)
        return VehicleTrip(
            tuple(
                VehicleStop(
                    s.id, s.name, s.pos, s.scheduledPos, _timing(s.arrival), _timing(s.departure)
                )
                for s in response.stops
            ),
            response.refreshRateSeconds,
        )
