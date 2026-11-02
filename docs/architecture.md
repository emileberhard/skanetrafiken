# Architecture

`skt` is a standalone human and machine CLI, with a stable journey JSON contract for the Live Activity. A skill only points agents to the CLI. The public Skånetrafiken journey planner API avoids credentials and a server; departure boards are outside scope.

The API module owns HTTP, bounded retries and Pydantic validation. The CLI owns name resolution, local time conversion, pagination, filtering and display. This keeps upstream representations at one boundary and lets the CLI report invalid responses instead of silently inventing data. Direct use of the existing website API is simpler than a Trafiklab-keyed server or a second MCP service; the trade-off is dependence on an undocumented API, so recorded fixtures and exit 4 make drift visible. Exact-name resolution prevents wrong-stop guesses; IDs and coordinates handle ambiguity. Independent name lookups run concurrently; pages are sequential because each requires the preceding cursor.

The API may stop exposing the endpoints or change its meaning; repair then belongs at the boundary, and preserving the JSON contract may require translating new upstream data. Unknown realtime must remain distinct from zero; the journey and route-link endpoints can disagree about which values are present. Once consumers use the contract, changing its field semantics is expensive.
