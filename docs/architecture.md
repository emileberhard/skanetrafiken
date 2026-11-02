# Architecture

`skane` is a standalone Python library and CLI, with a stable journey JSON contract for machine consumers. A skill points agents to the CLI. The public Skånetrafiken journey planner API avoids credentials and a server; departure boards are outside scope.

The private wire module owns HTTP, bounded retries and Pydantic validation. The client translates wire responses into frozen public models, owns place resolution, time conversion and pagination, and exposes typed operations. Models serialize themselves to the same JSON consumed by the CLI. This keeps upstream representations at one boundary and reports invalid responses instead of inventing data. Direct use of the website API is simpler than a keyed server or separate MCP service; the trade-off is dependence on an undocumented API, so recorded public fixtures and exit 4 make drift visible. Exact-name resolution prevents wrong-stop guesses; IDs and coordinates handle ambiguity. Independent name lookups run concurrently; pages are sequential because each requires the preceding cursor.

The API may stop exposing endpoints or change their meaning; repair belongs at the boundary. Unknown realtime must remain distinct from zero; journey and route-link endpoints can disagree about which values are present. Once consumers use the JSON and Python contracts, changing field semantics or model shapes is expensive.
