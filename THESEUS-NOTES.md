# Theseus fork notes

## What changed

Theseus extends the Hermes tool loop with a learning journal. Each manually started experiment records a recurring problem, a proposed change and its rationale, a prediction, development and held-out observations, and a separate human decision. Later runs can retrieve earlier attempts, including rejections. The design draws on [Self-Improvement Memory](https://arxiv.org/abs/2611.18856).

The approved November 22 change affects what the proposal model receives. It can use the earlier rationale, prediction, development observation, evaluator recommendation and human verdict. The journal retains the raw held-out observations and reviewer commentary for inspection. Keep reserved-case details in those dedicated fields when recording a run.

The source also includes manual workflows for interrupted jobs, artifact verification, Desktop workflow trials, screenshot comparisons and source-linked project memory. The [learning-run skill](skills/software-development/learning-run/SKILL.md) documents the commands. The [roadmap](THESEUS-ROADMAP.md) describes ongoing work.

## What the experiments measured

| Experiment | Baseline | Candidate |
| --- | ---: | ---: |
| Journal retrieval on one development history | 4/6 | 6/6 |
| Journal retrieval on two reserved histories | 9/12 | 12/12 |
| Fields sent in proposal requests on two synthetic histories | 6/10 | 10/10 |

The first retrieval candidate passed 5/6 development checks. It removed reserved-case labels but still exceeded the 16 KiB prompt target because it copied previous diffs. The second candidate retrieved that failure and kept shorter development summaries. Those first two rows describe a separate experiment on the earlier journal. The third row measures the approved change in this fork. A local stub captured the outgoing model request. Its effect on live proposal quality is still unmeasured.

From a development checkout with the Hermes test environment prepared, run the offline regression suites:

```sh
scripts/run_tests.sh -j 3 \
  tests/skills/test_learning_draft.py tests/skills/test_learning_run_skill.py \
  tests/skills/test_desktop_workflow_trial.py tests/skills/test_durable_delivery.py \
  tests/skills/test_learning_proof.py tests/skills/test_learning_vision.py \
  tests/skills/test_revisable_memory.py -q
```

See the [Hermes contributor guide](CONTRIBUTING.md) for test environment setup. The Mac launcher uses a separately provisioned local Theseus profile. The repository contains source and fixtures. Local credentials, sessions, journals and saved screenshots stay on the operator's machine.

Publication checks on November 22 passed all 75 tests in the seven suites above. Adding the ordinary project-memory, project-recall and research suites brought the total to 138 passing Python tests. The Desktop artifact filename suite passed all 19 tests.

## Upstream

Theseus is based on [Nous Research's Hermes Agent at a3b993b9](https://github.com/NousResearch/hermes-agent/commit/a3b993b98a1c679193bd8d4ea99b89ce7614bfde). The original [MIT license](LICENSE) and attribution are preserved.
