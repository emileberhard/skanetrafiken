---
name: skanetrafiken
description: "Find Skåne bus and train journeys, departure and arrival times, delays, stop IDs and vehicle-trip stops using Skånetrafiken. Use for regional public transport planning; use places-search for general Google Maps routing."
---

# Skånetrafiken

Run `skt --version` to check the installed CLI. If missing, download the latest wheel with `ghp release download --repo emileberhard/skanetrafiken --pattern "*.whl"` and install it with `uv tool install ./skanetrafiken_cli-*.whl`. Use `skt --help` and each command's `--help` for the full interface.

```sh
skt stops Jupitergatan
skt trips 'Lund Jupitergatan' 'Malmö Kungsgatan' --line 171 --json
skt trips 'Lund C' 'Malmö C' --arrive-by '08:00'
skt trip 'LEG_ID' --json
```

Names must match exactly, ignoring case; otherwise choose an ID or coordinates from `skt stops`. Times are Europe/Stockholm. A null delay means realtime is unavailable, not that the trip is on time. Exit 4 means Skånetrafiken's API response changed and the CLI needs repair.
