import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

JSON_NAMES = {
    "origin": "from",
    "destination": "to",
    "scheduled_platform": "scheduledPlatform",
    "delay_minutes": "delayMinutes",
    "distance_meters": "distanceMeters",
    "time_omitted": "timeOmitted",
}


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {JSON_NAMES.get(key, key): _json_value(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class Place(_Serializable):
    id: str
    name: str
    type: str
    area: str | None
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class PlaceRef(_Serializable):
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class StopTime(_Serializable):
    id: str | None
    name: str
    platform: str | None
    scheduled_platform: str | None
    scheduled: datetime
    expected: datetime | None
    delay_minutes: int | None


@dataclass(frozen=True, slots=True)
class Leg(_Serializable):
    id: str | None
    mode: str
    name: str
    line: str | None
    towards: str | None
    cancelled: bool
    notices: tuple[str, ...]
    origin: StopTime
    destination: StopTime
    distance_meters: int | None


@dataclass(frozen=True, slots=True)
class Trip(_Serializable):
    departure: datetime
    arrival: datetime
    changes: int
    notices: tuple[str, ...]
    legs: tuple[Leg, ...]


@dataclass(frozen=True, slots=True)
class Trips(_Serializable):
    origin: PlaceRef
    destination: PlaceRef
    trips: tuple[Trip, ...]


@dataclass(frozen=True, slots=True)
class Timing(_Serializable):
    scheduled: datetime
    expected: datetime | None
    delay_minutes: int | None
    time_omitted: bool


@dataclass(frozen=True, slots=True)
class VehicleStop(_Serializable):
    id: str | None
    name: str
    platform: str | None
    scheduled_platform: str | None
    arrival: Timing | None
    departure: Timing | None
    cancelled: bool


@dataclass(frozen=True, slots=True)
class VehicleTrip(_Serializable):
    stops: tuple[VehicleStop, ...]


@dataclass(frozen=True, slots=True)
class DepartAt:
    time: datetime


@dataclass(frozen=True, slots=True)
class ArriveBy:
    time: datetime
