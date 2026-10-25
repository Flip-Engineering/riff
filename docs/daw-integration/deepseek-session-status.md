# DeepSeek seat status for #78 (operational note)

Author: riff-daw-deepseek-20261025. Status: 2026-10-25, end of research turn.

## Route actually observed

- Harness/model/effort: `dsh / deepseek-flash / max` (launch configuration;
  no capacity inferred from registry).
- Native session: `session-36ede686-9a78-488f-8526-258c520aaa67`.
- Attempt: `receive:riff-daw-deepseek-20261025:2497:333c00fe02306534ad1f4c23b50b9d12`.
- This turn is running and completed the probes below. The earlier
  `exit 1` report in `baton players` is the predecessor launch's missing
  `DEEPSEEK_API_KEY` failure, not this attempt. Registry `observedHarness` /
  `observedEffort` are empty, so they are not evidence of the route.

## Baton write-channel failure (needs operator/coordinator action)

All Baton **write** actions are refused in this DSH workspace-write sandbox:

- `baton report --text ...` -> `status: refused`, `code: 13`,
  `cause: Permission denied` (`report:ed4d1df483ad184bba20236495415daa`;
  a second startup-ack/evidence report was also refused as
  `report:bd6ae2eb15e86da0ee67d6808420bf1e`).
- `baton completion-request --text ...` -> message id
  `message:riff-daw-deepseek-20261025:4b41c5294dee2b4aef13b1c2904a9d88`
  but `admission.status: refused`, `code: 8`,
  `cause: attempt to write a readonly database`.
- `baton knowledge search/record` also refused on the DB open.

`baton inbox` and `baton players` reads work. The database directory and
attempt directory are outside the writable workspace; the native harness can
record delivery but the CLI cannot write reports from this seat. The
coordinator should capture this final native result and apply any knowledge
records itself.

## Git commit failure

`git commit` failed with:

```
fatal: Unable to create '.../riff-backlog-20261019/.git/worktrees/riff-daw-ensemble-20261025/index.lock': Permission denied
```

The worktree's `.git` directory is outside the writable workspace, so this
seat cannot stage or commit even though the worktree files are writable. The
deliverables below are present as untracked files in the shared checkout.
The conductor has write access and should `git add`/commit them with
`refs #78` (or merge the content into its proposal commit).

## Deliverables left in the shared checkout

- `docs/daw-integration/abc-semantics.md`
- `docs/daw-integration/ardour-headless-probe.md`
- `docs/daw-integration/headless-import-correction.md`
- `docs/daw-integration/tracktion-engine-note.md`
- `docs/evidence/abc-semantics-20261025.json`
- `docs/evidence/ardour-headless-probe-20261025.json`
- this status note

Four-note follow-up receipt: `.probe-tmp/deepseek/ardour-probe/four-session/export/four-gmsynth.wav`
(nonzero, peak -13.13 dBFS, RMS -32.25 dBFS, 11.655729 s; 4 MIDI notes
C-D-E-F through gmsynth). The conductor's earlier review saw only the empty
Test3 silence probe; the content path is the `1region` session fixture, not
Test3.

The ABC prose file was renamed to `abc-semantics.md` per the conductor's
ownership instruction; the uncommitted `proposal-draft.md` references were
updated to that path and the new evidence filename.

These are research evidence only; no Riff operation contract, DAW selection
or implementation architecture is committed here.
