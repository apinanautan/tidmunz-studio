# Prompt-Ref Storyboard → GPT transport contract

Captured from the **embedded SnapGen Chrome profile** with Chrome DevTools Protocol Network events on 2026-08-05. This note is the canonical reference for the `สร้าง Storyboard + Prompt` button.

## Product rule

One story uses one Prompt-Ref ChatGPT conversation. After the Storyboard image is generated, SnapGen must attach the exact file currently displayed in the Storyboard result box to that same Prompt-Ref conversation and current parent message. It must not open a fresh Vision chat and must not use an image-generation conversation.

## Request contract captured from ChatGPT Web

- `action`: `next`
- `model`: `gpt-5-5`
- `conversation_id`: current Prompt-Ref conversation ID
- `parent_message_id`: current Prompt-Ref parent ID
- `client_prepare_state`: `none`
- `system_hints`: `[]` — no `picture_v2` for normal image analysis
- `supports_buffering`: `true`
- `supported_encodings`: `["v1"]`
- `conversation_mode.kind`: `primary_assistant`
- one user message with `content_type: multimodal_text`
- first content part: `content_type: image_asset_pointer` with `sediment://file_...`
- attachment fields: `id`, `size`, `name`, `mime_type`, `source: local`, `is_big_paste: false`, `width`, `height`
- second content part: the text request asking GPT to inspect the whole Storyboard

Sanitized payload example: `docs/fixtures/prompt_ref_storyboard_multimodal_request.example.json`

## Required GPT output

One JSON object with exactly these top-level data groups:

- `panel_count`
- `image_prompts`
- `video_prompts`

Image and video prompts are separate arrays. Slot numbers must match and be continuous. SnapGen writes them to:

- `prompt_bank_image.txt` using `Image Slot N:`
- `prompt_bank_video.txt` using `Video Slot N:`

## Character identity

The image controls framing, direction, action, objects, depth, and lighting. The first Context JSON controls canonical character names. For example, use `แบงค์` instead of repeatedly writing `เด็กชาย` when the character can be matched safely.

## State that must be persisted for every new Prompt-Ref story

- `conversation_url`
- `conversation_id`
- `parent_message_id`
- `account_alias`
- `chrome_profile`
- `storyboard_transport_version`
- `storyboard_transport_model`
- `storyboard_transport_note`

Every continuation must be pinned to the saved `account_alias`. If the account does not match, stop instead of routing to another login.

## Do not regress

Do not change the Storyboard reader to any of the following:

- a fresh Vision conversation
- the image-generation conversation/history
- `model=auto`
- `picture_v2` for normal image analysis
- a different image selected by filename search or newest-file guessing
- combined image/video prompt text

The active code path is `_generate_prompts_from_storyboard_image()` → `_prompt_ref_chat(..., require_history=True)`.

## Debugging with embedded Chrome

Use the profile saved in Prompt-Ref state, normally:

`%LOCALAPPDATA%\TidMunStudio\SnapGenChromeProfile\Default`

Open the exact saved `conversation_url`, not a similarly named chat. Attach the Storyboard file from the result box, send a short panel-count request, and inspect the POST to `/backend-api/f/conversation` through CDP Network events. Never capture from normal Chrome or from the image-generation page.
