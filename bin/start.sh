#!/bin/bash
# Starts the lab server, lab-server from the uv tool install of experiment-lab:
# the pages at http://127.0.0.1:$EXPERIMENT_LAB_PORT/lab (default 5000). Its
# files are in the data folder $EXPERIMENT_LAB_DATA (default ~/lab-data), made
# if missing: the database lab.db, the runs' files in files/, settings in
# lab.toml, the log in server.log. It runs at the lowest CPU and disk priority
# and outlives the shell that started it.
data=${EXPERIMENT_LAB_DATA:-$HOME/lab-data}
port=${EXPERIMENT_LAB_PORT:-5000}
url=http://127.0.0.1:$port
command -v lab-server > /dev/null || { echo "no lab-server on PATH; install experiment-lab (see its README)"; exit 1; }
mkdir -p "$data" && cd "$data" || exit 1
if curl -sf "$url/lab/health" > /dev/null; then echo "already running at $url/lab"; exit 0; fi

: > server.log
EXPERIMENT_LAB_DATA=$PWD EXPERIMENT_LAB_PORT=$port setsid nohup nice -n 19 ionice -c 3 lab-server \
    >> server.log 2>&1 < /dev/null &
pid=$!
for _ in $(seq 60); do
    curl -sf "$url/lab/health" > /dev/null && { echo "running at $url/lab"; exit 0; }
    kill -0 "$pid" 2> /dev/null || { echo "the server stopped; see $PWD/server.log"; exit 1; }
    sleep 1
done
echo "no answer after 60 s; see $PWD/server.log"
exit 1
