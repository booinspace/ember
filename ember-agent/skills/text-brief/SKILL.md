---
name: text-brief
description: Produce a concise, faithful brief of text supplied by the user.
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  ember:
    requires:
      tools: []
  openclaw:
    category: writing
---

# Text brief

Use only the text in the user's current request. Load `references/format.md` with
`skill_view` before writing the brief. Preserve names, dates and uncertainty; do
not add outside facts. If the source is missing, say what is needed. Submit the
completed brief using `ember_finish`; do not call unrelated tools or save memory.
