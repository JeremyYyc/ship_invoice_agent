#!/bin/zsh
set -eu

REPO='/Users/jeremyyang/codes/湃乐多航运科技/笔试题AIAGENT/ship_invoice_agent'
HARNESS='/Users/jeremyyang/Documents/Codex/2026-10-22/task/local_ocr_harness.py'
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-local-ocr"
RESULT_DIR="$REPO/test-results/$RUN_ID"
SUFFIX="$(date -u +%Y%m%d%H%M%S)-$$"
NETWORK="ship-ocr-test-$SUFFIX"
FILE_CONTAINER="ship-files-test-$SUFFIX"
OCR_CONTAINER="ship-ocr-test-$SUFFIX"
OCR_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
HARNESS_STATUS=99

mkdir -p "$RESULT_DIR/container-logs" "$RESULT_DIR/environment"

cleanup() {
  set +e
  docker logs --timestamps "$FILE_CONTAINER" > "$RESULT_DIR/container-logs/file-server.log" 2>&1
  docker logs --timestamps "$OCR_CONTAINER" > "$RESULT_DIR/container-logs/local-ocr.log" 2>&1
  docker inspect "$FILE_CONTAINER" > "$RESULT_DIR/environment/file-server-inspect.json" 2>&1
  docker inspect "$OCR_CONTAINER" > "$RESULT_DIR/environment/local-ocr-inspect.json" 2>&1
  docker stop "$OCR_CONTAINER" "$FILE_CONTAINER" > "$RESULT_DIR/cleanup.log" 2>&1
  docker network rm "$NETWORK" >> "$RESULT_DIR/cleanup.log" 2>&1
}
trap cleanup EXIT INT TERM

cd "$REPO"

git branch --show-current > "$RESULT_DIR/environment/git-branch.txt"
git rev-parse HEAD > "$RESULT_DIR/environment/git-commit.txt"
git status --short --branch > "$RESULT_DIR/environment/git-status-before.txt"
docker version > "$RESULT_DIR/environment/docker-version.txt" 2>&1
docker image inspect python:3.12-slim > "$RESULT_DIR/environment/python-image-inspect.json"
docker image inspect ship_invoice_agent-local-ocr > "$RESULT_DIR/environment/ocr-image-inspect.json"

{
  echo "run_id=$RUN_ID"
  echo "result_dir=$RESULT_DIR"
  echo "network=$NETWORK"
  echo "file_container=$FILE_CONTAINER"
  echo "ocr_container=$OCR_CONTAINER"
  echo "ocr_host_binding=127.0.0.1:$OCR_PORT:8001"
  echo "file_server_published_ports=none"
  echo "llm_called=false"
  echo "external_ocr_called=false"
  echo "apply_used=false"
  echo "proxy_bypass=127.0.0.1,localhost,$FILE_CONTAINER"
  echo "retry_note=${SHIP_RETRY_NOTE:-none}"
} > "$RESULT_DIR/configuration.txt"

{
  echo "docker network create $NETWORK"
  echo "docker run -d --rm --pull=never --name $FILE_CONTAINER --network $NETWORK -v $REPO/.runtime/files:/files:ro python:3.12-slim python -m http.server 8081 --bind 0.0.0.0 --directory /files"
  echo "docker run -d --rm --pull=never --name $OCR_CONTAINER --network $NETWORK -p 127.0.0.1:$OCR_PORT:8001 -e LOCAL_OCR_LANGUAGE=eng ship_invoice_agent-local-ocr"
  echo "curl --fail http://127.0.0.1:$OCR_PORT/health"
  echo "SHIP_REPO=$REPO SHIP_RESULT_DIR=$RESULT_DIR SHIP_OCR_PORT=$OCR_PORT SHIP_FILE_SERVICE_NAME=$FILE_CONTAINER .venv/bin/python $HARNESS"
  echo "docker logs --timestamps $FILE_CONTAINER"
  echo "docker logs --timestamps $OCR_CONTAINER"
  echo "docker stop $OCR_CONTAINER $FILE_CONTAINER"
  echo "docker network rm $NETWORK"
} > "$RESULT_DIR/commands.txt"

docker network create "$NETWORK" > "$RESULT_DIR/network-create.log"
docker run -d --rm --pull=never \
  --name "$FILE_CONTAINER" \
  --network "$NETWORK" \
  -v "$REPO/.runtime/files:/files:ro" \
  python:3.12-slim \
  python -m http.server 8081 --bind 0.0.0.0 --directory /files \
  > "$RESULT_DIR/file-container-start.txt"
docker run -d --rm --pull=never \
  --name "$OCR_CONTAINER" \
  --network "$NETWORK" \
  -p "127.0.0.1:$OCR_PORT:8001" \
  -e LOCAL_OCR_LANGUAGE=eng \
  -e "NO_PROXY=$FILE_CONTAINER,localhost,127.0.0.1" \
  -e "no_proxy=$FILE_CONTAINER,localhost,127.0.0.1" \
  ship_invoice_agent-local-ocr \
  > "$RESULT_DIR/ocr-container-start.txt"

HEALTH_OK=false
for attempt in {1..60}; do
  if curl --silent --show-error --fail --max-time 2 \
    "http://127.0.0.1:$OCR_PORT/health" \
    > "$RESULT_DIR/health.json" 2> "$RESULT_DIR/health-error.log"; then
    HEALTH_OK=true
    break
  fi
  sleep 1
done
if [[ "$HEALTH_OK" != true ]]; then
  echo "local OCR health check failed" >&2
  exit 1
fi

docker exec "$OCR_CONTAINER" python --version > "$RESULT_DIR/environment/ocr-python-version.txt" 2>&1
docker exec "$OCR_CONTAINER" tesseract --version > "$RESULT_DIR/environment/tesseract-version.txt" 2>&1
docker exec "$OCR_CONTAINER" pdftoppm -v > "$RESULT_DIR/environment/poppler-version.txt" 2>&1

set +e
SHIP_REPO="$REPO" \
SHIP_RESULT_DIR="$RESULT_DIR" \
SHIP_OCR_PORT="$OCR_PORT" \
SHIP_FILE_SERVICE_NAME="$FILE_CONTAINER" \
PYTHONDONTWRITEBYTECODE=1 \
NO_PROXY="127.0.0.1,localhost" \
no_proxy="127.0.0.1,localhost" \
  .venv/bin/python "$HARNESS" \
  > "$RESULT_DIR/harness-stdout.log" 2> "$RESULT_DIR/harness-stderr.log"
HARNESS_STATUS=$?
set -e
echo "$HARNESS_STATUS" > "$RESULT_DIR/harness-exit-code.txt"

cleanup
trap - EXIT INT TERM

docker ps -a --filter "name=$FILE_CONTAINER" --filter "name=$OCR_CONTAINER" \
  --format '{{.Names}}|{{.Status}}|{{.Ports}}' > "$RESULT_DIR/environment/container-leftovers.txt"
set +e
docker network inspect "$NETWORK" > "$RESULT_DIR/environment/network-leftover.json" 2>&1
NETWORK_STATUS=$?
set -e
echo "$NETWORK_STATUS" > "$RESULT_DIR/environment/network-inspect-exit-code.txt"
git status --short --branch > "$RESULT_DIR/environment/git-status-after.txt"

echo "$RESULT_DIR"
exit "$HARNESS_STATUS"
