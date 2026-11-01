---
name: feedback
description: Writes a report on how experiment-lab served this session (its frictions, the tokens spent for little, what the lab lacked) and, once the person agrees, sends it as an issue of dgutson/experiment-lab.
disable-model-invocation: true
---

# A report on how the lab served this session

1. Run, adding `--real` only if the person's words after the command allow real names ("$ARGUMENTS"):

   ```sh
   PYTHONPATH=~/experiment-lab python3 -m experiment_lab.feedback draft --session ${CLAUDE_SESSION_ID} --plugin "${CLAUDE_PLUGIN_ROOT}"
   ```

   It reads this session's transcript and prints the file it saved, then the draft report: the
   versions, how much the session used the lab, and each lab command that failed (with what did the
   same work next and the turns it took), each q that found nothing and was searched again, and each
   lab command that ran more than once or printed much, with names, paths, numbers and quoted texts
   made neutral.
2. If the draft lists nothing that cost the session turns or tokens and you have nothing to add, the
   lab served it well: tell the person so in one line, and send nothing unless they ask. Report only
   what happened; do not look for problems.
3. Else append to that file, with `cat >> FILE <<'EOF'`, what only you know, in the draft's neutral
   names, with only the sections that have something to say:
   - `## The work`: in two or three sentences, the kind of work the session did with the lab, such
     as recording a failed idea, without its subject.
   - `## Other frictions`: what cost turns or tokens that the draft does not show, such as a result
     taken as right that was wrong.
   - `## Done by hand`: scripts, queries or parsers the session wrote that q, rec or `lab.py` could do.
   - `## Old and new results`: whether q found the earlier results the work needed, and whether rec
     and `lab.py` recorded the new ones.
   - `## Missing`: what the work needed that the lab lacks, and what would make it more useful.
4. Put the whole report in your reply, since the person does not see what commands print, and ask
   whether to send it as a public issue of dgutson/experiment-lab. Only on a yes, run:

   ```sh
   PYTHONPATH=~/experiment-lab python3 -m experiment_lab.feedback send FILE --title "Agent report: <the work, in a few neutral words>"
   ```

   It opens the issue with gh, or prints how to open it by hand.
