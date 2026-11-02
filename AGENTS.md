# skt

Standalone Skånetrafiken command-line journey planner; the CLI owns behavior and the agent skill is a short entry point.

| Path | Owner |
| --- | --- |
| `src/skt/api.py` | HTTP, retries and upstream response validation |
| `src/skt/cli.py` | place resolution, time conversion, journey filtering and output contract |
| `tests/fixtures` | sanitized recorded upstream responses; tests never access the network |
| `skills/skanetrafiken` | agent entry point |
| `.github/workflows` | CI and release |

## API and invariants

The unauthenticated base is `https://www.skanetrafiken.se/gw-tps/api`; send `Search-Engine-Environment: TjP`, `Accept-Language: sv-SE` and an honest versioned User-Agent. `/v2/Points` finds stops, addresses and POIs. `/v2/Journey` accepts stop area IDs or `lat#lon` with type `LOCATION` and pages via `nextCursor` or `prevCursor`. `/v3/RouteLink/<encoded id>` returns a vehicle's stops. No keyless departure board is known.

Never fuzzy-select a place. Resolve both names concurrently. Preserve an absent realtime deviation as unknown; explicit zero is zero. Scheduled UTC times plus deviation yield expected times; output Europe/Stockholm offsets. Journey and route-link endpoints can expose different realtime information. ARGB line colors discard alpha. A line filter retains trips only when every transit leg matches.

`trips --json` is a consumer contract: top-level `from`, `to`, `trips`; each trip has `departure`, `arrival`, `changes`, `notices`, `legs`. Legs expose `id`, `mode`, `name`, `line`, `towards`, `color`, `textColor`, `cancelled`, `notices`, and `from`/`to` with `id`, `name`, `platform`, `scheduledPlatform`, `scheduled`, `expected`, `delayMinutes`. Unknown realtime is null; trip departure/arrival fall back to scheduled. Preserve names and meanings.

## Commands and release

`just check` runs locked sync, Ruff, ty and pytest. CI also builds both distributions. Plain annotated `MAJOR.MINOR.PATCH` tags on main, newer than other version tags and with passing main CI, build versioned wheel and sdist and publish a GitHub release with tag notes. Invalid tags fail. No deploy or persistent service. Recover by issuing a new higher patch tag; never overwrite a release.
