import io
import json
import subprocess
import sys
from datetime import UTC, datetime, time
from email.message import Message
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest

import skane
from skane import _wire, cli

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["skane", *args])
    cli._run()
    return json.loads(capsys.readouterr().out)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(_wire, "urlopen", reject)


def respond(monkeypatch, response):
    def open_fixture(request, timeout):
        value = response(request.full_url) if callable(response) else response
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(_wire, "urlopen", open_fixture)


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
    }
    assert result["from"] == {"id": "9021012081116000", "name": "Lund Jupitergatan"}
    assert trip["departure"] == "2026-11-02T06:09:00+01:00"
    assert trip["arrival"] == "2026-11-02T06:26:00+01:00"
    assert (leg["mode"], leg["line"], leg["towards"]) == ("bus", "171", "Malmö Södervärn")
    assert leg["from"]["expected"] is None and leg["from"]["delayMinutes"] is None


def test_leg_realtime_and_stop_flags(monkeypatch, capsys):
    raw = fixture("route-realtime.json")
    raw["stops"][0]["departure"].update(canceled=True, timeOmitted=True)
    respond(monkeypatch, raw)
    result = invoke(monkeypatch, capsys, "leg", "leg-id", "--json")
    first, second = result["stops"][:2]
    assert first["cancelled"] is True
    assert first["departure"] == {
        "scheduled": "2026-11-02T02:21:00+01:00",
        "expected": None,
        "delayMinutes": None,
        "timeOmitted": True,
    }
    assert second["arrival"]["expected"] == "2026-11-02T02:26:00+01:00"
    assert second["arrival"]["delayMinutes"] == -1
    monkeypatch.setattr(sys, "argv", ["skane", "leg", "leg-id"])
    cli._run()
    rendered = capsys.readouterr().out
    assert "CANCELLED" in rendered and "--:-- (scheduled 02:21)" in rendered


def test_on_time_and_timetable_are_distinct_in_human_output(monkeypatch, capsys):
    raw = fixture("journey-bus.json")
    respond(monkeypatch, raw)
    args = ["skane", "trips", "1", "2", "--at", "2026-11-02 00:00", "--count", "1"]
    monkeypatch.setattr(sys, "argv", args)
    cli._run()
    assert "06:09 (scheduled)" in capsys.readouterr().out
    raw["journeys"][0]["routeLinks"][0]["from"]["deviation"] = 0
    cli._run()
    assert "06:09 (on time)" in capsys.readouterr().out


def test_ambiguous_place_has_structured_candidates_and_cli_exit_2(monkeypatch, capsys):
    respond(monkeypatch, fixture("points.json"))
    with pytest.raises(skane.AmbiguousPlaceError) as caught:
        skane.trips("Jupitergatan", "123")
    assert caught.value.query == "Jupitergatan"
    assert any(
        p.name == "Lund Jupitergatan" and p.id == "9021012081116000"
        for p in caught.value.candidates
    )
    monkeypatch.setattr(sys, "argv", ["skane", "trips", "Jupitergatan", "123", "--json"])
    with pytest.raises(SystemExit, match="2"):
        cli._run()
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == 2 and "Lund Jupitergatan" in error["message"]


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
    skane.trips(chosen, "9021012081216000", when, count=1)
    assert requests[0]["fromPointType"] == ["LOCATION"]
    assert requests[0]["fromPointId"] == [f"{chosen.lat}#{chosen.lon}"]
    requests.clear()
    skane.trips(chosen.id, "9021012081216000", when, count=1)
    assert requests[0]["fromPointType"] == ["LOCATION"]


def test_invalid_upstream_response_and_boolean_delay_exit_4(monkeypatch, capsys):
    raw = fixture("journey-bus.json")
    raw["journeys"][0]["routeLinks"][0]["from"]["deviation"] = False
    respond(monkeypatch, raw)
    monkeypatch.setattr(sys, "argv", ["skane", "trips", "123", "456", "--json"])
    with pytest.raises(SystemExit, match="4"):
        cli._run()
    assert json.loads(capsys.readouterr().out)["error"]["code"] == 4


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


def test_dst_and_next_occurrence():
    zone = ZoneInfo("Europe/Stockholm")
    assert cli.local_time("2026-07-01 08:00").isoformat().endswith("+02:00")
    assert cli.local_time("2026-12-01 08:00").isoformat().endswith("+01:00")
    with pytest.raises(skane.UsageError):
        cli.local_time("2026-10-25 02:30")
    with pytest.raises(skane.UsageError):
        cli.local_time("2026-03-29 02:30")
    assert (
        cli._next_occurrence(time(8), datetime(2026, 11, 2, 22, tzinfo=zone)).date().isoformat()
        == "2026-11-03"
    )
    assert (
        cli._next_occurrence(time(2, 30), datetime(2026, 10, 25, 0, 45, tzinfo=UTC)).isoformat()
        == "2026-10-25T02:30:00+01:00"
    )
    assert (
        cli._next_occurrence(time(2, 30), datetime(2026, 3, 29, 0, 0, tzinfo=UTC))
        .date()
        .isoformat()
        == "2026-03-30"
    )


def test_walk_distance_and_train_mode(monkeypatch, capsys):
    respond(monkeypatch, fixture("journey-walk.json"))
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
    walk, train = result["trips"][0]["legs"]
    assert (walk["mode"], walk["distanceMeters"], walk["line"]) == ("walk", 903, None)
    assert train["mode"] == "train"
    assert skane._client._distance_meters("1,2 km") == 1200
    with pytest.raises(skane.InvalidResponseError):
        skane._client._distance_meters("unknown")


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
        (["trips", "123", "456", "--json"], "Departure point could not be found"),
        (["leg", "bad", "--json"], "Invalid pattern of serviceJourneyId"),
    ]:
        calls.clear()
        monkeypatch.setattr(sys, "argv", ["skane", *args])
        with pytest.raises(SystemExit, match="2"):
            cli._run()
        error = json.loads(capsys.readouterr().out)["error"]
        assert (
            message in error["message"]
            and "Message:" in error["message"]
            and "Details:" in error["message"]
        )
        assert len(calls) == 1

    def missing(request, timeout):
        raise HTTPError(request.full_url, 404, "Not Found", Message(), io.BytesIO(b""))

    monkeypatch.setattr(_wire, "urlopen", missing)
    monkeypatch.setattr(sys, "argv", ["skane", "stops", "Lund", "--json"])
    with pytest.raises(SystemExit, match="4"):
        cli._run()
    assert json.loads(capsys.readouterr().out)["error"]["code"] == 4


def test_incomplete_read_retries_then_json_exit_3(monkeypatch, capsys):
    calls = []

    def unavailable(request, timeout):
        calls.append(request.full_url)
        raise IncompleteRead(b"partial")

    monkeypatch.setattr(_wire, "urlopen", unavailable)
    monkeypatch.setattr(_wire.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sys, "argv", ["skane", "stops", "Lund", "--json"])
    with pytest.raises(SystemExit, match="3"):
        cli._run()
    assert len(calls) == 3
    assert json.loads(capsys.readouterr().out)["error"]["code"] == 3


def test_command_deadline_maps_to_json_error(monkeypatch, capsys):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] < 30
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(cli.subprocess, "run", timeout)
    monkeypatch.setattr(sys, "argv", ["skane", "stops", "Lund", "--json"])
    with pytest.raises(SystemExit, match="3"):
        cli.main()
    assert json.loads(capsys.readouterr().out)["error"]["code"] == 3


def test_past_explicit_departure_and_aware_input(monkeypatch):
    respond(monkeypatch, fixture("journey-realtime.json"))
    when = skane.DepartAt(datetime(2026, 11, 2, 2, 0, tzinfo=ZoneInfo("Europe/Stockholm")))
    result = skane.trips("9021012080000000", "9021012045007000", when, count=1)
    assert result.trips[0].departure.isoformat() == "2026-11-02T02:35:00+01:00"
    with pytest.raises(skane.UsageError, match="timezone"):
        skane.trips("123", "456", skane.DepartAt(datetime(2026, 11, 2, 8, 0)))


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
    cli._run()
    assert "02:21 (on time)" in capsys.readouterr().out
    del raw["stops"][0]["departure"]["deviation"]
    cli._run()
    assert "02:21 (scheduled)" in capsys.readouterr().out


def test_leg_boolean_delay_is_invalid(monkeypatch, capsys):
    raw = fixture("route-realtime.json")
    raw["stops"][0]["departure"]["deviation"] = True
    respond(monkeypatch, raw)
    monkeypatch.setattr(sys, "argv", ["skane", "leg", "leg-id", "--json"])
    with pytest.raises(SystemExit, match="4"):
        cli._run()
    assert json.loads(capsys.readouterr().out)["error"]["code"] == 4


def test_command_deadline_terminates_child(monkeypatch, capsys):
    actual_run = subprocess.run

    def slow_child(*args, **kwargs):
        kwargs["timeout"] = 0.05
        return actual_run([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)

    monkeypatch.setattr(cli.subprocess, "run", slow_child)
    monkeypatch.setattr(sys, "argv", ["skane", "stops", "Lund", "--json"])
    with pytest.raises(SystemExit, match="3"):
        cli.main()
    assert json.loads(capsys.readouterr().out)["error"]["code"] == 3
