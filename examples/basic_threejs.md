# Example: Basic threejs game generation

Simple end-to-end flow with no reference files.

## Conversation

**User:** I want to make a simple game where you dodge asteroids.

**Agent (internal reasoning):** Clear gameplay (dodge asteroids). 2D vs 3D is ambiguous — "dodging" could be either, but "asteroids" often suggests 2D top-down. Confirm briefly.

**Agent → User:** "Nice — quick check: 2D top-down (like the classic arcade) or 3D?"

**User:** 2D, like the arcade one.

**Agent (internal):** 2D → engine = threejs (automatic, no need to ask). Prompt is converged. Start generation.

## Commands run

```bash
python scripts/seele_client.py create \
  --prompt "A 2D top-down arcade-style game where the player pilots a small spaceship and must dodge incoming asteroids of varying sizes and speeds. Classic retro feel." \
  --engine threejs \
  --model Seele01-flash \
  --wait
```

## What the user sees during the wait

While `--wait` is blocking, the agent reports progress to the user periodically (it can observe `stderr` lines from the CLI, or check back in manually with `status` if preferred). Example mid-flight message:

> "Still generating — currently on *"composing scene"*. I'll ping you the moment it's done."

## When it finishes

The CLI returns (abbreviated):

```json
{
  "ok": true,
  "status": "finished",
  "data": {
    "game_id": "canvas_game_abc",
    "game_title": "Asteroid Dodger",
    "generation_status": "finished",
    "summary": "A retro top-down arcade game...",
    "preview_url": "https://seeles.ai/game/generation/canvas_game_abc"
  }
}
```

**Agent → User:**

> ✅ **Asteroid Dodger** is ready.
>
> A retro top-down arcade game where you pilot a ship through a field of oncoming asteroids.
>
> 🎮 Play it: https://seeles.ai/game/generation/canvas_game_abc
>
> Try it out — if you want to tweak anything (enemy speed, controls, visuals), just say so.
