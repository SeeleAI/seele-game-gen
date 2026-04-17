# Example: Multi-turn iteration on the same game

Demonstrates the `continue` flow, the important 409 guard, and how the agent picks up `game_id` across turns.

## Conversation

**User (turn 1):** Let me play a 3D fruit-slicing game.

**Agent:** "3D works with `threejs` (faster) or `unity` (higher fidelity) — which would you prefer?"

**User:** threejs is fine.

```bash
python scripts/seele_client.py create \
  --prompt "A 3D first-person fruit-slicing game. The player slices fruits flying through the air by swinging a sword. Fast arcade pace, vibrant colors." \
  --engine threejs \
  --model Seele01-flash \
  --wait
```

→ finishes → agent presents the game. `game_id = canvas_game_xyz`.

---

**User (turn 2):** Nice! Can you make the fruits come in waves and add bombs I need to avoid?

**Agent (internal):** User wants to modify the *existing* game, not start over. Use `continue` on `canvas_game_xyz`. The game just finished, so it should be ready to continue. Describe the *change*, not the whole game.

```bash
python scripts/seele_client.py continue canvas_game_xyz \
  --prompt "Instead of a steady stream of fruit, spawn them in timed waves. Also introduce bombs mixed into the waves — slicing one ends the run." \
  --wait
```

→ finishes → agent presents the updated version.

---

**User (turn 3):** Add a combo system.

```bash
python scripts/seele_client.py continue canvas_game_xyz \
  --prompt "Add a combo system: consecutive successful slices within 1 second increase a multiplier, resets on a miss." \
  --wait
```

---

## Handling the "continue too early" race

If the agent (or the user) calls `continue` while the game is still generating, the CLI surfaces this:

```json
{
  "ok": false,
  "error": {
    "code": "GAME_ALREADY_PROCESSING",
    "message": "game is still generating, continue is not allowed yet",
    "guidance": "The game is still generating. Wait for it to finish before calling continue."
  },
  "hint": "Call `seele_client.py wait canvas_game_xyz` first, then retry continue once finished."
}
```

The agent should then:

```bash
python scripts/seele_client.py wait canvas_game_xyz
# ...then retry the continue command.
```

## Recovering `game_id` after a context loss

If the agent loses track of the current `game_id` (e.g., a long conversation where the ID scrolled out), query local history:

```bash
python scripts/seele_client.py recent --limit 5
```

Returns the most recent games with their prompts, engines, and last known status — usually enough to identify "the one the user is talking about". If ambiguous, ask the user to confirm which game they mean before calling `continue`.

## When *not* to use continue

If the user says something like "let's make a completely different game now" or "forget that one, I want to try X", **confirm first**:

> "Just to check — do you want a brand-new game, or keep iterating on *{game_title}*?"

For a new game, drop the current `game_id` and call `create` with a fresh prompt. Mixing unrelated requests into `continue` on the old game wastes the prior context and usually produces muddled results.
