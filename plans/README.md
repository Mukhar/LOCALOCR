# LOCALOCR — Feature Roadmap (reference)

> **This directory is a superseded reference.** The canonical GSD-formatted
> phase plans now live under [`.planning/`](../.planning/).
>
> Use those for execution. The docs here remain as a plain-English
> summary for anyone who wants the tl;dr without opening the GSD structure.

## Where to look now

| Want... | Open |
|---------|------|
| Project charter | [`.planning/PROJECT.md`](../.planning/PROJECT.md) |
| Full requirements list | [`.planning/REQUIREMENTS.md`](../.planning/REQUIREMENTS.md) |
| Phase sequencing | [`.planning/ROADMAP.md`](../.planning/ROADMAP.md) |
| Current progress | [`.planning/STATE.md`](../.planning/STATE.md) |
| Phase 1 (scene extraction) plans | [`.planning/phases/01-scene-extraction/`](../.planning/phases/01-scene-extraction/) |
| Phase 2 (transcript attribution) plans | [`.planning/phases/02-transcript/`](../.planning/phases/02-transcript/) |
| Phase 3 (web UI) plans | [`.planning/phases/03-web-ui/`](../.planning/phases/03-web-ui/) |

## Reference summaries

Kept for the plain-English reading experience:

- [PHASE_1_scene_change_extraction.md](./PHASE_1_scene_change_extraction.md)
- [PHASE_2_speaker_attribution.md](./PHASE_2_speaker_attribution.md)
- [PHASE_3_bonus_web_ui.md](./PHASE_3_bonus_web_ui.md)

## Executing a phase (GSD-native)

From the repo root:

```bash
# Kick off execution of Phase 1's first plan
/gsd-execute-phase 1

# Or plan out further detail on a phase before executing
/gsd-plan-phase 2

# Check overall status any time
/gsd-progress
```
