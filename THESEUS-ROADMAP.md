# Theseus product roadmap

**Working list · November 21, 2026.** Theseus is a local desktop assistant for science, business research and planning, web information gathering, and personal productivity. A project can use the same sources through any configured model. The first release proposed below should be useful to someone who never writes code.

## Build first — a brief you can use again

Start with a question, the decision it informs, and an optional limit on time or pages. Finish with a brief, an editable table where appropriate, and links to original evidence. Reopening the brief should offer a deliberate refresh and show what changed. The suggested order below is a product judgment. The numbered items are proposals.

| Order | Capability | A usable first version |
|---|---|---|
| 1 | Project source library and quick capture | Drop a file, URL, selected passage, or dictated note into a project inbox. Find it from a description. Open the original local file, Drive document or connected Gemini notebook and show where the match came from. Start with local files and web pages. |
| 2 | Bounded web crawling | Give a research question and optional starting sites. Set page, depth and time limits. Save retrieved pages, dates, relevant passages, failures and duplicates. Provide a one-click stop and resume. |
| 3 | Research briefs and editable comparison tables | Offer a scientific literature review, market or vendor comparison, and personal decision brief. Include units, scope, missing values and citations at the cell or claim that needs them. Let me change the columns. |
| 4 | Refresh and compare editions | On demand, revisit selected sources. Show changed facts beside their prior values and link to saved versions. Preserve my notes and show which conclusions may need review. Schedule a watch only when requested. |

**Packaged-app acceptance.** Run three unrelated tasks in fresh chats: compare vendors including one with unpublished pricing; compare studies that use different endpoints; plan a personal purchase with a budget and deadline. Open a cited passage from each artifact, change a column, quit, reopen, and refresh after a controlled source change. Verify that the affected row updates and the user's edits survive. Count useful sources, manual corrections, elapsed time, and whether the artifact actually helped make the decision.

## Build next — turn findings into work

| Capability | What I should be able to do |
|---|---|
| Business scenarios | Link a quote to a cost assumption. Edit price, volume, lead time or payment terms. Compare scenarios for margin, break-even and cash needs. Show which assumption would reverse a decision. |
| Reproducible scientific analysis | Select data, inspect units and methods, produce a calculation or plot, and trace a point to its source row. Reopen the analysis, add a batch and rerun it. Expose an executable notebook when useful. |
| Reusable workflows | Save a successful job with its inputs, editable steps, expected outputs and decisions requiring review. Run it manually on a new project or batch. |
| Personal inbox and attention view | Capture a link or commitment quickly. Across projects, show what needs my decision, what is waiting on someone, and what is ready to use. Open the exact source or next action. Offer a time-boxed view such as “What can I finish in twenty minutes?” |
| Cross-project discovery | Surface a paper, contact, supplier or method already found elsewhere. Keep its original date and project visible. Let me explicitly reuse it in a second context. |
| Learning from successful use | Let me save the columns, formats and workflows I actually reuse. Suggest local defaults after repeated use and give me a way to inspect or undo them. Use reuse as a reason to investigate value; silence supplies no verdict. |
| Evidence behind an answer | From a conclusion or a changed artifact, open the relevant source passage, previous wording, correction and affected saved output in one place. Test observation, comparative ranking, and the final sentence separately. |
| Paired trials for improvement decisions | Compare matched cases where one observation legitimately changes the answer. Require positive, ambiguous and missing-evidence cases, independent receipts and an actual saved artifact where relevant. Retain reviewed failures. |

## Additional opportunities

| Idea | Smallest experiment worth doing |
|---|---|
| Shared source collections | File one source under multiple projects with separate annotations. Correct its metadata once; neither project's interpretation should change silently. A saved collection can show “read later” across projects. |
| Detect repeated reporting | In a market or science brief, show when six pages all repeat one press release or study. Preserve useful commentary while exposing the common origin. |
| Decision revisit dates | When making a decision, record the assumption and the event or date that would justify revisiting it. Bring it back in a manually opened planning review alongside its original rationale. |
| Internal and external editions | Generate an internal planning sheet and an external brief from shared facts. Label estimates and keep private costs inside the internal edition. Reopen both and check the actual exports. |
| Portable project handoff | Export a folder containing the brief, table or notebook, source references, outstanding decisions and a short reading order. Someone without Theseus should be able to use it. |
| Research trail for interrupted work | Show what has been searched, which domains or documents remain, and the most useful unanswered question. Resume a partial crawl without fetching unchanged material twice. |

## Desktop appearance and interaction

- **First visual change:** a new-session lockup pairing the existing ship-and-T mark with a serif Theseus wordmark, fine dividers and a plain-language prompt. It scales with the available width.
- **Next:** give the left rail two useful views, Projects and Needs my attention. Keep conversation central and use the right pane for the selected source, comparison, saved artifact or run.
- **Research reading view:** use comfortable line lengths, strong table headings, stable column widths and a source inspector that keeps the selected passage visible.
- **Glanceable changes:** show previous and current values together, with text labels alongside color. Reserve prominent styling for decisions and material changes.
- **Quiet status area:** retain small model/account usage text where supplied by the provider. Distinguish remaining allowance, estimated usage, and unknown availability.
- **Visual acceptance:** try narrow and wide windows, light and dark themes, keyboard focus, long project titles and real dense tables. A decorative element should never displace evidence or the message composer.

## What already exists to build on

The existing fork has its own packaged app, ship identity and profile, a Hermes tool loop, multiple model routes, and project memory shared across chats. Local studies covered corrections reaching resumed jobs, historical and competing theses, bounded older-source retrieval, trace and source receipts, proof of done, and a learning journal with keep/revert decisions. Earlier projects also explored Sage-Taiyi investigation planning, Seer decision history, adaptive observation, GUI crash cases and vision on demand. Carry these into ordinary work. Each new user-facing feature still needs its own packaged-app trial.

Keep calibrated reviewer questions optional. Preserve measured outcomes as the basis for accepting changes. Avoid default project-history searches on straightforward file questions, a second memory store without demonstrated benefit, and an ever-growing permanent reasoning prompt. Retain model switching, source-grounded narrow edits, and existing local data isolation.

## Implementation references

- Local discovery and cross-project collections. [Hermes Recall](https://github.com/memeshee/hermes-recall) · [Zotero collections and saved searches](https://www.zotero.org/support/collections_and_tags)
- Crawling, updates and research. [Crawl4AI](https://github.com/unclecode/crawl4ai) · [changedetection.io](https://github.com/dgtlmoon/changedetection.io) · [PaperQA](https://github.com/Future-House/paper-qa) · [GPT Researcher](https://github.com/assafelovic/gpt-researcher)
- Calculations and workflows. [Grist](https://github.com/gristlabs/grist-core) · [Jupyter–Hermes](https://github.com/dive4dec/jupyter-ai-hermes-magics) · [Stoa playbooks](https://github.com/jarrett-del/hermes-stoa)
- Attention, capture and use. [Hermes Mods](https://github.com/daveey/hermes-mods) · [Joplin Web Clipper](https://joplinapp.org/help/apps/clipper/) · [Hermes WebUI](https://github.com/nesquena/hermes-webui)
- Evidence and evaluation. [Hermes LCM](https://github.com/stephenschoettler/hermes-lcm) · [Hermes Self-Evolution](https://github.com/NousResearch/hermes-agent-self-evolution) · [Agent Station](https://github.com/thealps01-netizen/agent-station)
