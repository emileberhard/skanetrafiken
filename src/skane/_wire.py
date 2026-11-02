import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import AwareDatetime, BaseModel, Field, ValidationError

BASE = "https://www.skanetrafiken.se/gw-tps/api"


from skane._errors import InvalidResponseError, UnavailableError


class Point(BaseModel):
    id2: str
    name: str
    type: Literal["STOP_AREA", "ADDRESS", "POI"]
    area: str | None = None
    lat: float
    lon: float


class Points(BaseModel):
    points: list[Point]


class StopTime(BaseModel):
    name: str
    id2: str | None = None
    time: AwareDatetime
    deviation: int | None = None
    pos: str | None = None
    scheduledPos: str | None = None


class Notice(BaseModel):
    text: str


class Line(BaseModel):
    name: str
    type: str
    no: str | None = None
    towards: str | None = None
    color: str | None = None
    iconColor: str | None = None
    distance: str | float | None = None


class Link(BaseModel):
    id: str | None = None
    origin: StopTime = Field(alias="from")
    destination: StopTime = Field(alias="to")
    line: Line
    canceled: bool = False
    deviations: list[Notice] = []
    distance: str | float | None = None


class Journey(BaseModel):
    noOfChanges: int
    routeLinks: list[Link] = Field(min_length=1)
    deviationTag: Notice | None = None


class Journeys(BaseModel):
    journeys: list[Journey]
    nextCursor: str | None = None
    prevCursor: str | None = None


class Timing(BaseModel):
    time: AwareDatetime
    deviation: int | None = None


class TripStop(BaseModel):
    name: str
    id: str | None = None
    pos: str | None = None
    scheduledPos: str | None = None
    arrival: Timing | None = None
    departure: Timing | None = None


class TripStops(BaseModel):
    stops: list[TripStop]
    refreshRateSeconds: int | None = None


def get[T: BaseModel](path: str, params: dict[str, str], model: type[T], version: str) -> T:
    url = BASE + path
    if params:
        url += "?" + urlencode(params)
    request = Request(
        url,
        headers={
            "Search-Engine-Environment": "TjP",
            "Accept-Language": "sv-SE",
            "User-Agent": f"skane/{version}",
        },
    )
    for attempt in range(3):
        retry_after = 0.0
        try:
            with urlopen(request, timeout=12) as response:
                data = response.read()
            try:
                return model.model_validate_json(data)
            except (ValidationError, ValueError) as exc:
                raise InvalidResponseError(
                    f"Skånetrafiken response for {path} is invalid; the API may have changed and skane needs repair: {str(exc)[:600]}",
                ) from exc
        except HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise UnavailableError(
                    f"Skånetrafiken {path} returned HTTP {exc.code}; check the request or try later.",
                ) from exc
            error = exc
            header = exc.headers.get("Retry-After")
            if header:
                try:
                    retry_after = max(0.0, float(header))
                except ValueError:
                    try:
                        retry_after = max(
                            0.0, (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
                        )
                    except (TypeError, ValueError, OverflowError):
                        retry_after = 0.0
                if retry_after > 10:
                    raise UnavailableError(
                        f"Skånetrafiken {path} asks to retry after {header}; try again later."
                    ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            error = exc
        if attempt < 2:
            time.sleep(max(retry_after, random.uniform(0.2, 0.5) * 2**attempt))
    raise UnavailableError(
        f"Skånetrafiken {path} is unreachable or failing after retries: {error}; try again later.",
    )


def points(query: str, version: str) -> list[Point]:
    return get("/v2/Points", {"name": query}, Points, version).points


def journey(params: dict[str, str], version: str) -> Journeys:
    return get("/v2/Journey", params, Journeys, version)


def trip(leg_id: str, version: str) -> TripStops:
    return get("/v3/RouteLink/" + quote(leg_id, safe=""), {}, TripStops, version)
