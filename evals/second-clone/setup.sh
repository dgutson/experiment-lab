#!/usr/bin/env bash
# audio-codec's folder, ~/audio-codec, is a git repository, and the run works
# in another clone of it, which is no project's folder.
set -euo pipefail
"$(dirname "$0")/../lab.sh"
git -C ~/audio-codec -c init.defaultBranch=main init -q
git -C ~/audio-codec add -A
git -C ~/audio-codec -c user.name=lab -c user.email=lab@example.com commit -qm "bench.py"
git clone -q ~/audio-codec .
