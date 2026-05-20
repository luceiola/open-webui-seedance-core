---
model: seedance-2.0
version: 2026.05
reliability: official
tags:
  - rules
  - safety
  - grounding
---

# Seedance Prompt Core Rules

## Scope
- This rule card applies to prompt optimization outputs intended for Seedance 2.0.
- Optimizer output must be factual, controllable, and directly executable by users.

## Mandatory Rules
1. Preserve user intent; optimize expression quality, not business meaning.
2. Avoid unverifiable details when references do not provide evidence.
3. Keep camera language explicit: subject, shot scale, movement, light, mood.
4. Prefer concise and deterministic wording over poetic but ambiguous language.
5. If a requirement conflicts with model constraints, flag risk and provide fallback.

## Prohibited Patterns
- Hallucinating visual elements not present in user prompt or reference material.
- Mixing multiple incompatible styles without transition or hierarchy.
- Producing prompt content that implies hidden tool results.

## Output Requirements
- Optimized prompt should be copy-ready.
- Risk checks should explicitly call out ambiguity and constraint conflicts.
