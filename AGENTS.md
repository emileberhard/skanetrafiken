# skane

A public Python library and CLI for Skånetrafiken journey planning. Never commit credentials, personal data, private infrastructure or private paths. The library owns behavior; the CLI and agent skill are entry points.

| Path | Owner |
| --- | --- |
| `src/skane/_wire.py` | HTTP, retries and upstream wire validation |
| `src/skane/_client.py` | place resolution, journey filtering and translation into public models |
| `src/skane/_models.py` | immutable public models and their JSON serialization |
| `src/skane/cli.py` | argument parsing, terminal output, and the one table of error kinds and exit codes |
| `tests/fixtures` | sanitized public timetable responses; tests never access the network |
| `skills/skanetrafiken` | agent entry point |
| `.github/workflows` | CI and release |

## API and invariants

The unauthenticated base is `https://www.skanetrafiken.se/gw-tps/api`; send `Search-Engine-Environment: TjP`, `Accept-Language: sv-SE` and an honest versioned User-Agent. `/v2/Points` finds stops, addresses and POIs. `/v2/Journey` accepts stop area IDs or `lat#lon` with type `LOCATION` and pages via `nextCursor` or `prevCursor`. `/v3/RouteLink/<encoded id>` returns a vehicle's stops. No keyless departure board is known.

Never fuzzy-select a place. Resolve both names concurrently. Preserve an absent realtime deviation as unknown; explicit zero is zero. Scheduled UTC times plus deviation yield expected times; output Europe/Stockholm offsets. Journey and route-link endpoints can expose different realtime information; don't enrich journeys with leg lookups. A line filter retains trips only when every transit leg matches. Addresses and POIs use `lat,lon` as their reusable public identifier everywhere, including `trips` output; only the journey request encodes it as `lat#lon`. The top-level `from`/`to` name is null when the input was coordinates, or a stop ID that no returned trip names.

`trips --json` is a consumer contract: top-level `from`, `to`, `trips`; each trip has `departure`, `arrival`, `changes`, `notices` (the journey's `deviationTags` texts), `legs`. Legs expose `id`, `mode`, `name`, `line`, `towards`, `cancelled`, `notices`, `distanceMeters`, and `from`/`to` with `id`, `name`, `platform`, `scheduledPlatform`, `scheduled`, `expected`, `delayMinutes`, `timeOmitted`. Unknown realtime is null; trip departure/arrival fall back to scheduled. The API marks cancellation only with `canceled: true`, on a journey leg when the requested section is cancelled and on a RouteLink stop's arrival or departure, and omits it otherwise (recorded for train 1753 on 2026-11-02, whose Ystad–Simrishamn section was cancelled while Malmö–Ystad ran), so absence decodes as not cancelled. Public models serialize to this same representation; JSON names use an explicit mapping. `stops --json` prints `{"places": [...]}`. With `--json`, every `SkaneError`, argument errors included, prints `{"error": {"kind", "message"}}` to stdout, plus `candidates` for `unresolved_place`; the CLI's failure table owns kinds, exit codes and the help text that lists them. The CLI detects `--json` in argv because a parse error has no namespace yet.

The public planner API avoids a keyed server but is undocumented and can change. The wire boundary owns its validation and errors. Recorded public fixtures guard parsing but cannot see upstream drift, so fields the API always sends are required and a rename fails loudly; optional fields default only to what their observed absence means. Once consumers use the JSON and Python contracts, changing field semantics or model shapes is expensive. Independent lookups run concurrently; pages are sequential because each needs the prior cursor.

Each public operation owns one monotonic 30-second budget shared by lookups, pages and retries. Request and retry waits use the remaining budget. urllib's timeout bounds single socket operations but not DNS or a slowly dripping body, so each attempt runs in a daemon thread that the caller abandons at the attempt's wall-clock limit; an abandoned GET may finish unseen in the background. A bare `HH:MM` is today in Stockholm, including past times; ambiguous or nonexistent local times require an offset.

## Commands and release

`just check` runs locked sync, Ruff, ty and pytest. CI checks Python 3.12–3.15, builds both distributions once and runs the built wheel in an isolated environment. Plain annotated `MAJOR.MINOR.PATCH` tags on main, newer than other version tags and with passing main CI, build versioned wheel and sdist, check that the wheel reports the tag as its version, and publish with attestations to PyPI through OIDC in the `pypi` environment, then publish a GitHub release with tag notes. Invalid tags fail. Recover by issuing a new higher patch tag; never overwrite a release.
