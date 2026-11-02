import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from importlib.metadata import version
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import AwareDatetime, BaseModel, Field, StrictInt, ValidationError

from skane._errors import InvalidResponseError, UnavailableError, UsageError

BASE = "https://www.skanetrafiken.se/gw-tps/api"
VERSION = version("skane")
DEADLINE_SECONDS = 30


class Point(BaseModel):
    id2: str
    name: str
    type: str
    area: str | None = None
    lat: float
    lon: float


class Points(BaseModel):
    points: list[Point]


class StopTime(BaseModel):
    name: str
    id2: str | None = None
    time: AwareDatetime
    deviation: StrictInt | None = None
    timeOmitted: bool = False
    pos: str | None = None
    scheduledPos: str | None = None


class Notice(BaseModel):
    text: str


class Line(BaseModel):
    name: str
    type: str
    no: str | None = None
    towards: str | None = None
    distance: str | None = None


class Link(BaseModel):
    id: str | None = None
    origin: StopTime = Field(alias="from")
    destination: StopTime = Field(alias="to")
    line: Line
    canceled: bool = False
    deviations: list[Notice] = []


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
    deviation: StrictInt | None = None
    canceled: bool = False
    timeOmitted: bool = False


class TripStop(BaseModel):
    name: str
    id: str | None = None
    pos: str | None = None
    scheduledPos: str | None = None
    arrival: Timing | None = None
    departure: Timing | None = None


class TripStops(BaseModel):
    stops: list[TripStop]


class ErrorDetail(BaseModel):
    Code: str
    Source: str
    Message: str
    Details: str | None = None


class ErrorEnvelope(BaseModel):
    Error: ErrorDetail


def _error_detail(data: bytes) -> ErrorDetail | None:
    try:
        return ErrorEnvelope.model_validate_json(data).Error
    except ValidationError:
        return None


def _message(path: str, status: int, detail: ErrorDetail | None) -> str:
    if detail is None:
        return f"Skånetrafiken {path} returned HTTP {status}; Message: unavailable; Details: unavailable."
    return f"Skånetrafiken {path} returned HTTP {status} ({detail.Code}): Message: {detail.Message}; Details: {detail.Details or 'none'}."


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise UnavailableError(
            f"Skånetrafiken request exceeded the {DEADLINE_SECONDS}-second deadline; try again later."
        )
    return remaining


def get[T: BaseModel](path: str, params: dict[str, str], model: type[T], deadline: float) -> T:
    url = BASE + path
    if params:
        url += "?" + urlencode(params)
    request = Request(
        url,
        headers={
            "Search-Engine-Environment": "TjP",
            "Accept-Language": "sv-SE",
            "User-Agent": f"skane/{VERSION}",
        },
    )
    for attempt in range(3):
        retry_after = 0.0
        try:
            with urlopen(request, timeout=min(12, _remaining(deadline))) as response:
                data = response.read()
            _remaining(deadline)
            detail = _error_detail(data)
            if detail is not None:
                message = _message(path, 200, detail)
                if detail.Source == "Client":
                    raise UsageError(message)
                raise UnavailableError(message)
            try:
                return model.model_validate_json(data)
            except (ValidationError, ValueError) as exc:
                raise InvalidResponseError(
                    f"Skånetrafiken response for {path} is invalid; the API may have changed and skane needs repair: {str(exc)[:600]}"
                ) from exc
        except HTTPError as exc:
            try:
                detail = _error_detail(exc.read())
                _remaining(deadline)
            except (HTTPException, OSError) as read_error:
                error = str(read_error)
                if attempt < 2:
                    time.sleep(min(random.uniform(0.2, 0.5) * 2**attempt, _remaining(deadline)))
                continue
            message = _message(path, exc.code, detail)
            if exc.code in (404, 410) and path in ("/v2/Points", "/v2/Journey"):
                raise InvalidResponseError(
                    f"{message} The API endpoint may have changed; skane needs repair."
                ) from exc
            if detail and (
                detail.Source == "Client"
                or (
                    path.startswith("/v3/RouteLink/")
                    and detail.Code == "TPS5001"
                    and detail.Details
                    and "400" in detail.Details
                    and "Invalid pattern of serviceJourneyId" in detail.Details
                )
            ):
                raise UsageError(message) from exc
            if exc.code < 500 and exc.code != 429:
                raise UnavailableError(f"{message} Try again later.") from exc
            error = message
            header = exc.headers.get("Retry-After") if exc.headers else None
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
                    raise UnavailableError(f"{message} Retry after {header}.") from exc
        except (URLError, OSError, HTTPException) as exc:
            error = str(exc)
        if attempt < 2:
            time.sleep(
                min(max(retry_after, random.uniform(0.2, 0.5) * 2**attempt), _remaining(deadline))
            )
    _remaining(deadline)
    raise UnavailableError(
        f"Skånetrafiken {path} is unreachable or failing after retries: {error}; try again later."
    )


def points(query: str, deadline: float) -> list[Point]:
    return get("/v2/Points", {"name": query}, Points, deadline).points


def journey(params: dict[str, str], deadline: float) -> Journeys:
    return get("/v2/Journey", params, Journeys, deadline)


def leg_stops(leg_id: str, deadline: float) -> TripStops:
    return get("/v3/RouteLink/" + quote(leg_id, safe=""), {}, TripStops, deadline)
