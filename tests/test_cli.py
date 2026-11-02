import io
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from skt import api, cli

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["skt", *args])
    cli.main()
    return json.loads(capsys.readouterr().out)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(api, "urlopen", reject)


def respond(monkeypatch, response):
    def open_fixture(request, timeout):
        value = response(request.full_url) if callable(response) else response
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(api, "urlopen", open_fixture)


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
    journey = result["trips"][0]
    leg = journey["legs"][0]
    assert result["from"] == {"id": "9021012081116000", "name": "Lund Jupitergatan"}
    assert journey["departure"] == "2026-11-02T06:09:00+01:00"
    assert journey["arrival"] == "2026-11-02T06:26:00+01:00"
    assert (leg["mode"], leg["line"], leg["towards"]) == ("bus", "171", "Malmö Södervärn")
    assert (leg["color"], leg["textColor"]) == ("#FEC900", "#3E3B39")
    assert leg["from"]["platform"] == "Läge A"
    assert leg["from"]["expected"] is None
    assert leg["from"]["delayMinutes"] is None


def test_vehicle_detail_realtime_zero_and_nonzero(monkeypatch, capsys):
    respond(monkeypatch, fixture("route-realtime.json"))
    result = invoke(monkeypatch, capsys, "trip", "leg", "--json")
    first = result["stops"][0]["departure"]
    second = result["stops"][1]["arrival"]
    assert first == {
        "scheduled": "2026-11-02T02:21:00+01:00",
        "expected": "2026-11-02T02:21:00+01:00",
        "delayMinutes": 0,
    }
    assert second["expected"] == "2026-11-02T02:26:00+01:00"
    assert second["delayMinutes"] == -1


def test_ambiguous_place_has_candidates_and_exit_2(monkeypatch, capsys):
    respond(monkeypatch, fixture("points.json"))
    monkeypatch.setattr(sys, "argv", ["skt", "trips", "Jupitergatan", "123", "--json"])
    with pytest.raises(SystemExit, match="2"):
        cli.main()
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == 2
    assert "Lund Jupitergatan" in error["message"]
    assert "9021012081116000" in error["message"]


def test_invalid_upstream_response_exits_4(monkeypatch, capsys):
    respond(monkeypatch, {"wrong": []})
    monkeypatch.setattr(sys, "argv", ["skt", "stops", "Lund", "--json"])
    with pytest.raises(SystemExit, match="4"):
        cli.main()
    assert json.loads(capsys.readouterr().out)["error"]["code"] == 4


def test_line_filter_pages_and_deduplicates(monkeypatch, capsys):
    bus = fixture("journey-bus.json")
    first = dict(bus, journeys=[], nextCursor="page-two")
    second = dict(bus, journeys=bus["journeys"][:1], nextCursor=None)

    def page(url):
        return second if parse_qs(urlparse(url).query).get("nextCursor") else first

    respond(monkeypatch, page)
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
    assert len(result["trips"]) == 1
    assert result["trips"][0]["legs"][0]["line"] == "171"


def test_dst_offsets_and_ambiguous_wall_time():
    assert cli.iso(cli.local_time("2026-07-01 08:00")).endswith("+02:00")
    assert cli.iso(cli.local_time("2026-12-01 08:00")).endswith("+01:00")
    with pytest.raises(api.SktError):
        cli.local_time("2026-10-25 02:30")
    with pytest.raises(api.SktError):
        cli.local_time("2026-03-29 02:30")
    assert cli.iso(cli.local_time("2026-10-25T02:30:00+01:00")).endswith("+01:00")


def test_walk_distance_and_train_mode_from_live_response(monkeypatch, capsys):
    respond(monkeypatch, fixture("journey-walk.json"))
    result = invoke(
        monkeypatch, capsys, "trips", "55.603,13.003", "9021012081216000", "--count", "1", "--json"
    )
    walk, train = result["trips"][0]["legs"]
    assert walk["mode"] == "walk"
    assert walk["distance"] == "903 m"
    assert walk["line"] is None
    assert train["mode"] == "train"


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
    journey = result["trips"][0]
    leg = journey["legs"][0]
    assert leg["mode"] == "train"
    assert leg["from"]["expected"] is None
    assert leg["from"]["delayMinutes"] is None
    assert leg["to"]["scheduled"] == "2026-11-02T03:15:00+01:00"
    assert leg["to"]["expected"] == "2026-11-02T03:10:00+01:00"
    assert leg["to"]["delayMinutes"] == -5
    assert journey["arrival"] == "2026-11-02T03:10:00+01:00"


def test_unavailable_api_retries_and_exits_3(monkeypatch, capsys):
    from urllib.error import URLError

    calls = []

    def unavailable(request, timeout):
        calls.append(request.full_url)
        raise URLError("offline")

    monkeypatch.setattr(api, "urlopen", unavailable)
    monkeypatch.setattr(api.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sys, "argv", ["skt", "stops", "Lund", "--json"])
    with pytest.raises(SystemExit, match="3"):
        cli.main()
    assert len(calls) == 3
    assert json.loads(capsys.readouterr().out)["error"]["code"] == 3


def test_omits_departures_before_requested_time(monkeypatch, capsys):
    respond(monkeypatch, fixture("journey-realtime.json"))
    result = invoke(
        monkeypatch,
        capsys,
        "trips",
        "9021012080000000",
        "9021012045007000",
        "--at",
        "2026-11-02 03:00",
        "--count",
        "1",
        "--json",
    )
    assert result["trips"][0]["departure"] == "2026-11-02T03:35:00+01:00"
