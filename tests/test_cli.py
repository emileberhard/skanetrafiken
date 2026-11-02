import io
import itertools
import json
import sys
import threading
from datetime import UTC, date, datetime
from email.message import Message
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.response import addinfourl
from zoneinfo import ZoneInfo

import pytest

import skane
from skane import _wire, cli

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["skane", *args])
    cli.main()
    return json.loads(capsys.readouterr().out)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(_wire, "urlopen", reject)


def ok(value, url, content_type="application/json; charset=utf-8"):
    headers = Message()
    headers["Content-Type"] = content_type
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    return addinfourl(io.BytesIO(body), headers, url, 200)


def http_error(url, status, **headers):
    message = Message()
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    return HTTPError(url, status, "error", message, io.BytesIO(b""))


def respond(monkeypatch, response):
    def open_fixture(request, timeout):
        value = response(request.full_url) if callable(response) else response
        return ok(value, request.full_url)

    monkeypatch.setattr(_wire, "urlopen", open_fixture)


def fail(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["skane", *args, "--json"])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    return exited.value.code, json.loads(capsys.readouterr().out)["error"]


def test_bus_json_contract_and_filter(monkeypatch, capsys):
    respond(monkeypatch, fixture("journey-bus.json"))
    result = invoke(
        monkeypatch,
        capsys,
        "trips",
        "9021012081116000",
        "9021012080117000",
        "--line",
        "171",
        "--count",
        "1",
        "--at",
        "2026-11-02T00:00+01:00",
        "--json",
    )
    trip = result["trips"][0]
    leg = trip["legs"][0]
    assert set(result) == {"from", "to", "trips"}
    assert set(trip) == {"departure", "arrival", "changes", "notices", "legs"}
    assert set(leg) == {
        "id",
        "mode",
        "name",
        "line",
        "towards",
        "cancelled",
        "notices",
        "from",
        "to",
        "distanceMeters",
    }
    assert set(leg["from"]) == {
        "id",
        "name",
        "platform",
        "scheduledPlatform",
        "scheduled",
        "expected",
        "delayMinutes",
        "timeOmitted",
    }
    assert result["from"] == {"id": "9021012081116000", "name": "Lund Jupitergatan"}
    assert trip["departure"] == "2026-11-02T06:09:00+01:00"
    assert trip["arrival"] == "2026-11-02T06:26:00+01:00"
    assert (leg["mode"], leg["line"], leg["towards"]) == ("bus", "171", "Malmö Södervärn")
    assert leg["from"]["expected"] is None
    assert leg["from"]["delayMinutes"] is None


def test_leg_realtime_and_omitted_time(monkeypatch, capsys):
    raw = fixture("route-realtime.json")
    raw["stops"][0]["departure"]["timeOmitted"] = True
    respond(monkeypatch, raw)
    result = invoke(monkeypatch, capsys, "leg", "leg-id", "--json")
    first, second = result["stops"][:2]
    assert first["departure"] == {
        "scheduled": "2026-11-02T02:21:00+01:00",
        "expected": None,
        "delayMinutes": None,
        "timeOmitted": True,
    }
    assert second["arrival"]["expected"] == "2026-11-02T02:26:00+01:00"
    assert second["arrival"]["delayMinutes"] == -1
    monkeypatch.setattr(sys, "argv", ["skane", "leg", "leg-id"])
    cli.main()
    assert "--:-- (scheduled 02:21)" in capsys.readouterr().out


def test_recorded_cancellations_mark_only_the_cancelled_section(monkeypatch, capsys):
    respond(monkeypatch, fixture("journey-cancelled.json"))
    result = invoke(
        monkeypatch,
        capsys,
        "trips",
        "9021012086239000",
        "9021012091069000",
        "--at",
        "2026-11-02 05:00",
        "--json",
    )
    assert [trip["legs"][0]["cancelled"] for trip in result["trips"]] == [False, True]
    respond(monkeypatch, fixture("route-cancelled.json"))
    stops = {
        stop["name"]: stop for stop in invoke(monkeypatch, capsys, "leg", "1753", "--json")["stops"]
    }
    assert stops["Ystad station"]["cancelled"] is False
    assert stops["Köpingebro station"]["cancelled"] is True
    assert stops["Köpingebro station"]["arrival"]["delayMinutes"] is None
    assert stops["Simrishamn station"]["cancelled"] is True
    monkeypatch.setattr(sys, "argv", ["skane", "leg", "1753"])
    cli.main()
    rendered = capsys.readouterr().out.splitlines()
    assert [line.endswith("CANCELLED") for line in rendered].count(True) == 4


def test_on_time_and_timetable_are_distinct_in_human_output(monkeypatch, capsys):
    raw = fixture("journey-bus.json")
    respond(monkeypatch, raw)
    args = ["skane", "trips", "1", "2", "--at", "2026-11-02 00:00", "--count", "1"]
    monkeypatch.setattr(sys, "argv", args)
    cli.main()
    assert "06:09 (scheduled)" in capsys.readouterr().out
    raw["journeys"][0]["routeLinks"][0]["from"]["deviation"] = 0
    cli.main()
    assert "06:09 (on time)" in capsys.readouterr().out


def test_unresolved_place_has_structured_candidates_and_cli_exit_2(monkeypatch, capsys):
    respond(monkeypatch, fixture("points.json"))
    with pytest.raises(skane.UnresolvedPlaceError) as caught:
        skane.trips("Jupitergatan", "123", skane.DepartAt(datetime(2026, 11, 2, tzinfo=UTC)))
    assert caught.value.query == "Jupitergatan"
    assert any(
        p.name == "Lund Jupitergatan" and p.id == "9021012081116000"
        for p in caught.value.candidates
    )
    monkeypatch.setattr(
        sys, "argv", ["skane", "trips", "Jupitergatan", "123", "--at", "2026-11-02 00:00", "--json"]
    )
    with pytest.raises(SystemExit, match="2"):
        cli.main()
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["kind"] == "unresolved_place"
    assert {
        "id": "9021012081116000",
        "name": "Lund Jupitergatan",
        "type": "STOP_AREA",
    }.items() <= next(c for c in error["candidates"] if c["id"] == "9021012081116000").items()


def test_returned_places_are_reusable(monkeypatch, capsys):
    raw = fixture("points.json")
    address = next(p for p in raw["points"] if p["type"] == "ADDRESS")
    address["type"] = "NEW_PLACE_TYPE"
    respond(monkeypatch, raw)
    places = skane.stops("Jupitergatan")
    chosen = next(p for p in places if p.type == "NEW_PLACE_TYPE")
    assert chosen.id == f"{chosen.lat},{chosen.lon}"
    assert any(p.id == "9021012081116000" for p in places)
    response = fixture("journey-walk.json")
    requests = []

    def journey(url):
        requests.append(parse_qs(urlparse(url).query))
        return response

    respond(monkeypatch, journey)
    when = skane.DepartAt(datetime(2026, 11, 2, 0, 0, tzinfo=UTC))
    result = skane.trips(chosen, "9021012081216000", when, count=1)
    assert requests[0]["fromPointType"] == ["LOCATION"]
    assert requests[0]["fromPointId"] == [f"{chosen.lat}#{chosen.lon}"]
    assert result.origin == skane.PlaceRef(chosen.id, chosen.name)
    requests.clear()
    again = skane.trips(result.origin.id, "9021012081216000", when, count=1)
    assert requests[0]["fromPointType"] == ["LOCATION"]
    assert requests[0]["fromPointId"] == [f"{chosen.lat}#{chosen.lon}"]
    assert again.origin == skane.PlaceRef(chosen.id, None)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("from", {"name": "Lund", "time": "2026-11-02T05:09:00Z", "deviation": False}),
        ("line", {"name": "Gång", "type": "Walk", "distance": "about a kilometre"}),
        ("deviations", None),
    ],
)
def test_invalid_upstream_leg_exits_4(monkeypatch, capsys, field, value):
    raw = fixture("journey-bus.json")
    link = raw["journeys"][0]["routeLinks"][0]
    if value is None:
        del link[field]
    else:
        link[field] = value
    respond(monkeypatch, raw)
    monkeypatch.setattr(
        sys, "argv", ["skane", "trips", "123", "456", "--at", "2026-11-02 00:00", "--json"]
    )
    with pytest.raises(SystemExit, match="4"):
        cli.main()
    assert json.loads(capsys.readouterr().out)["error"]["kind"] == "invalid_response"


def test_line_filter_pages_and_deduplicates(monkeypatch, capsys):
    bus = fixture("journey-bus.json")
    first = dict(bus, journeys=[bus["journeys"][0], bus["journeys"][0]], nextCursor="page-two")
    second = dict(bus, journeys=bus["journeys"], nextCursor=None)

    def page(url):
        return second if parse_qs(urlparse(url).query).get("nextCursor") else first

    respond(monkeypatch, page)
    result = invoke(
        monkeypatch,
        capsys,
        "trips",
        "1",
        "2",
        "--line",
        "171",
        "--count",
        "2",
        "--at",
        "2026-11-02T00:00+01:00",
        "--json",
    )
    assert [t["departure"] for t in result["trips"]] == [
        "2026-11-02T06:09:00+01:00",
        "2026-11-02T06:17:00+01:00",
    ]


def test_arrive_by_pages_backwards(monkeypatch, capsys):
    bus = fixture("journey-bus.json")
    first = dict(bus, journeys=bus["journeys"][1:], prevCursor="earlier")
    earlier = dict(bus, journeys=bus["journeys"][:1], prevCursor=None)

    def page(url):
        return earlier if parse_qs(urlparse(url).query).get("prevCursor") else first

    respond(monkeypatch, page)
    result = invoke(
        monkeypatch,
        capsys,
        "trips",
        "1",
        "2",
        "--arrive-by",
        "2026-11-02 06:35",
        "--count",
        "2",
        "--json",
    )
    assert [t["departure"] for t in result["trips"]] == [
        "2026-11-02T06:09:00+01:00",
        "2026-11-02T06:17:00+01:00",
    ]


def test_dst_and_bare_time_means_today():
    today = date(2026, 11, 2)
    assert cli.local_time("08:00", today).isoformat() == "2026-11-02T08:00:00+01:00"
    assert cli.local_time("2026-07-01 08:00", today).isoformat().endswith("+02:00")
    assert cli.local_time("2026-12-01 08:00", today).isoformat().endswith("+01:00")
    with pytest.raises(skane.UsageError):
        cli.local_time("2026-10-25 02:30", today)
    with pytest.raises(skane.UsageError):
        cli.local_time("2026-03-29 02:30", today)


def test_walk_distance_and_train_mode(monkeypatch, capsys):
    raw = fixture("journey-walk.json")
    respond(monkeypatch, raw)
    result = invoke(
        monkeypatch,
        capsys,
        "trips",
        "55.603,13.003",
        "9021012081216000",
        "--at",
        "2026-11-02 03:00",
        "--count",
        "1",
        "--json",
    )
    assert result["from"] == {"id": "55.603,13.003", "name": None}
    assert result["to"] == {"id": "9021012081216000", "name": "Lund C"}
    walk, train = result["trips"][0]["legs"]
    assert (walk["mode"], walk["distanceMeters"], walk["line"]) == ("walk", 903, None)
    assert train["mode"] == "train"
    walk_line = raw["journeys"][0]["routeLinks"][0]["line"]
    walk_line["distance"] = "1,2 km"
    result = invoke(
        monkeypatch, capsys, "trips", "1", "2", "--at", "2026-11-02 03:00", "--count", "1", "--json"
    )
    assert result["trips"][0]["legs"][0]["distanceMeters"] == 1200
    del walk_line["distance"]
    result = invoke(
        monkeypatch, capsys, "trips", "1", "2", "--at", "2026-11-02 03:00", "--count", "1", "--json"
    )
    assert result["trips"][0]["legs"][0]["distanceMeters"] is None


def test_journey_preserves_live_delay_without_inventing_departure(monkeypatch, capsys):
    respond(monkeypatch, fixture("journey-realtime.json"))
    result = invoke(
        monkeypatch,
        capsys,
        "trips",
        "9021012080000000",
        "9021012045007000",
        "--count",
        "1",
        "--at",
        "2026-11-02T00:00+01:00",
        "--json",
    )
    trip = result["trips"][0]
    leg = trip["legs"][0]
    assert leg["from"]["expected"] is None
    assert leg["to"]["scheduled"] == "2026-11-02T03:15:00+01:00"
    assert leg["to"]["expected"] == trip["arrival"] == "2026-11-02T03:10:00+01:00"
    assert leg["to"]["delayMinutes"] == -5


def test_error_envelopes_and_fixed_endpoint_404(monkeypatch, capsys):
    calls = []

    def fail(request, timeout):
        calls.append(request.full_url)
        is_leg = "/RouteLink/" in request.full_url
        body = fixture("error-leg.json" if is_leg else "error-client.json")
        raise HTTPError(
            request.full_url,
            502 if is_leg else 400,
            "error",
            Message(),
            io.BytesIO(json.dumps(body).encode()),
        )

    monkeypatch.setattr(_wire, "urlopen", fail)
    for args, message in [
        (
            ["trips", "123", "456", "--at", "2026-11-02 00:00", "--json"],
            "Departure point could not be found",
        ),
        (["leg", "bad", "--json"], "Invalid pattern of serviceJourneyId"),
    ]:
        calls.clear()
        monkeypatch.setattr(sys, "argv", ["skane", *args])
        with pytest.raises(SystemExit, match="2"):
            cli.main()
        error = json.loads(capsys.readouterr().out)["error"]
        assert error["kind"] == "usage"
        assert message in error["message"]
        assert "Message:" in error["message"]
        assert "Details:" in error["message"]
        assert len(calls) == 1

    def missing(request, timeout):
        raise HTTPError(request.full_url, 404, "Not Found", Message(), io.BytesIO(b""))

    monkeypatch.setattr(_wire, "urlopen", missing)
    monkeypatch.setattr(sys, "argv", ["skane", "stops", "Lund", "--json"])
    with pytest.raises(SystemExit, match="4"):
        cli.main()
    assert json.loads(capsys.readouterr().out)["error"]["kind"] == "invalid_response"


def test_incomplete_read_retries_then_json_exit_3(monkeypatch, capsys):
    calls = []

    def unavailable(request, timeout):
        calls.append(request.full_url)
        raise IncompleteRead(b"partial")

    monkeypatch.setattr(_wire, "urlopen", unavailable)
    monkeypatch.setattr(_wire.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sys, "argv", ["skane", "stops", "Lund", "--json"])
    with pytest.raises(SystemExit, match="3"):
        cli.main()
    assert len(calls) == 3
    assert json.loads(capsys.readouterr().out)["error"]["kind"] == "unavailable"


def test_deadline_covers_pagination_and_retry(monkeypatch, capsys):
    clock = [0.0]
    timeouts = []
    response = {"journeys": [], "nextCursor": "next"}

    def open_page(request, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 3:
            clock[0] += 9.75
            raise TimeoutError("connection timed out")
        clock[0] += 10
        response["nextCursor"] = str(len(timeouts))
        return ok(response, request.full_url)

    monkeypatch.setattr(_wire.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        _wire.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(_wire.random, "uniform", lambda low, high: 1.0)
    monkeypatch.setattr(_wire, "urlopen", open_page)
    monkeypatch.setattr(
        sys, "argv", ["skane", "trips", "1", "2", "--at", "2026-11-02 00:00", "--json"]
    )
    with pytest.raises(SystemExit, match="3"):
        cli.main()
    assert timeouts == [12, 12, 10]
    assert clock[0] == 30
    assert json.loads(capsys.readouterr().out)["error"]["kind"] == "unavailable"


def test_past_explicit_departure_and_aware_input(monkeypatch):
    respond(monkeypatch, fixture("journey-realtime.json"))
    when = skane.DepartAt(datetime(2026, 11, 2, 2, 0, tzinfo=ZoneInfo("Europe/Stockholm")))
    result = skane.trips("9021012080000000", "9021012045007000", when, count=1)
    assert result.trips[0].departure.isoformat() == "2026-11-02T02:35:00+01:00"
    for choice in (skane.DepartAt, skane.ArriveBy):
        with pytest.raises(skane.UsageError, match="timezone"):
            choice(datetime(2026, 11, 2, 8, 0))


def test_mixed_line_trip_is_excluded(monkeypatch, capsys):
    bus = fixture("journey-bus.json")
    bus["journeys"] = bus["journeys"][:1]
    first_leg = bus["journeys"][0]["routeLinks"][0]
    second_leg = dict(first_leg, line=dict(first_leg["line"], no="5"))
    bus["journeys"][0]["routeLinks"].append(second_leg)
    bus["nextCursor"] = None
    respond(monkeypatch, bus)
    result = invoke(
        monkeypatch,
        capsys,
        "trips",
        "1",
        "2",
        "--line",
        "171",
        "--at",
        "2026-11-02 00:00",
        "--json",
    )
    assert result["trips"] == []


def test_leg_human_output_distinguishes_zero_from_missing(monkeypatch, capsys):
    raw = fixture("route-realtime.json")
    respond(monkeypatch, raw)
    monkeypatch.setattr(sys, "argv", ["skane", "leg", "leg-id"])
    cli.main()
    assert "02:21 (on time)" in capsys.readouterr().out
    del raw["stops"][0]["departure"]["deviation"]
    cli.main()
    assert "02:21 (scheduled)" in capsys.readouterr().out


def test_leg_boolean_delay_is_invalid(monkeypatch, capsys):
    raw = fixture("route-realtime.json")
    raw["stops"][0]["departure"]["deviation"] = True
    respond(monkeypatch, raw)
    monkeypatch.setattr(sys, "argv", ["skane", "leg", "leg-id", "--json"])
    with pytest.raises(SystemExit, match="4"):
        cli.main()
    assert json.loads(capsys.readouterr().out)["error"]["kind"] == "invalid_response"


def test_omitted_journey_stop_time_is_unknown(monkeypatch, capsys):
    raw = fixture("journey-bus.json")
    raw["journeys"][0]["routeLinks"][0]["from"].update(deviation=0, timeOmitted=True)
    respond(monkeypatch, raw)
    result = invoke(
        monkeypatch, capsys, "trips", "1", "2", "--at", "2026-11-02 00:00", "--count", "1", "--json"
    )
    stop = result["trips"][0]["legs"][0]["from"]
    assert stop["timeOmitted"] is True
    assert stop["expected"] is None
    assert stop["delayMinutes"] is None
    monkeypatch.setattr(
        sys, "argv", ["skane", "trips", "1", "2", "--at", "2026-11-02 00:00", "--count", "1"]
    )
    cli.main()
    assert "--:-- (scheduled 06:09)" in capsys.readouterr().out


def test_trip_notices_list_every_deviation_tag(monkeypatch, capsys):
    respond(monkeypatch, fixture("journey-cancelled.json"))
    result = invoke(
        monkeypatch,
        capsys,
        "trips",
        "9021012086239000",
        "9021012091069000",
        "--at",
        "2026-11-02 05:00",
        "--json",
    )
    bus, train = result["trips"]
    assert bus["notices"] == ["1 ANMÄRKNING"]
    assert bus["legs"][0]["notices"][0].startswith("Turen går skoldagar.")
    assert train["notices"] == ["INSTÄLLD"]
    assert train["legs"][0]["notices"][0].startswith("Tåget är inställt Ystad station")


def test_retry_after_paces_the_next_attempt(monkeypatch, capsys):
    calls, sleeps = [], []

    def throttled_once(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise http_error(request.full_url, 429, Retry_After="2")
        return ok(fixture("points.json"), request.full_url)

    monkeypatch.setattr(_wire, "urlopen", throttled_once)
    monkeypatch.setattr(_wire.time, "sleep", sleeps.append)
    result = invoke(monkeypatch, capsys, "stops", "Jupitergatan", "--json")
    assert len(calls) == 2
    assert sleeps == [2.0]
    assert any(place["id"] == "9021012081116000" for place in result["places"])


@pytest.mark.parametrize("retry_after", ["30", "Fri, 01 Jan 2100 00:00:00 GMT"])
def test_distant_retry_after_fails_without_waiting(monkeypatch, capsys, retry_after):
    calls = []

    def throttled(request, timeout):
        calls.append(request.full_url)
        raise http_error(request.full_url, 503, Retry_After=retry_after)

    monkeypatch.setattr(_wire, "urlopen", throttled)
    code, error = fail(monkeypatch, capsys, "stops", "Lund")
    assert (code, len(calls)) == (3, 1)
    assert f"Retry after {retry_after}." in error["message"]


def test_server_errors_retry_until_attempts_run_out(monkeypatch, capsys):
    calls, sleeps = [], []

    def failing(request, timeout):
        calls.append(request.full_url)
        raise http_error(request.full_url, 502)

    monkeypatch.setattr(_wire, "urlopen", failing)
    monkeypatch.setattr(_wire.time, "sleep", sleeps.append)
    code, error = fail(monkeypatch, capsys, "stops", "Lund")
    assert (code, len(calls), len(sleeps)) == (3, 3, 2)
    assert "returned HTTP 502" in error["message"]


def test_non_json_success_is_unavailable_not_a_changed_api(monkeypatch, capsys):
    monkeypatch.setattr(
        _wire, "urlopen", lambda request, timeout: ok(b"<html>", request.full_url, "text/html")
    )
    code, error = fail(monkeypatch, capsys, "stops", "Lund")
    assert code == 3
    assert "text/html instead of JSON" in error["message"]


def test_stalled_request_is_abandoned_at_the_deadline(monkeypatch, capsys):
    released = threading.Event()
    calls = []

    def stall(request, timeout):
        calls.append(timeout)
        released.wait()
        raise OSError("released after the test")

    ticks = itertools.chain([0.0], itertools.repeat(29.95))
    monkeypatch.setattr(_wire.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(_wire.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(_wire, "urlopen", stall)
    try:
        code, error = fail(monkeypatch, capsys, "stops", "Lund")
    finally:
        released.set()
    assert (code, len(calls)) == (3, 3)
    assert "no complete response" in error["message"]


@pytest.mark.parametrize(
    "args", [["trips", "1", "2", "--count", "many"], ["trips", "1", "2", "--at", "tomorrow"]]
)
def test_argument_errors_are_json_usage_errors(monkeypatch, capsys, args):
    code, error = fail(monkeypatch, capsys, *args)
    assert (code, error["kind"]) == (2, "usage")
