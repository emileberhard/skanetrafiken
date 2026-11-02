---
name: skanetrafiken
description: "Find Skåne bus and train journeys, departure and arrival times, delays, stop IDs and vehicle-trip stops using Skånetrafiken. Use for regional public transport planning; use places-search for general Google Maps routing."
---

# Skånetrafiken

Run `skane --version` to check the CLI; install with `uv tool install skane` or run with `uvx skane`. Use `skane --help` and each command's `--help` for the full interface.

```sh
skane stops Jupitergatan
skane trips 'Lund Jupitergatan' 'Malmö Kungsgatan' --line 171 --json
skane trips 'Lund C' 'Malmö C' --arrive-by '08:00'
skane trip 'LEG_ID' --json
```

Names must match exactly, ignoring case; otherwise choose an ID or coordinates from `skane stops`. Times are Europe/Stockholm. A null delay means the response has no realtime value, not that the trip is on time. Exit 4 means Skånetrafiken's API response changed and the library needs repair.
