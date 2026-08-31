"""Read-only parsers for source files this repo does not own.

Python and firmware ground truth arrives as JSON from ``reacher.schema``. What
remains is the frontend, whose TypeScript tables are hand-maintained mirrors of
the backend registries with nothing checking them — so they must be read from
source.

Every parser here is a pure function (path in, plain dicts out) and carries a
sanity floor: a regex that stops matching after a reformat must raise, never
return an empty result that a comparison would read as "no drift".
"""
