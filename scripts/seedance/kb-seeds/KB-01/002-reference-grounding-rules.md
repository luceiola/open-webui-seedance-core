---
model: seedance-2.0
version: 2026.05
reliability: internal-reviewed
tags:
  - references
  - grounding
  - multimodal
---

# Reference Grounding Rules

## Input Priority
1. User explicit text requirement
2. Shot script (if provided)
3. Confirmed reference assets (image/video/audio)
4. Optional style hint

## Grounding Constraints
- References are evidence anchors, not automatic style transfer.
- If references conflict with user text, prefer user intent and mention conflict.
- Do not infer identity, logo, text content, or exact layout when not visible.

## Conflict Handling
- Missing critical detail: ask for supplement or mark uncertainty in risk checks.
- Ambiguous references: require clearer relative path or stronger descriptor.
- Over-constrained requests: keep highest-priority constraints and list dropped ones.

## Optimizer Implication
- `reasoning` must cite which parts came from text vs. reference anchors.
- `risk_checks` must include at least one grounding risk item when uncertainty exists.
