#!/usr/bin/env bash
# Живые прогоны: собрать zhoda_rate / rounds / switches / paths_rejected / cost.
#
#   cd core
#   ./eval/run.sh                         # все кейсы
#   ./eval/run.sh debate-pg-kafka         # один id
#   DRY=1 ./eval/run.sh                   # только команды
#   SLEEP=45 ./eval/run.sh                # пауза между вопросами (сек)
#
# --auto-clarify: Stage 0 без рук; неспрошенное → open_ambiguities.
# Транскрипты: core/transcripts/<id>.jsonl  Логи: core/eval/runs/<stamp>/
set -euo pipefail

CORE="$(cd "$(dirname "$0")/.." && pwd)"
QUESTIONS="${CORE}/eval/questions.jsonl"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${CORE}/eval/runs/${STAMP}"
SLEEP="${SLEEP:-30}"
DRY="${DRY:-0}"
CONFIG="${CONFIG:-zhoda.yaml}"

cd "${CORE}"

if [[ ! -f "${QUESTIONS}" ]]; then
  echo "нет ${QUESTIONS}" >&2
  exit 2
fi
if [[ "${DRY}" != "1" && ! -f "${CONFIG}" ]]; then
  echo "нет ${CONFIG} — скопируй zhoda.yaml.example" >&2
  exit 2
fi

mkdir -p "${OUT}"

python3 - "${QUESTIONS}" "$@" <<'PY' > "${OUT}/queue.jsonl"
import json, sys
path, ids = sys.argv[1], set(sys.argv[2:])
with open(path, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if ids and rec["id"] not in ids:
            continue
        print(json.dumps(rec, ensure_ascii=False))
PY

if [[ ! -s "${OUT}/queue.jsonl" ]]; then
  echo "очередь пуста (проверь id)" >&2
  exit 2
fi

echo "out=${OUT}"
echo "sleep=${SLEEP}s dry=${DRY}"
echo "---"

extract() {
  python3 - "$1" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
text = re.sub(r"\x1b\[[0-9;]*m", "", text)

def first(pat: str, default: str = "") -> str:
    # якорь на итоговые строки CLI, не на ✓ progress («cached (3 models)»)
    m = re.search(pat, text, re.M)
    return m.group(1).strip() if m else default

ic = "1" if "insufficient_context" in text else "0"
proto = first(r"^protocol:\s+(vote|debate|red_team)") or first(
    r"^✓ protocol=(vote|debate|red_team)"
)
print("\t".join([
    proto,
    first(r"^zhoda_reached:\s+(\S+)"),
    first(r"^zhoda_reached:\s+\S+\s+\(([^,]+), rounds:"),
    first(r"^zhoda_reached:.*rounds:\s*(\d+)"),
    first(r"^cost:\s*(\d+) requests"),
    first(r"^cost:\s*\d+ requests,\s*\$([0-9.]+)"),
    first(r"^cost:.*cache_hits:\s*(\d+)", "0"),
    first(r"^switches:\s+(\d+)", "0"),
    first(r"^paths rejected:\s+(\d+)", "0"),
    first(r"transcript:\s+([0-9a-f]{12})"),
    ic,
]))
PY
}

SUMMARY="${OUT}/summary.tsv"
printf 'id\tpass\texit\tprotocol\tzhoda\tstrength\trounds\trequests\tusd\tcache_hits\tswitches\tpaths_rejected\ttranscript\tic\n' > "${SUMMARY}"

need_sleep=0
while IFS= read -r rec; do
  id="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "${rec}")"
  q="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["question"])' "${rec}")"
  why="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["why"])' "${rec}")"
  expect="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["expect_protocol"])' "${rec}")"
  nrepeat="$(python3 -c 'import json,sys; print(int(json.loads(sys.argv[1]).get("repeat", 1)))' "${rec}")"
  ctxs="$(python3 -c 'import json,sys; print("\n".join(json.loads(sys.argv[1]).get("context") or []))' "${rec}")"

  for pass in $(seq 1 "${nrepeat}"); do
    log="${OUT}/${id}.${pass}.log"
    cmd=(uv run zhoda deliberate "${q}" --auto-clarify --config "${CONFIG}")
    while IFS= read -r ctx; do
      [[ -z "${ctx}" ]] && continue
      cmd+=(--context "${CORE}/${ctx}")
    done <<< "${ctxs}"

    echo "# ${id} pass=${pass} expect=${expect}"
    echo "# ${why}"
    printf ' %q' "${cmd[@]}"
    echo
    if [[ "${DRY}" == "1" ]]; then
      printf '%s\t%s\t%s\t\t\t\t\t\t\t\t\t\t\n' "${id}" "${pass}" "dry" >> "${SUMMARY}"
      continue
    fi

    if [[ "${need_sleep}" == "1" && "${pass}" == "1" ]]; then
      sleep "${SLEEP}"
    fi
    need_sleep=1

    set +e
    "${cmd[@]}" > "${log}" 2>&1
    code=$?
    set -e
    metrics="$(extract "${log}")"
    printf '%s\t%s\t%s\t%s\n' "${id}" "${pass}" "${code}" "${metrics}" >> "${SUMMARY}"
    echo "exit=${code} ${metrics}"
    if grep -qE 'quota_exceeded|API key expired|"code":401|401:' "${log}" 2>/dev/null; then
      echo "provider 401/quota — стоп (обнови OPENROUTER_API_KEY)" >&2
      break 2
    fi
  done
done < "${OUT}/queue.jsonl"

echo "---"
echo "summary: ${SUMMARY}"
echo "transcripts: ${CORE}/transcripts/"
column -t -s $'\t' "${SUMMARY}" 2>/dev/null || cat "${SUMMARY}"
