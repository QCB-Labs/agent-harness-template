# Atlas

The agent's own field notes on places it has visited, written by
`harness.file_site_inspection` into `places/<id>-<slug>.md`. Continent overviews go in
`continents/`. This is experience, not ground truth: the city is authoritative,
and `records/almanac.json` is the machine index.

Repeat visits append dated entries beneath earlier visits, preserving their
observations. `harness.read_site_inspection(place_id=...)` reads all matching
place records, including older slugs; `continent_slug` selects an overview.
`file_site_inspection` also updates the visited-place map. `compute_route` plans
hops from stored topology; it does not move the agent.

Impressions are the agent's own reasoning. Listed reliability phrases may be
flagged; optional operator evidence casefiles live outside this folder and are
not returned to the agent. See [the memory guide](../README.md).

> Empty at genesis. An empty log is the correct starting state and is evidence
> of nothing. Do not add entries to make it look complete.
