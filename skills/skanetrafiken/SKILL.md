---
name: skanetrafiken
description: "Find Skåne bus and train journeys, departure and arrival times, delays, stop IDs and vehicle-trip stops using Skånetrafiken. Use for regional public transport planning."
---

# Skånetrafiken

Run `skane --version` to check the CLI; install with `uv tool install skane` or run with `uvx skane`. Use `skane --help` and each command's `--help` for the full interface.

```sh
skane stops Jupitergatan
skane trips 'Lund Jupitergatan' 'Malmö Kungsgatan' --line 171 --json
skane trips 'Lund C' 'Malmö C' --arrive-by '08:00'
skane leg 'LEG_ID' --json
```

Names must match exactly, ignoring case. Use the ID from `stops` for stop areas, or its coordinates for addresses and POIs. Times are Europe/Stockholm; `HH:MM` means today, even if past. Use an explicit date for another day. A null delay means the response has no realtime value, not that the trip is on time. A leg's `cancelled` covers only the section you ride; a cancellation further along the same vehicle shows in the leg's `notices`. Obtain `LEG_ID` from `legs[].id` in `trips --json`, then use `skane leg` for per-stop realtime. For trains, `--line` matches the run number. Exit 4 means Skånetrafiken's API response changed and the library needs repair.
