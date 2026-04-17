---
name: seele-game-gen
description: Generate playable games from a natural-language description using Seele's game-generation API. Use this skill whenever the user wants to create, build, make, or generate a game — whether they say "make me a game", "I have a game idea", "build a 2D puzzle game", "a 3D adventure game about X", or anything similar. Also use it when the user wants to modify, iterate on, or continue a game that was previously generated through this skill. Handles the full lifecycle — creating the game, polling until it finishes (generation can take 5-25 minutes), displaying results, and supporting multi-turn iteration on the same game. Supports optional reference images for 2D games.
---

# Seele Game Generation

Turn a user's game idea into a playable game by orchestrating Seele's generation API. The skill provides a CLI (`scripts/seele_client.py`) that handles HTTP, authentication, polling, and local game-history persistence. Your job is to drive that CLI based on the conversation.

## Setup — do this before the first call

The skill needs one environment variable. Check it before the first API call; if it's missing, ask the user to set it rather than guessing.

- `SEELE_API_KEY` — the user's API key. Visit [https://code4agent-feature-games-openapi-web-merge.seele.chat/api](https://code4agent-feature-games-openapi-web-merge.seele.chat/api) and click the **"Get API Key"** button to create one. The key is shown only once at creation, so the user must copy it immediately. The key looks like `c4a_sk_...`.

If `SEELE_API_KEY` is not set, the CLI returns a `MISSING_API_KEY` error — relay that to the user and ask them to export the key.

## The main flow

```
user describes a game idea
  → converge on 2D vs 3D and a clear prompt (short dialogue, not a form)
  → [if 2D with a reference image] upload the image
  → create the game (returns game_id, ~5-25 min generation time)
  → poll until finished, reporting progress to the user
  → show the preview link and summary
  → user asks to change something → continue on the same game_id
  → user wants a brand-new game → confirm, then start over with a new game_id
```

## Step 1 — Converge on a prompt

Before calling the API, make sure you understand two things. Use a couple of conversational turns to get there — not a questionnaire.

1. **Core gameplay** — what does the player actually do? (dodge, solve, shoot, jump, build, explore…)
2. **2D or 3D** — this decides the engine.

Other details (art style, theme, mood, mechanics) only matter as much as the user volunteers. Don't interrogate the user. **Aim to start generation within 2-3 exchanges.**

### Engine choice

| User's intent | Engine | Notes |
|---|---|---|
| 2D game | `threejs` | Pick automatically. |
| 3D game | ask the user | "Would you prefer `threejs` (faster, lighter) or `unity` (slower, higher fidelity)?" |

Unity only generates 3D games.

### Model choice

Default to `Seele01-flash`. Only switch to `Seele01-pro` if the user explicitly asks for the "pro" or "premium" or "best" model. Pro requires a paid subscription — if the API returns `SUBSCRIPTION_REQUIRED`, tell the user and fall back to flash.

## Step 2 — (Optional) Upload a reference image for 2D games

Reference images are **optional and only useful for 2D**. For 3D, skip this step unless the user insists.

Offer it casually once per game if the user hasn't already attached an image:

> "For a 2D game I can take a reference image if you have one — style, mood, that sort of thing. Otherwise I'll just go on the description."

When the user provides a local image file, upload it:

```bash
python scripts/seele_client.py upload <path>
```

That prints a JSON result with a `file_id`. Hold onto that ID and pass it to the `create` call via `--file-ids`. You can pass multiple IDs comma-separated (`--file-ids id1,id2`).

**Constraints:** max 25 MiB per file; only common image formats are useful here.

## Step 3 — Create the game

Pass the converged prompt, engine, model, and any file IDs:

```bash
python scripts/seele_client.py create \
  --prompt "<the full converged description>" \
  --engine threejs \
  --model Seele01-flash \
  --wait
```

**About `--wait`:** when present, the command blocks and polls until the game finishes (or times out at 30 min). This is usually what you want in an agent loop because your next message to the user depends on the result. If you prefer to poll manually, omit `--wait`, note the returned `game_id`, and call `wait` or `status` separately.

The create response includes `meta.estimated_time_minutes` — relay this to the user so they know what to expect:

> "I've kicked off generation. Estimated time is about 8 minutes; I'll let you know when it's ready."

Store the returned `game_id` — every subsequent operation (status, continue) needs it. The CLI also persists it locally in `.seele_games.json`, and `python scripts/seele_client.py recent` lists recent games if you lose track.

## Step 4 — Poll (if you didn't use --wait)

If you didn't pass `--wait`, use either:

- `python scripts/seele_client.py wait <game_id>` — blocks until finished or timeout.
- `python scripts/seele_client.py status <game_id>` — one-shot check, returns the current state and moves on.

Choose `wait` when the user is actively waiting for the result. Choose `status` when the user asked a passing "how's it going?" — report the `current_step`, then hand control back.

**If `wait` returns `"status": "timeout"` — this is not failure.** The generation is still running server-side. Tell the user the job is still in progress and resume polling later with another `wait` call on the same `game_id`.

## Step 5 — Present the result

When `generation_status` is `finished`, the payload includes:

- `game_title` — name of the game
- `summary` — short description of what got built
- `preview_url` — link to play the game in browser (always present)
- `game_project_url` — source-project download link. **Only present for Pro-tier users.** If missing, `game_project_url_access_message` explains why; pass that along rather than hiding it.

Present it like this (adapt the wording to the conversation):

> ✅ **{game_title}** is ready.
>
> {summary}
>
> 🎮 Play it: {preview_url}
> 📦 Project download: {game_project_url}  *(only if present)*

Then invite iteration:

> "Try it out — if you want to tweak anything, just tell me what to change."

## Step 6 — Multi-turn iteration

When the user asks for a change to a game that's already finished, use `continue` with the **same** `game_id`:

```bash
python scripts/seele_client.py continue <game_id> \
  --prompt "<describe the change, not the whole game>" \
  --wait
```

**Key rules for continue:**
- The game must be `finished`, not still `processing`. The API returns `GAME_ALREADY_PROCESSING` (409) otherwise. If you see that error, run `wait` first, then retry.
- The prompt should describe the *change* (e.g., "add a double-jump and make the enemies faster"), not re-describe the whole game. The backend has the prior context.
- File IDs are still optional — upload a new reference image only if the user provides one.

### When the user wants a *new* game (not a modification)

If the user says something that could be interpreted as either "modify the existing game" or "make a different game", **confirm before starting fresh**:

> "Do you want to make a brand-new game, or change the current one (*{game_title}*)?"

For a new game, drop the current `game_id` from working memory and restart from Step 1.

## Error handling

The CLI emits structured JSON errors on stdout (and logs to stderr). Every error has `error.code`; some also include `error.guidance` with a suggested next step. Common codes:

| Code | What it means | What to do |
|---|---|---|
| `MISSING_API_KEY` | Env var not set | Ask the user to export `SEELE_API_KEY`. |
| `UNAUTHORIZED` | Invalid/expired key | Ask the user to create a new key. |
| `SUBSCRIPTION_REQUIRED` | Seele01-pro without subscription | Retry with `--model Seele01-flash`. |
| `GAME_ALREADY_PROCESSING` | `continue` called too early | Run `wait <game_id>` first, then retry. |
| `UPSTREAM_INVALID_RESPONSE` / HTTP 502 | Backend hiccup | Wait a moment and retry once; then surface the error to the user. |
| `NETWORK_ERROR` | Couldn't reach the host | Check the user's network connection. |

For any error not listed here, relay the message to the user rather than guessing.

## CLI reference

Every command prints a single JSON object to stdout. Progress messages go to stderr (won't pollute JSON parsing).

```bash
# Create a new game.
python scripts/seele_client.py create \
  --prompt "<text>" \
  [--engine threejs|unity] \
  [--model Seele01-flash|Seele01-pro] \
  [--file-ids id1,id2,...] \
  [--wait] [--interval 15] [--timeout 1800]

# Continue (modify) an existing finished game.
python scripts/seele_client.py continue <game_id> \
  --prompt "<change description>" \
  [--model ...] [--file-ids ...] [--wait] [--interval ...] [--timeout ...]

# One-shot status check.
python scripts/seele_client.py status <game_id>

# Block-polling.
python scripts/seele_client.py wait <game_id> [--interval 15] [--timeout 1800]

# Upload a reference file; returns file_id.
python scripts/seele_client.py upload <path> [--content-type image/png]

# List recent games (from local history).
python scripts/seele_client.py recent [--limit 10]
```

## Examples

See `examples/` for worked scenarios:

- `examples/basic_threejs.md` — simple one-shot generation with no reference files.
- `examples/with_reference_image.md` — 2D generation with a user-provided reference image.
- `examples/multi_turn_iteration.md` — create → iterate → iterate flow.
