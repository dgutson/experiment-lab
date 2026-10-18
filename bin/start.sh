#!/bin/bash
# Starts the local MLflow Tracking server with the lab pages: UI and API on
# http://127.0.0.1:$MLFLOW_LAB_PORT (default 5000), the pages at /lab. Its files
# are in the data folder $MLFLOW_LAB_DATA (default ~/mlflow), made if missing:
# runs and metrics in mlflow.db (MLflow makes its tables on first start), logged
# files in artifacts/, the log in server.log, settings in lab.toml, the pages'
# copies of the database in lab-cache/. If the server stops while starting
# with the lab pages, it is started without them. It runs at the lowest CPU
# and disk priority and outlives the shell that started it. Server jobs and
# telemetry are off: the server only answers requests.
export MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false MLFLOW_DISABLE_TELEMETRY=true DO_NOT_TRACK=true MLFLOW_DISABLE_AGENT_HINT=1
data=${MLFLOW_LAB_DATA:-$HOME/mlflow}
port=${MLFLOW_LAB_PORT:-5000}
mkdir -p "$data" && cd "$data" || exit 1
url=http://127.0.0.1:$port
if curl -sf "$url/health" > /dev/null; then echo "already running at $url"; exit 0; fi

# start [OPTION...]: starts the server with these extra options; returns 0 once
# it answers, 1 if it stopped, 2 if it still gives no answer after 60 s.
start() {
    setsid nohup nice -n 19 ionice -c 3 mlflow server "$@" \
        --backend-store-uri "sqlite:///$PWD/mlflow.db" \
        --artifacts-destination "$PWD/artifacts" \
        --host 127.0.0.1 --port "$port" --workers 1 \
        >> server.log 2>&1 < /dev/null &
    local pid=$!
    for _ in $(seq 60); do
        curl -sf "$url/health" > /dev/null && return 0
        kill -0 "$pid" 2> /dev/null || return 1
        sleep 1
    done
    return 2
}

: > server.log
start --app-name lab
status=$?
if [ $status = 0 ]; then echo "running at $url, lab pages at $url/lab"; exit 0; fi
if [ $status = 1 ]; then
    echo "stopped while starting with the lab pages (see $PWD/server.log); starting without them"
    start
    status=$?
    [ $status = 0 ] && { echo "running at $url, without the lab pages"; exit 0; }
fi
[ $status = 1 ] && echo "the server stopped; see $PWD/server.log" || echo "no answer after 60 s; see $PWD/server.log"
exit 1
