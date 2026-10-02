# Bapsim project instructions

- `docs/reference/밥심_HA_AI_Matter_컨텍스트_요약.md` is the current architecture and implementation order. Treat it as reference material.
- `docs/reference/밥심_Matter_구현_보고서_초안.pdf` is planning background; do not use its older board-side analysis architecture as the implementation source of truth.
- The current scope is Home Assistant reception onward. Nordic firmware belongs to another teammate.
- Keep input adapters raw. Do not add detection or AI to the simulator.
- Preserve the raw event boundary in `inputs/base/contracts.py`; a future Matter adapter must feed the same normalizer boundary.
