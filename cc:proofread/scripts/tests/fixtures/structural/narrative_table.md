---
type: finding
audience: vault
---

# Segment-level read

## Summary

All segments are essentially flat. There is no movement worth flagging
at the segment cut.

## Segment table

| segment | baseline | current | delta |
|---|---|---|---|
| A | 12.0% | 12.1% | +0.1pp |
| B | 8.0% | 8.2% | +0.2pp |
| C | 1.0% | 2.0% | **+1.0pp (+100% relative)** |
| D | 14.0% | 14.1% | +0.1pp |

The cell for segment C is +100% relative to its baseline.

## Methodology

Segments are defined per the standard segment registry.
