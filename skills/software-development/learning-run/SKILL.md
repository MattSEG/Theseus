---
name: learning-run
description: "Retrieve and evaluate prior harness changes."
version: 0.1.0
author: Matt, Hermes Agent
license: MIT
platforms: [macos, linux, windows]
tags: [Testing, Evaluation, Memory]
---
# Learning Run

Use when the operator explicitly requests a bounded harness experiment. Group attempts under a stable recurring problem name. When the name is unknown, run `./theseus learning find "terms from the observed failure"`. Inspect the matching results and their human decisions. Reuse the same problem name or pass `--prior RUN` to begin when a related experiment belongs to another problem. The journal under the active profile is separate from conversation memory.

## Procedure

1. Reproduce the workflow and inspect the saved artifact. Use `terminal` to invoke this skill's `scripts/run.py begin PROBLEM --job REQUEST_KEY --baseline OBSERVATION` with Python. Add `--brief` when reading prior attempts for a proposal. It keeps outcomes and decisions and identifies each historical patch by size and SHA-256. Use `evidence RUN` without that flag to inspect the complete original patches. Read the returned prior attempts before suggesting any change. If the problem already has a name, reuse it.
2. For a fresh model proposal from the admitted evidence, run `./theseus learning draft RUN`. Add `--reuse` on the first call and its retries to recover the same saved model response after a lost acknowledgement. It returns a preview with cited run IDs, the history shown and the number omitted. Inspect and edit the preview before recording it. Record `propose RUN --change TEXT --rationale TEXT --expected TEXT`. Explain which prior result influenced the proposal, including a failure or a superseded conclusion where relevant.
3. Pipe the smallest reviewed source patch into `stage RUN --diff -` to make it available in Desktop before testing. Run regressions and fresh cases, relaunch the app, and inspect actual saved results. Record `evaluate RUN --diff - --candidate TEXT --fresh TEXT --recommendation keep|revert`, piping the tested source diff on stdin if it changed. Omit `--diff` to reuse the staged patch. Include model, harness revision, workload and counts in the observations. Never ingest credentials, complete logs, or profile configuration.
4. Return the local HTML report as a clickable absolute file link for Desktop preview. Ask the operator to review it. Record their decision using `review RUN --decision keep|revert --reason TEXT` only after they respond. Apply an accepted change for a real task, or restore a rejected candidate. Begin the next experiment manually.

## Verification

After interruption, run `resume REQUEST_KEY`. Inspect the verified report and next phase before retrying anything. Reuse the original key and baseline on a repeated begin. A matching report stays untouched; the journal can repair a partial or missing report.

Use `evidence RUN` to inspect exactly which prior phases were available at admission, even if a human has since changed the decision on a prior experiment. Use `show RUN` to rebuild a report from SQLite. The CLI rejects repeated or out-of-order phases. Retrieval includes every earlier attempt under the same name, including failed and pending experiments.

## Independent proof

From the Theseus checkout, run `./theseus proof review REQUEST.txt ARTIFACT --reference SOURCE.md`. This starts a fresh subscription-backed reviewer session with the original request and the actual saved artifact. Select only the files needed. Omit the reference for self-contained tasks. Inspect the per-requirement verdicts in the returned JSON receipt. Use `./theseus proof check RECEIPT.json` to verify the reviewed inputs are still current. Rendered appearance and external effects need separate evidence.

## Inspect a visual change

Use the Desktop Browser toolbar’s **Download preview screenshot** before and after the action. Give the two PNG files distinct names. From the checkout run `./theseus vision BEFORE.png AFTER.png --question "Which edition changed status?"`. Request a bounded inspection with `--region LEFT TOP RIGHT BOTTOM` in original screenshot pixels. Inspect the comparison PNG and JSON receipt. For an unchanged region the command records zero changed pixels and skips the model call.

## Resume an artifact delivery

For a bounded local delivery, use `./theseus deliver file JOB_KEY SOURCE DESTINATION` in the checkout. Select a regular artifact of up to 16 MiB and an existing destination directory. The isolated profile records the admitted bytes. After interruption, use `./theseus deliver list` to find the original key. Use `./theseus deliver inspect JOB_KEY` to compare the current destination with admitted bytes and see whether publication was acknowledged. These two commands leave the job and destination untouched. To continue the job, use `./theseus deliver resume JOB_KEY` and read the verified path and digest. A conflict or a missing previously delivered file requires operator judgment. Open the verified output to check its contents against the request.

For self-contained HTML, add `--render screen` or `--render print` to the proof review command. These modes start a fresh local Chrome context and review text from its layout at 1280 × 900. The receipt records the media, browser version, source hash, and layout-text hash. Use visual inspection for color, overlap, clipping and other pixel-level claims.

Use `./theseus vision --check RECEIPT` to verify the two original PNGs, region measurements, and comparison image. An unchanged receipt works offline. If the inputs changed, run the comparison again on the intended captures before relying on the old interpretation.

When an operator explicitly replaces an earlier reviewed conclusion, add `--supersedes OLD_RUN` to their new `review` command. The two reports link to each other, and the older run keeps its original decision. Search results identify its replacement.

Attach selected local test outputs and artifacts to an evaluation with repeated `--file "Label" PATH` options. The report links to the originals and the journal records their byte counts and digests. Run `check-files RUN` before relying on the linked files later.

For a single screenshot, run `./theseus vision locate CAPTURE.png "visible control label"`. The first vision session selects a grid cell. If it identifies one, a separate session inspects original pixels around the proposed location. The returned state distinguishes a verified crop, an unverified proposal, and an element the first session could not find. Coordinates refer to the original captured viewport.

For a combined candidate with a local Git revision recorded in its report, inspect the exact tested sources with `git show REVISION:PATH` or compare the candidate to its base with `git diff REVISION^ REVISION`. Review the candidate at the recorded revision even if later experiments have changed the working directory.

Use `./theseus vision --check RECEIPT` on a saved location to recheck the original capture, drawn grid, recorded crop and consistency of the stored verdict. This offline check does not repeat the visual interpretation.

To review a Desktop crash task, save the output of `./theseus crash verify TRIAL --case CASE --details` as JSON, including when verification reports a failed case. Pass the original request and this file to `./theseus proof review`. The reviewer gets saved edition states and write history checked against the local trial database. A subsequent `proof check` detects changes in those observations.

Add `--expect unchanged` when a selected screenshot region must be preserved, or `--expect changed` when it must differ. A violated expectation returns exit 2 and still writes the comparison receipt. Checking that receipt later verifies the recorded result, including a faithfully recorded failure.

For a candidate already frozen in local Git, stage its exact source with `stage RUN --between BASE CANDIDATE --path FILE`. Repeat `--path` for additional source files. The report stores the resolved commit IDs and the patch generated directly between them. This also works for files absent from the active branch index.

On the Mini, `vision locate CAPTURE "Review proof" --exact-text` recognizes visible text locally. It returns every matching text rectangle in original screenshot pixels. If several labels match, inspect their context before selecting a control. The rectangles bound recognized text, and a missing result can reflect an OCR miss.

For saved pytest or Vitest JUnit XML, use `./theseus proof review REQUEST.md RESULT.xml`. The reviewer receives counts derived from individual testcase records, including failures, errors and skips. A conflicting suite total stops the review.

For a repeated label in vertically stacked sections, use `./theseus vision locate CAPTURE.png "Review proof" --exact-text --between-labels "Insert" "Finish batch" --require-unique`. Both boundary labels must be unique and in order. The receipt lists whole target boxes in the intervening horizontal band, using original screenshot coordinates. A nonzero exit signals that a unique label could not be established.

When the original request names a local delivery, add `--delivery JOB` to the proof review. The reviewer can cite the acknowledged destination and verified bytes alongside the artifact. A file saved locally provides no evidence about copies elsewhere.

For a table with centered labels, use `vision locate CAPTURE.png "Review proof" --exact-text --at-intersection "Jackets" "Screen" --require-unique`. Both headings must appear exactly once. The row heading must be to the left and the column heading above the target. Their center lines must cross the target text.

Use `./theseus learning audit --from-run 1` to recheck the files registered by each evaluated run. The summary lists uncovered runs and changed, missing or unreadable files. It exits zero when the selected evaluations all have registered evidence and every file matches. An empty range exits 2.

After an interrupted structured chat, `./theseus chat-receipt KEY` reads the original admission offline. The outcome belongs to that admission. Message and tool-result counts cover its entire session. When the outcome is unknown, check the saved artifact or the delivery receipt before starting another job.

Use `proof review REQUEST FULL.xml --suite-prefix src/` to verify one directory against the original JUnit file. The reviewer receives overall outcomes plus counts for cases whose immediate containing suite name starts with the selected prefix.

For a manually triggered comparison, review the fresh chat transcript and reopened saved file separately against the same original request, acceptance criteria and reference. Run `./theseus learning observe RUN CASE --chat CHAT_RECEIPT --artifact FILE_RECEIPT`. Read the selected next test and its reason in the run report. Reuse the criteria when changing cases, freeze the candidate before transfer, and record the actual result before accepting a change.

Before sending a transfer chat, freeze its original evidence with `./theseus learning trace-plan RUN CASE --evidence PATH` (repeat the option for additional sources). Open a fresh Desktop chat, retain its exact final answer, and pass the originating session ID with `learning observe … --session ID`. The check pairs persisted calls with results, verifies numbered text against the frozen source bytes and binds the original request and final answer to the reviews. An outside file returned by the reader fails. Missing traces, shell calls and other unverifiable tools leave independence unverified. Older completed runs retain their recorded decisions. For a deliberately constrained trial use the fixed EVIDENCE_ONLY prefix in transfer_trace.py, followed by the task. An optional second task line, “Record (read_file): PATH”, can identify a frozen input for the chat separately from the artifact acceptance criteria.

To test native investigation reads, freeze the original sources with `trace-plan RUN CASE --investigation PROJECT CONCEPT --evidence FILE` before opening the chat. The evidence-only protocol in `transfer_trace.py` withholds ordinary project conclusions for that test. The checker requires a project-context receipt and replays native lookup results against the archived source bytes. Keep source independence separate from content and artifact requirements.

For a bounded reviewer study, use `reviewer-trial RUN KEY --mode specific --case RUN/CASE` with two to twelve independently measured positive and negative traces (repeat the case option). Send the exact packet in a fresh Desktop chat without tools, then run `score-reviewer RUN KEY --session SESSION`. Compare with overall mode and held-out cases. A Brier score of 0 is perfect, and always saying 50% scores 0.25. Enable optional Seer history with `./theseus shadow watch reviewer RUN`. Use reliable predictions to prioritize evidence collection; keep the measured outcomes authoritative.


For a measured investigation trial, `trace-plan ... --claim '{"scope":{"Room":"West"},"field":"Seal count"}' --max-reads 1` freezes the field, scope and read budget. The live native lookup retains four recent exhibits and inspects up to 32 earlier attached entries for a structured claim. Its search receipt names the sources it actually checked. For a text, Markdown, log or CSV proof, the reviewer sees the original line breaks. With `proof review REQUEST CHAT_ANSWER --source-trace RUN/CASE DESKTOP_SESSION` the actual producing chat and registered sources are verified separately from the artifact content. Use `--require-source-trace` when proof of the source method matters but a producing session has not been supplied.

Use repeatable `--source-receipt` options with the investigation's complete record, snapshot and SHA-256 citation to register archived versions of one file. This scopes permission to the exact archived record. A later version at the same path is a different input. The trace verifier checks every inspected snapshot, including bounded search records omitted from the displayed passages. Field-label title case is reconciled only for ordinary words. Case-sensitive values and units stay distinct.

In packaged Theseus Desktop, run the bundled proof reader using the app’s own Python interpreter. Under the standard Mini install these are `~/Applications/Theseus.app/Contents/Resources/python/bin/python3` and `~/Applications/Theseus.app/Contents/Resources/backend/skills/software-development/learning-run/scripts/proof.py`. Pass `-B` to the bundled Python so it never writes bytecode into the signed app. The Desktop environment supplies the active Theseus profile. Use the same interpreter and script for `check RECEIPT`. The fresh reviewer uses the app's bundled backend.

For a manually requested causal-rule comparison on the Mini, run `python3 scripts/theseus-rule-trial/run.py` from the Theseus checkout. It uses 18 fresh packaged-app chats across Sol and Opus. The local plan and results live in the trial directory it prints. Inspect the saved answers, trace scope, and costs together. To rescore while the trial workspace is available, use `--score TRIAL_DIRECTORY`. A tie keeps the current default.
