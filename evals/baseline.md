# Eval baseline

The last full run of the eval cases, for comparing a change to the skill, rec or q against:
`evals/run.sh --model sonnet --runs 3` on 2026-11-01 with Claude Code 2.1.316, 3 runs per case and
arm, at commit c98f4c5 (plugin 0.2.38). The whole run cost $8.43, the judge's calls included.

"With" is an agent given the plugin, "without" one given none. Both have rec and q on the PATH and
can read their `--help`, so the difference is what the skill adds over the commands' own help. A
score is the mean, over the runs, of the share of graders passed; the cost is Claude Code's
list-price estimate for one run (`costUsd` in `aggregate-result.json`), and turns are the agent's
turns in one run.

| Case | Score with | Score without | Cost per run with | Cost per run without | Turns with | Turns without |
|---|---|---|---|---|---|---|
| answer-from-data | 1.00 | 0.72 | $0.071 | $0.098 | 4.7 | 7.7 |
| dead-end | 1.00 | 0.67 | $0.083 | $0.137 | 6.0 | 8.7 |
| find-past-work | 1.00 | 0.67 | $0.077 | $0.106 | 5.7 | 7.3 |
| log-runs | 1.00 | 0.56 | $0.084 | $0.134 | 7.0 | 10.0 |
| new-line | 0.83 | 1.00 | $0.112 | $0.181 | 8.0 | 8.3 |
| no-project | 0.89 | 0.89 | $0.096 | $0.168 | 8.0 | 9.0 |
| other-project-views | 1.00 | 0.67 | $0.067 | $0.112 | 4.7 | 9.3 |
| param-script | 1.00 | 1.00 | $0.092 | $0.151 | 6.7 | 10.0 |
| param-sql | 1.00 | 0.50 | $0.081 | $0.113 | 5.0 | 8.0 |
| pick-parent | 1.00 | 0.67 | $0.082 | $0.143 | 6.3 | 8.0 |
| predict | 1.00 | 0.17 | $0.104 | $0.199 | 9.0 | 11.7 |
| report-latest | 1.00 | 1.00 | $0.113 | $0.206 | 7.7 | 9.7 |
| all cases, one run each | | | $1.06 | $1.75 | | |

Below 1.00 with the plugin: in new-line, one run put the new idea, unrelated to the earlier
experiments, below one of them with `--in`; in no-project, one run called `rec project` twice, which
a `rec project --help` before the call that makes the project counts as.

The scores without the plugin vary from one full run to the next, since 3 runs are few; the costs
and turns vary less. The whole run is also recorded in the lab, in the experiment-lab project's
experiment "agent evals › eval baseline".
