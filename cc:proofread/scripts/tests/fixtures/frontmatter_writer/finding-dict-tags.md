---
type: finding
date: 2026-05-29
slug: dict-tags
title: "A finding with an unsupported tags shape (dict)"
status: published
tags:
  primary: analysis-quality
  secondary: codex-review
---

# Dict tags

`tags:` as a dict is unsupported YAML for our convention — the writer
must surface this as unreadable rather than guess at flattening.
