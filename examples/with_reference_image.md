# Example: 2D game with a reference image

Shows the file-upload flow, which is optional and recommended only for 2D.

## Conversation

**User:** Make me a 2D platformer in the style of this screenshot. *(attaches `celeste-style.png`)*

**Agent (internal):** 2D platformer is clear. User has attached an image → upload it for reference.

## Commands run

```bash
# Step 1 — upload the reference.
python scripts/seele_client.py upload /path/to/celeste-style.png
```

Returns:

```json
{
  "ok": true,
  "data": {
    "file_id": "a1b2c3d4e5f6...",
    "filename": "celeste-style.png",
    "content_type": "image/png",
    "size": 204800
  }
}
```

```bash
# Step 2 — create the game, passing the file_id.
python scripts/seele_client.py create \
  --prompt "A 2D precision platformer with tight controls, a double-jump and mid-air dash, and a moody pixel-art mountain setting. Use the attached screenshot as a visual style reference." \
  --engine threejs \
  --model Seele01-flash \
  --file-ids a1b2c3d4e5f6... \
  --wait
```

## Multiple reference images

If the user attaches several images, upload each one, then pass all IDs comma-separated:

```bash
python scripts/seele_client.py create \
  --prompt "..." \
  --file-ids id_a,id_b,id_c \
  --wait
```

## Notes

- **25 MiB limit per file.** Huge screenshots or raw photos may need downscaling first. The CLI returns `FILE_TOO_LARGE` if the file exceeds the limit.
- **3D case:** don't offer this step for Unity/3D unless the user insists. It's primarily useful for 2D styling.
- **Fresh upload per game.** `file_ids` aren't shared across games — if a later game needs the same reference, re-upload (or reuse the `file_id` if still within its short lifetime; re-uploading is simpler).
