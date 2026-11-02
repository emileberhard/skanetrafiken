# skane

A Python library and CLI for Skånetrafiken journey planning. It uses the journey-planner API behind [skanetrafiken.se](https://www.skanetrafiken.se/) and is not affiliated with Skånetrafiken.

```sh
uv add skane
uv tool install skane
skane stops Jupitergatan
skane trips 'Lund Jupitergatan' 'Malmö Kungsgatan' --line 171 --json
```

```python
from skane import Client

result = Client().trips("Lund Jupitergatan", "Malmö Kungsgatan", line="171")
for journey in result.trips:
    print(journey.departure, journey.arrival)
```

The immutable models expose `to_dict()` and `to_json()` with the same fields as the CLI JSON. Times are aware `datetime` values in Europe/Stockholm; input datetimes must be timezone-aware. Missing realtime values are `None`, not zero. Place names must match exactly, ignoring case; use `Client().stops()` to find IDs or coordinates. Failures derive from `SkaneError`: `UsageError`, `UnavailableError` and `InvalidResponseError`.

Use `skane --help` and each command's `--help` for options and exit codes. Development: `uv sync --locked`, then `just check`.
