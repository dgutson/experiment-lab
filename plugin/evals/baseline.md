# Eval baseline

The last full run of the eval cases, for comparing a change to the skill, rec or q against:
`bin/evals.sh --model sonnet` on 2026-10-24 with Claude Code 2.1.310, 3 runs per case and arm.
The whole run cost $5.04, the judge's calls included.

"With" is an agent given the plugin, "without" one given none. Both have rec and q on the PATH and
can read their `--help`, so the difference is what the skill adds over the commands' own help. A
score is the mean, over the runs, of the share of graders passed; the cost is Claude Code's
list-price estimate for one run (`costUsd` in `aggregate-result.json`), and turns are the agent's
turns in one run.

| Case | Score with | Score without | Cost per run with | Cost per run without | Turns with | Turns without |
|---|---|---|---|---|---|---|
| answer-from-data | 1.00 | 0.56 | $0.067 | $0.086 | 5.0 | 6.0 |
| dead-end | 1.00 | 0.83 | $0.076 | $0.137 | 5.7 | 7.7 |
| find-past-work | 1.00 | 0.67 | $0.069 | $0.115 | 5.0 | 9.3 |
| log-runs | 1.00 | 1.00 | $0.087 | $0.133 | 8.7 | 12.7 |
| new-line | 1.00 | 0.83 | $0.109 | $0.181 | 7.7 | 11.7 |
| no-project | 1.00 | 1.00 | $0.088 | $0.164 | 6.3 | 11.0 |
| other-project-views | 1.00 | 1.00 | $0.061 | $0.099 | 4.7 | 9.0 |
| pick-parent | 1.00 | 0.00 | $0.077 | $0.133 | 6.0 | 7.7 |
| all cases, one run each | | | $0.63 | $1.05 | | |

The scores without the plugin vary from one full run to the next (pick-parent scored 0.67 in
another), since 3 runs are few; the costs and turns vary less.
