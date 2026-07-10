# Knowledge Bridge — connecting the agent to your notes vault

The agent gets dramatically better when it can read your notes (project status,
deadlines, decisions) and file its own findings back. But naively syncing your
entire vault to an always-on box is wrong on two counts: blast radius (personal,
financial, private notes the agent has no business reading) and sync integrity
(cloud-drive sync like iCloud doesn't reach non-Apple machines and fights with
git-style tooling).

## The pattern: projection out, inbox in

**Canonical vault stays where it is.** One source of truth, human-owned. The
agent never holds the master copy.

```
   Mac / vault machine                     Agent host (VPS / mini PC)
 ┌─────────────────────┐   git push   ┌──────────────────────────────┐
 │ Canonical vault      │────────────►│ brain-mirror (read-only clone)│
 │ (iCloud/Obsidian)    │  projection │  allowlisted subset only      │
 │                      │             │                               │
 │ _Inbox/Agent/  ◄─────│── git pull ─│ agent-inbox (agent writes)    │
 └─────────────────────┘             └──────────────────────────────┘
```

1. **Projection (read path).** A scheduled job on the vault machine copies an
   *allowlisted* subset (project indexes, status notes, deadline sources) into
   a private git repo (`brain-mirror`) and pushes. The agent host pulls before
   each routine that needs context. Denylist wins over allowlist; personal and
   financial folders are excluded by default and require explicit opt-in.
2. **Inbox (write path).** The agent never edits canonical notes. It writes new
   markdown files to an `agent-inbox` branch/folder: one note per digest,
   finding, or decision, with frontmatter (`date`, `source` routine, `business`,
   `type: digest|alert|decision|research`) and `[[wikilinks]]` to the project
   notes it relates to. The vault machine pulls these into `_Inbox/Agent/` in
   the canonical vault, where you triage in your normal note-reading flow —
   merge into project notes, or delete.
3. **Promotion is human.** Moving something from `_Inbox/Agent/` into a real
   project note is your call (or a supervised skill run on the vault machine,
   where the full vault is legitimately available).
4. **Every agent note carries provenance.** Frontmatter must include `source`
   (which routine/run produced it), `date`, `scope` (which business), and —
   for anything time-sensitive — `expires`. Expired or superseded notes are
   candidates for automatic archival, and any note is revocable: deleting it
   from the vault is the end of it (the mirror never resurrects operator
   deletions). Memory that can't be corrected or retired is a liability, not
   an asset.

## Why not just sync the whole vault?

- **Least privilege:** the vault is your whole life; the agent needs perhaps
  10% of it. An agent host is also the most exposed machine you own.
- **Conflict safety:** two writers on one file tree (you via cloud sync, agent
  via anything) is how notes get corrupted. Projection out / inbox in gives
  each side exclusive write ownership of its own paths — no merge conflicts by
  construction.
- **Auditability:** the projection job's allowlist is a reviewable file; every
  agent note is a git commit.

## Config sketch

```yaml
# config/brain.yml (gitignored)
projection:
  repo: yourname/brain-mirror        # private!
  include:
    - "10 - Projects/**/*index*.md"
    - "10 - Projects/<business>/*Status*.md"
  exclude:                            # wins over include
    - "20 - Areas/Finance*/**"
    - "20 - Areas/Personal/**"
  schedule: "0 6,12,18 * * *"
inbox:
  vault_target: "_Inbox/Agent/"
  note_types: [digest, alert, decision, research]
```
