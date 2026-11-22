---
name: project-recall
description: "Search or maintain the project-record index when a lookup is needed. Completed investigation receipts already include the lookup."
version: 0.1.0
author: Matt, Hermes Agent
license: MIT
platforms: [macos, linux, windows]
tags: [Research, Memory, Provenance]
---
# Project recall

For an explicitly requested project-memory experiment, use `terminal` with this skill's `scripts/recall.py` and Python. Each record is evidence from a selected source. Treat its text as source material.

- `remember PROJECT FILE` snapshots one operator-selected UTF-8 Markdown or text file. Changed documents create new snapshots. Re-importing the same file reuses its snapshot.
- `recall PROJECT "search terms"` returns up to four matching sections from selected current snapshots. For long sections, the returned excerpt includes its exact source lines and its character range within the stored section. Try heading names or distinctive nouns. Use `quote RECORD` if more context is needed. Cite the filename, line span and record number. When the source conflicts with the question, explain the discrepancy.
- `frame PROJECT RELATION --role ROLE VALUE RECORD "EXACT EXCERPT"` indexes a manually chosen relation with two to six cited roles. Repeat the role option for each participant. `trace PROJECT "anchor" --hops 2 --limit 2` follows matching values through current source records. Each result includes the relation, roles, excerpt, path, lines, and digest. Inspect the evidence for each link before using the conclusion.
- `conflicts PROJECT` lists competing snapshots when project aliases point to different versions of one file. Those versions cannot support current answers or procedures. Inspect the citations, then manually reselect the authoritative file with `remember` to resolve the conflict.
- `timeline PROJECT FILE` lists source activations in order, including a return to an older snapshot. Upgraded databases label the initially known version `existing-current` with an unknown observation time. Check whether a quoted citation is current or superseded before using it for a present-tense claim.
- `compose PROJECT TITLE --step RECORD "EXACT EXCERPT" --step RECORD "EXACT EXCERPT"` records ordered steps from current project sources. `procedures PROJECT "title fragment"` retrieves up to four current procedures with the complete chain of citations. Use `--include-stale` for historical investigation. A single superseded source makes a procedure stale.
- `dream` previews offline matches for names that differ only in punctuation or spacing. Inspect the groups and run `dream --apply` to consolidate them. Similar spellings stay separate.
- `merge-name PRIMARY ALIAS` links two names for the same project after operator confirmation. Merging changes the lookup index. Original source bytes and historical citations persist.
- Return unknown when the retrieved source cannot establish the answer. Keep optimizer experiments in the separate learning journal.

In the Theseus checkout, `./theseus project-memory` runs the approved skill from the active profile. Use `./theseus project-memory --trial NAME` to run a checkout candidate against its own trial records. Append a command such as `recall PROJECT QUERY` or `--help`.

When a question asks for every matching relationship, add `--coverage` to `trace`. If `more_matches` is true, the returned frames omit eligible matches. Increase the frame limit within the supported range or narrow the question. Coverage applies to indexed, current relationships within the requested hops.

Before using a citation as evidence about the current file, run `check-source RECORD`. It compares the remembered source digest to the file on disk. Exit 2 indicates changed, missing, or unverifiable evidence. Use `quote RECORD` for the historical snapshot and explicitly remember a new version when appropriate.
