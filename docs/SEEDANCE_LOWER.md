# Seedance Lower video

Verified against the public SnapGen Bytedance page on 2026-10-19:
https://snapgen.ai/app/video-gen/bytedance

| Model | API ID | Resolution | Seconds |
|---|---|---|---|
| Seedance 2.0 - Lower | `sd-2-lower` | 720p | 5, 10, 15 |
| Seedance 2.5 - Lower | `sd-2-5-lower` | 720p | 5, 10, 30 |

Both accept 16:9, 9:16, 1:1, 3:4, 4:3 and 21:9. The provider's current
Lower configuration supports ingredient references, up to **four images total**;
it does not expose first/last-frame mode, video refs or audio refs for these models.
The scene is an ingredient with composition instructions, not a guaranteed locked first frame.

## Slot

Select the scene image normally, then open the model menu and choose
**แนบ Ref Seedance (ฉาก / ตัวละคร)**. Add up to three more images and assign
ตัวละคร / สถานที่ / สิ่งของ to each one. Save, then select the Lower model.
The scene is `@image1`; additional refs retain the displayed order and roles.
The prompt binds each image number to its role. Model output still needs visual review.

Reference selections are stored per scene image path in
`snapgen_data/video_seedance_references.json`, independently of other Slots.
Switching scenes does not carry references from the previous scene.

## Auto

Open the story project and select a Lower model in Auto's model dropdown.
The choice is saved in that project's `project.json` as `video_model`.
The reference folder is the one selected on Image AI. Files are matched by
their exact names to the shot's `characters` and `location`; unrelated characters
are excluded. The rendered scene is image 1, followed by its matched references.
More than four total images, a missing file or an oversized shot stops submission
with an actionable message rather than silently dropping inputs.

## Transport and validation

Uses the existing SnapGen API key and `POST /uapi/v1/video-gen/seedance`:
multipart `prompt`, `model`, `resolution`, `duration`, `aspect_ratio`,
`mode=lower`, `mode_image=ingredient`, repeated `ref_images` uploads.
No new credentials are embedded. UUID polling/download and actual credit
measurement use the existing hosted video flow.

Public frontend evidence: `7UtQXePU.js` contains model capabilities and ingredient
selection; `DcFrrLGM.js` contains Bytedance multipart submission; `CM5riiyA.js`
contains the separate duration/credit tables. These chunk names can change.
Tests exercise transport construction, model options, reference roles/order,
capacity limits, per-scene restart persistence, Auto matching and dispatch.
Paid generation and the installed EXE have not been tested or updated by this change.
