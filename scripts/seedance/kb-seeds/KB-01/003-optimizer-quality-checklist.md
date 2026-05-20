---
model: seedance-2.0
version: 2026.05
reliability: internal-reviewed
tags:
  - checklist
  - quality
  - optimizer
---

# Optimizer Quality Checklist

Use this checklist before returning optimized output.

## Structural Checks
- `optimized_prompt` is non-empty and copy-ready.
- Language follows user preference.
- Prompt contains clear scene/action/camera/style cues.

## Control Checks
- No undefined abbreviations or private shorthand.
- No impossible or contradictory camera instructions.
- Time and rhythm wording is explicit if duration is requested.

## Safety and Hallucination Checks
- No fabricated reference details.
- No fabricated tool results or IDs.
- Risks are explicit when evidence is weak.

## Evidence Checks
- At least 2 KB evidence entries used for optimization.
- Source refs are included in output trace.
