# Change requests

One file per confirmed change request, committed in the pull request that makes
the change: `changes/YYYY-MM-DD-<slug>.md`. A change that spans reacher and
labrynth carries the same file in both pull requests.

Do not write these by hand. The `change_request_record` tool of `reacher-mcp`
renders the file from the request the user confirmed (see
`docs/mcp-server.md`). The front matter has a fixed schema, named by its
`schema` key (`reacher-change-request/1`):

| Key | Meaning |
|---|---|
| `date`, `kind`, `request` | When, which change kind, and the user's own words |
| `paradigms` | Every paradigm that gets the change, `_lite` UNO builds listed by name |
| `boards` | `mega`, `uno`, or both |
| `devices`, `pins` | Component keys (as `get_hardware_map` names them) and pins per board |
| `commands` | Registry names, each marked `existing` or `new` |
| `reaches_board` | Whether the board is told (a firmware command) or only the host uses it |
| `safety_flags` | Why a person must review and bench-check it; empty when none |
| `small_model_ok`, `bench_test`, `silent_steps` | From the plan: who can make it, what test it needs, how many edits no check guards |

The body holds the behaviour as observable events, the acceptance check, and
every question asked with the user's answer.
