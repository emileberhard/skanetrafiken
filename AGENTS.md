# skane

A public Python library and CLI for Skånetrafiken journey planning. Never commit credentials, personal data, private infrastructure or private paths. The library owns behavior; the CLI and agent skill are entry points.

| Path | Owner |
| --- | --- |
| `src/skane/_wire.py` | HTTP, retries and upstream wire validation |
| `src/skane/_client.py` | place resolution, journey filtering and translation into public models |
| `src/skane/_models.py` | immutable public models and their JSON serialization |
| `src/skane/cli.py` | argument parsing, terminal output and exit-code mapping |
| `tests/fixtures` | sanitized public timetable responses; tests never access the network |
| `skills/skanetrafiken` | agent entry point |
| `.github/workflows` | CI and release |

## API and invariants

The unauthenticated base is `https://www.skanetrafiken.se/gw-tps/api`; send `Search-Engine-Environment: TjP`, `Accept-Language: sv-SE` and an honest versioned User-Agent. `/v2/Points` finds stops, addresses and POIs. `/v2/Journey` accepts stop area IDs or `lat#lon` with type `LOCATION` and pages via `nextCursor` or `prevCursor`. `/v3/RouteLink/<encoded id>` returns a vehicle's stops. No keyless departure board is known.

Never fuzzy-select a place. Resolve both names concurrently. Preserve an absent realtime deviation as unknown; explicit zero is zero. Scheduled UTC times plus deviation yield expected times; output Europe/Stockholm offsets. Journey and route-link endpoints can expose different realtime information; don't enrich journeys with leg lookups. A line filter retains trips only when every transit leg matches. Addresses and POIs use coordinates as reusable identifiers.

`trips --json` is a consumer contract: top-level `from`, `to`, `trips`; each trip has `departure`, `arrival`, `changes`, `notices` (the journey's `deviationTags` texts), `legs`. Legs expose `id`, `mode`, `name`, `line`, `towards`, `cancelled`, `notices`, `distanceMeters`, and `from`/`to` with `id`, `name`, `platform`, `scheduledPlatform`, `scheduled`, `expected`, `delayMinutes`, `timeOmitted`. Unknown realtime is null; trip departure/arrival fall back to scheduled. Public models serialize to this same representation; JSON names use an explicit mapping.

The public planner API avoids a keyed server but is undocumented and can change. The wire boundary owns its validation and errors. Recorded public fixtures guard parsing but cannot see upstream drift, so fields the API always sends are required and a rename fails loudly; optional fields default only to what their observed absence means. Once consumers use the JSON and Python contracts, changing field semantics or model shapes is expensive. Independent lookups run concurrently; pages are sequential because each needs the prior cursor.

Each public operation owns one monotonic 30-second budget shared by lookups, pages and retries. Request and retry waits use the remaining budget. Python's blocking HTTP timeout is per operation rather than a hard total bound, so stalled DNS or a response that keeps producing bytes can exceed the budget; a strict wall-clock guarantee would require a cancellable transport. A bare `HH:MM` is today in Stockholm, including past times; ambiguous or nonexistent local times require an offset.

## Commands and release

`just check` runs locked sync, Ruff, ty and pytest. CI checks Python 3.12–3.15 and builds both distributions once. Plain annotated `MAJOR.MINOR.PATCH` tags on main, newer than other version tags and with passing main CI, build versioned wheel and sdist and publish with attestations to PyPI through OIDC in the `pypi` environment, then publish a GitHub release with tag notes. Invalid tags fail. Recover by issuing a new higher patch tag; never overwrite a release.
