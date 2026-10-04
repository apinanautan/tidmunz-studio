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

## GitHub commit and release workflow

Source repository: `C:\Users\Apinan\Documents\GitHub\tidmunz-studio`, branch `main`, remote `https://github.com/tidmunzsocial-lab/tidmunz-studio`. The installed app at `%LOCALAPPDATA%\Tidmunz Studio` and the legacy `Desktop\Project snapgen.ai` folder are not source locations; never edit them. `C:\Users\Apinan\chatgpt-api` is a separate repository: preserve its existing uncommitted changes and do not stage or commit them as part of Tidmunz Studio work.

After a completed Tidmunz Studio source task, commit and push only the files changed for that task:

1. Inspect `git status` and stage each intended file by its explicit path. Never use `git add -A` or `git add .`.
2. Run `python -m py_compile` on every changed Python file.
3. Run `uvx --quiet --with pillow pytest -q tests`. Three Prompt-Ref failures are known baseline; do not push if the failure count increases or any additional test fails.
4. Check for secrets and confirm `__pycache__/snapgen_core.cpython-312.pyc` and user configuration were not changed.
5. Commit with a short message in the form `<page>: <change>`, then push `main` using the `tidmunzsocial-lab` GitHub account:

   ```powershell
   $env:GH_TOKEN = gh auth token -u tidmunzsocial-lab
   try {
       git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin main
   } finally {
       Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
   }
   ```

Never display or log the token.

Create and publish a GitHub Release only when the user explicitly asks to release a version using `ออก vX.Y.Z`. Use the requested tag; do not create a tag or release otherwise. Push `main` first, push the tag, wait for the Release workflow, then confirm the release contains both `tidmun-studio-patch.zip` and `Tidmunz-Studio.exe`. Report the commit, checks, and release status in Thai; state any failed or unverified step plainly.

## Character Creator 5 characters

For any request to make or continue CC5 characters ("ทำตัวละคร", "ทำตัวที่เหลือต่อ", "เข้าไปอ่านหน้า CC5"), read `docs/CC5_CHARACTER_GUIDE.md` first and follow it end to end. It lists every file location, the user's rules, the token-saving method (`snapgen_modules/cc5/cc5_find.py` text search instead of thumbnails), and the exact CC5 click path.
