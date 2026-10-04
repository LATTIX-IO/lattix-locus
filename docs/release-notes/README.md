# Release notes

One fragment per PR that bumps MINOR or MAJOR (optional for a PATCH), named
after the change. Format and rules: [docs/VERSIONING.md](../VERSIONING.md),
section 5. CI (`python locus_tooling/versioning.py check`) validates every added
or edited fragment.

```markdown
Release-Impact: minor
Version: 0.3

What changed for users, in one or two sentences.
Action required: ... (only for a breaking change)
```
