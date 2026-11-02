# skane

A Python library and CLI for Skånetrafiken journey planning. It uses the journey-planner API behind [skanetrafiken.se](https://www.skanetrafiken.se/) and is not affiliated with Skånetrafiken.

```sh
uv add skane
uv tool install skane
skane stops Jupitergatan
skane trips 'Lund Jupitergatan' 'Malmö Kungsgatan' --line 171 --json
skane leg 'LEG_ID'
```

```python
from skane import DepartAt, trips
from datetime import datetime
from zoneinfo import ZoneInfo

result = trips(
    "Lund Jupitergatan",
    "Malmö Kungsgatan",
    DepartAt(datetime(2026, 11, 3, 7, 0, tzinfo=ZoneInfo("Europe/Stockholm"))),
    line="171",
)
for trip in result.trips:
    print(trip.departure, trip.arrival)
```

Immutable models expose `to_dict()` and `to_json()` with the same fields as CLI JSON. Times are aware `datetime` values in Europe/Stockholm; input datetimes must be timezone-aware. Missing realtime values are `None`, not zero. Names must match exactly, ignoring case; `stops()` returns reusable `Place` values. Failures derive from `SkaneError`: `UsageError`, `UnavailableError` and `InvalidResponseError`. A name that matches no single place raises `UnresolvedPlaceError` with the candidates. `vehicle_trip(leg_id)` returns every stop of the vehicle that runs a leg.

Use `skane --help` and each command's `--help` for options and exit codes. Development: `uv sync --locked`, then `just check`.
