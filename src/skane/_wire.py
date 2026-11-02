import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from importlib.metadata import version
from queue import SimpleQueue
from typing import Annotated
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pydantic import AwareDatetime, BaseModel, BeforeValidator, Field, StrictInt, ValidationError

from skane._errors import InvalidResponseError, SkaneError, UnavailableError, UsageError

BASE = "https://www.skanetrafiken.se/gw-tps/api"
VERSION = version("skane")
DEADLINE_SECONDS = 30
ATTEMPTS = 3
REQUEST_SECONDS = 12
LONGEST_RETRY_AFTER_SECONDS = 10
HEADERS = {
    "Search-Engine-Environment": "TjP",
    "Accept-Language": "sv-SE",
    "User-Agent": f"skane/{VERSION}",
}


def _meters(value: object) -> object:
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(m|km)\s*", value, re.IGNORECASE)
    if match is None:
        raise ValueError("expected a distance such as '903 m' or '1,2 km'")
    meters = Decimal(match.group(1).replace(",", "."))
    if match.group(2).lower() == "km":
        meters *= 1000
    if meters != meters.to_integral_value():
        raise ValueError("expected a whole number of meters")
    return int(meters)


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
    distance: Annotated[int, BeforeValidator(_meters)] | None = None


class Link(BaseModel):
    id: str | None = None
    origin: StopTime = Field(alias="from")
    destination: StopTime = Field(alias="to")
    line: Line
    canceled: bool = False
    deviations: list[Notice]


class Journey(BaseModel):
    noOfChanges: int
    routeLinks: list[Link] = Field(min_length=1)
    deviationTags: list[Notice] = []


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


@dataclass(frozen=True, slots=True)
class _Reply:
    status: int
    body: bytes
    content_type: str
    retry_after: str | None


@dataclass(frozen=True, slots=True)
class _Retry:
    after: float
    reason: str


type _Rule = Callable[[int, ErrorDetail | None, str], SkaneError | None]


def _error_detail(data: bytes) -> ErrorDetail | None:
    try:
        return ErrorEnvelope.model_validate_json(data).Error
    except ValidationError:
        return None


def _message(path: str, status: int, detail: ErrorDetail | None) -> str:
    if detail is None:
        return f"Skånetrafiken {path} returned HTTP {status}."
    return (
        f"Skånetrafiken {path} returned HTTP {status} ({detail.Code}): "
        f"Message: {detail.Message}; Details: {detail.Details or 'none'}."
    )


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise UnavailableError(
            f"Skånetrafiken request exceeded the {DEADLINE_SECONDS}-second deadline; "
            "try again later."
        )
    return remaining


def _fetch(request: Request, timeout: float) -> _Reply:
    replies: SimpleQueue[_Reply | OSError | HTTPException] = SimpleQueue()
    worker = threading.Thread(target=_deliver, args=(request, timeout, replies), daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"no complete response within {timeout:.1f} seconds")
    reply = replies.get_nowait()
    if isinstance(reply, _Reply):
        return reply
    raise reply


def _deliver(
    request: Request, timeout: float, replies: SimpleQueue[_Reply | OSError | HTTPException]
) -> None:
    try:
        replies.put(_open(request, timeout))
    except (OSError, HTTPException) as exc:
        replies.put(exc)


def _open(request: Request, timeout: float) -> _Reply:
    try:
        with urlopen(request, timeout=timeout) as response:
            return _Reply(
                response.status, response.read(), response.headers.get_content_type(), None
            )
    except HTTPError as exc:
        with exc:
            return _Reply(
                exc.code, exc.read(), exc.headers.get_content_type(), exc.headers.get("Retry-After")
            )


def _retry_after(header: str | None, now: datetime) -> float:
    if not header:
        return 0.0
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        return max(0.0, (parsedate_to_datetime(header) - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _outcome[T: BaseModel](
    path: str, reply: _Reply, model: type[T], rule: _Rule, now: datetime
) -> T | _Retry:
    if 200 <= reply.status < 300:
        if reply.content_type != "application/json":
            raise UnavailableError(
                f"Skånetrafiken {path} answered with {reply.content_type} instead of JSON; "
                "try again later."
            )
        try:
            return model.model_validate_json(reply.body)
        except ValidationError as exc:
            detail = _error_detail(reply.body)
            if detail is None:
                raise InvalidResponseError(
                    f"Skånetrafiken response for {path} is invalid; the API may have changed "
                    f"and skane needs repair: {str(exc)[:600]}"
                ) from exc
            error = UsageError if detail.Source == "Client" else UnavailableError
            raise error(_message(path, reply.status, detail)) from None
    detail = _error_detail(reply.body)
    message = _message(path, reply.status, detail)
    if (rejected := rule(reply.status, detail, message)) is not None:
        raise rejected
    if detail is not None and detail.Source == "Client":
        raise UsageError(message)
    if reply.status < 500 and reply.status != 429:
        raise UnavailableError(f"{message} Try again later.")
    wait = _retry_after(reply.retry_after, now)
    if wait > LONGEST_RETRY_AFTER_SECONDS:
        raise UnavailableError(f"{message} Retry after {reply.retry_after}.")
    return _Retry(wait, message)


def _get[T: BaseModel](
    path: str, params: dict[str, str], model: type[T], deadline: float, rule: _Rule
) -> T:
    url = BASE + path + ("?" + urlencode(params) if params else "")
    request = Request(url, headers=HEADERS)
    reason = ""
    for attempt in range(ATTEMPTS):
        wait = 0.0
        try:
            reply = _fetch(request, min(REQUEST_SECONDS, _remaining(deadline)))
        except (OSError, HTTPException) as exc:
            reason = str(exc)
        else:
            _remaining(deadline)
            outcome = _outcome(path, reply, model, rule, datetime.now(UTC))
            if not isinstance(outcome, _Retry):
                return outcome
            wait, reason = outcome.after, outcome.reason
        if attempt < ATTEMPTS - 1:
            backoff = random.uniform(0.2, 0.5) * 2**attempt
            time.sleep(min(max(wait, backoff), _remaining(deadline)))
    _remaining(deadline)
    raise UnavailableError(
        f"Skånetrafiken {path} is unreachable or failing after retries: {reason}; try again later."
    )


def _moved_endpoint(status: int, detail: ErrorDetail | None, message: str) -> SkaneError | None:
    if status in (404, 410):
        return InvalidResponseError(
            f"{message} The API endpoint may have changed; skane needs repair."
        )
    return None


def _malformed_leg_id(status: int, detail: ErrorDetail | None, message: str) -> SkaneError | None:
    if (
        detail is not None
        and detail.Code == "TPS5001"
        and "Invalid pattern of serviceJourneyId" in (detail.Details or "")
    ):
        return UsageError(message)
    return None


def points(query: str, deadline: float) -> list[Point]:
    return _get("/v2/Points", {"name": query}, Points, deadline, _moved_endpoint).points


def journey(params: dict[str, str], deadline: float) -> Journeys:
    return _get("/v2/Journey", params, Journeys, deadline, _moved_endpoint)


def vehicle_trip(leg_id: str, deadline: float) -> TripStops:
    return _get(
        "/v3/RouteLink/" + quote(leg_id, safe=""), {}, TripStops, deadline, _malformed_leg_id
    )
