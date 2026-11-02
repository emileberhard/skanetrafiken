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

Names must match exactly, ignoring case. Use the ID from `stops` for stop areas, or its coordinates for addresses and POIs. Times are Europe/Stockholm; `HH:MM` means today, even if past. Use an explicit date for another day. A null delay means the response has no realtime value, not that the trip is on time. A leg's `cancelled` covers only the section you ride; a cancellation further along the same vehicle shows in the leg's `notices`. A trip's `departure` and `arrival` are expected times when realtime is known and scheduled times otherwise; check the first and last leg's `delayMinutes` to tell them apart. `trips` seldom carries realtime even for a vehicle already on its way, while `skane leg` usually does, so read a departure or arrival you depend on from `skane leg` with the `LEG_ID` from `legs[].id`; walking legs have a null `id`. For trains, `--line` matches the run number.

With `--json`, `stops` prints `{"places": [...]}` and every failure prints `{"error": {"kind", "message"}}` to stdout. Kind `unresolved_place` (exit 2) adds `candidates`, each a `stops` place to retry with; `usage` (exit 2) means fix the arguments; `unavailable` (exit 3) means try again later; `invalid_response` (exit 4) means Skånetrafiken's API changed and skane needs repair. Exit 1 is a bug in skane.
