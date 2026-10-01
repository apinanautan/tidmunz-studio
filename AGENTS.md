# SnapGen Codex workflow

Use the `codex-work-loop` discipline for every non-trivial change in this repository.

## Required loop

1. Inspect the relevant runtime path and `git status` before editing.
2. Define observable acceptance checks.
3. Make the smallest scoped change and preserve unrelated user edits.
4. Verify syntax plus the narrowest relevant behavior or regression test.
5. Inspect the final diff and update `STATE.md` when work may continue in another task.

For persistence bugs, trace all five points: state owner, UI mutation, save call, on-disk value, and startup load/default ordering. A setting is not fixed until a restart scenario is verified or clearly reported as unverified.

Do not overwrite the recovered bytecode, expose values from `snapgen_data/snapgen_config.json`, or print credentials and session tokens. Do not publish an update unless the user explicitly requests publication.
