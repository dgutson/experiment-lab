#!/bin/bash
# Restarts the lab server, which keeps the code it started with: stops the
# lab-server listening on port $EXPERIMENT_LAB_PORT (default 5000), waits for
# it to end, then runs start.sh, which reads the same settings. A server on
# another port is left running.
port=${EXPERIMENT_LAB_PORT:-5000}
pid=$(ss -ltnpH "sport = :$port" | grep -o '"lab-server",pid=[0-9]*' | head -1 | cut -d= -f2)
if [ -n "$pid" ]; then
    kill "$pid"
    for _ in $(seq 30); do
        kill -0 "$pid" 2> /dev/null || break
        sleep 1
    done
    kill -0 "$pid" 2> /dev/null && { echo "lab-server $pid did not stop after 30 s"; exit 1; }
elif [ -n "$(ss -ltnH "sport = :$port")" ]; then
    echo "port $port is used by a process that is not lab-server"
    exit 1
fi
exec "$(dirname "$0")/start.sh"
