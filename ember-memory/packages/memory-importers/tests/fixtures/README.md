# Synthetic fixtures only

`conversations.synthetic.json` is handcrafted and contains no real personal data.
It exercises branches, null graph nodes, timestamps, structured references, and
reasoning exclusion. Its shape is a test case, not evidence that the pending
export uses this format. Real sanitized structural fixtures are a phase 0 output.

Keep actual archive content in ignored local storage. Inspection reports contain
shapes/counts/hashes, never message text or raw reasoning. Any real-derived fixture
must replace content and identifiers, preserve the relevant structure, and pass
privacy review before being committed.

`test_phase_zero_formats.py` builds additional original synthetic sources for array,
wrapper and single-conversation roots, numbered shards, nested ZIPs, directories,
code in `text`, opaque `.dat` assets, unknown metadata, and parser/safety failures.
These follow public-format hypotheses documented in `docs/phase-0-research.md`;
none is a sanitized fixture derived from the pending personal export.
