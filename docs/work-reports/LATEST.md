# Latest Work Report

Current task: Final Character Visual Bible Architecture

Status: completed

Full work report:
- `docs/work-reports/2026-08-08_final-character-visual-bible-architecture.md`

Key handoff:
- AI-generated canonical Character Visual Bible v5 is the only semantic source of truth for Character Reference.
- Python only canonicalizes legacy structure, validates, transports, serializes, and fills missing fields via targeted AI repair; it does not infer character wardrobe.
- Character lookup and repair bind by `character_id` / exact canonical name, never array index.
- Character Ref directly serializes one canonical object into Front / 3/4 / Full-body with the same wardrobe.
- Runtime debug snapshot: `snapgen_data/debug/ref_last_request.json`.
- `แมวผี (อาย)` fixture verifies mother/uncle/medium/Kan/cat invariants and reordered-array wardrobe stability.
- Combined new integration/unit suite: 19/19 pass.
- Existing Prompt-Ref/Storyboard baseline remains 18 tests with 4 failures and 4 errors; no new regression.
- Unrelated pre-existing working-tree changes remain; do not stage them blindly.
