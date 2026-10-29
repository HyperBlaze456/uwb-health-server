#!/usr/bin/env bash
# shellcheck disable=SC2329  # 단계 함수는 run_step이 이름으로 부르므로 shellcheck가 못 봄
# 백업 (SPEC OP-1, OP-2). 호스트 cron이 돌립니다(ops/crontab).
#
#   ops/backup.sh [--dry-run] [-v] files    raw·archive 파일을 원격에 복사(10분마다)
#   ops/backup.sh [--dry-run] [-v] db       DB 덤프를 원격에 올리고 오래된 덤프를 정리(매일)
#   ops/backup.sh [--dry-run] [-v] verify   core 컨테이너에서 tools/verify_archive.py(매주, OP-3)
#   ops/backup.sh [--dry-run] [-v] all      files + db
#
# 원격 배치:
#   $RCLONE_REMOTE/raw/...       raw 그대로. copy만 합니다(sync 금지). 수정 시각이 35일을 넘은 것은 원격에서 지움
#   $RCLONE_REMOTE/archive/...   archive 그대로. copy --immutable(원격 파일을 덮어쓰지 않음). 지우지 않음.
#                                원격 저장소의 버전 보존(versioning)을 켜 두고, archive/에 수명 규칙을 두지 않습니다.
#   $RCLONE_REMOTE/db/uwb-<YYYY-MM-DDTHHMMZ>.dump   pg_dump -Fc. 14일 보관, 달마다 첫 덤프는 지우지 않음
# 결과 원문·궤적·rejected는 백업하지 않습니다(SPEC 5.1).
#
# 설정(환경 변수. 없으면 저장소 .env에서 읽음):
#   RCLONE_REMOTE      원격 뿌리(필수). 예 'b2:uwb-backup'. 시험은 로컬 폴더 ':local:/tmp/uwb-remote'
#   DATA_DIR           호스트 파일 뿌리(기본 /srv/uwb)
#   ALERT_WEBHOOK_URL  실패하면 이 주소에 JSON을 POST. 없으면 실패 내용을 표준 오류에 쓰고 0이 아닌 코드로 끝남(cron 메일)
#   RCLONE             rclone 명령(기본 rclone)
#   RCLONE_FLAGS       rclone에 더 줄 옵션(예 '--config /etc/uwb/rclone.conf')
#   RAW_REMOTE_DAYS    원격 raw 보관 일수(기본 35)
#   RAW_REMOTE_PRUNE   1이면 이 스크립트가 원격 raw를 지움(기본 1). 원격의 수명 규칙으로 지우면 0
#   DUMP_KEEP_DAYS     DB 덤프 보관 일수(기본 14)
#   DB_OWNER, DB_NAME  pg_dump 사용자와 DB(기본 uwb, uwb)
#   COMPOSE_DIR        compose.yaml이 있는 폴더(기본 이 스크립트의 위 폴더)
#   BACKUP_TMP         덤프를 잠시 두는 폴더(기본 $TMPDIR 또는 /tmp). DB 덤프 크기만큼 자리가 있어야 함
#   BACKUP_LOG         있으면 매번의 출력을 이 파일에 덧붙임
#   LOCK_STALE_MIN     같은 작업이 이 분보다 오래 돌고 있으면 실패로 알림(기본 120)
#
# 성공하면 아무것도 출력하지 않습니다(cron 메일이 오지 않게). -v나 --dry-run이면 출력을 보입니다.
# 종료 코드: 0 성공(이전 실행이 아직 돌고 있어 건너뛴 경우 포함), 1 백업 실패(알림 보냄),
# 2 사용법 오류나 설정 오류(설정 오류는 알림 보냄).

set -euo pipefail

usage() {  # 맨 위 설명(3번째 줄부터 첫 빈 줄 앞까지)
  awk 'NR >= 3 && /^#/ { sub(/^# ?/, ""); print; next } NR >= 3 { exit }' "$0"
}

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
COMPOSE_DIR=${COMPOSE_DIR:-$(dirname "$SCRIPT_DIR")}

DRY_RUN=0
VERBOSE=0
MODE=
for arg in "$@"; do
  case $arg in
    --dry-run) DRY_RUN=1 ;;
    -v | --verbose) VERBOSE=1 ;;
    -h | --help) usage; exit 0 ;;
    files | db | verify | all) MODE=$arg ;;
    *) usage >&2; exit 2 ;;
  esac
done
if [[ -z $MODE ]]; then
  usage >&2
  exit 2
fi
if ((DRY_RUN)); then VERBOSE=1; fi

# .env에서 아직 정하지 않은 값만 읽음(환경 변수가 이김)
from_dotenv() {
  local name=$1 file="$COMPOSE_DIR/.env" line value
  if [[ -n ${!name:-} || ! -f $file ]]; then return 0; fi
  line=$(grep -E "^${name}=" "$file" | tail -n 1 || true)
  if [[ -z $line ]]; then return 0; fi
  value=${line#*=}
  value=${value%\"}; value=${value#\"}; value=${value%\'}; value=${value#\'}
  printf -v "$name" '%s' "$value"
}
for name in RCLONE_REMOTE DATA_DIR ALERT_WEBHOOK_URL DB_OWNER DB_NAME; do
  from_dotenv "$name"
done

RCLONE_REMOTE=${RCLONE_REMOTE:-}
DATA_DIR=${DATA_DIR:-/srv/uwb}
ALERT_WEBHOOK_URL=${ALERT_WEBHOOK_URL:-}
RCLONE=${RCLONE:-rclone}
RCLONE_FLAGS=${RCLONE_FLAGS:-}
RAW_REMOTE_DAYS=${RAW_REMOTE_DAYS:-35}
RAW_REMOTE_PRUNE=${RAW_REMOTE_PRUNE:-1}
DUMP_KEEP_DAYS=${DUMP_KEEP_DAYS:-14}
DB_OWNER=${DB_OWNER:-uwb}
DB_NAME=${DB_NAME:-uwb}
BACKUP_TMP=${BACKUP_TMP:-${TMPDIR:-/tmp}}
BACKUP_LOG=${BACKUP_LOG:-}
LOCK_STALE_MIN=${LOCK_STALE_MIN:-120}
HOST=$(hostname)

WORK=$(mktemp -d "${BACKUP_TMP%/}/uwb-backup.XXXXXX")
OUT="$WORK/out.log"
: >"$OUT"
HELD_LOCKS=""

cleanup() {
  local dir
  for dir in $HELD_LOCKS; do rm -rf "$dir"; done
  rm -rf "$WORK"
}
trap cleanup EXIT

say() {
  printf '%s [backup %s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODE" "$*" >>"$OUT"
}

flush_output() {
  if [[ -n $BACKUP_LOG ]]; then cat "$OUT" >>"$BACKUP_LOG" || true; fi
}

json_escape() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\t'/ }
  s=${s//$'\r'/ }
  s=${s//$'\n'/ | }
  printf '%s' "$s"
}

# OP-2: 실패 알림. 웹훅이 없거나 실패해도 표준 오류와 종료 코드로 cron 메일이 감
alert() {
  local message=$1 body
  body=$(printf '{"reason":"backup_failed","host":"%s","mode":"%s","message":"%s"}' \
    "$(json_escape "$HOST")" "$MODE" "$(json_escape "$message")")
  if ((DRY_RUN)); then
    say "dry-run: would alert: $body"
    return 0
  fi
  if [[ -n $ALERT_WEBHOOK_URL ]]; then
    if ! curl -fsS -m 15 -X POST -H 'Content-Type: application/json' --data "$body" "$ALERT_WEBHOOK_URL" >>"$OUT" 2>&1; then
      say "alert webhook failed"
    fi
  fi
}

die_config() {
  say "config error: $1"
  alert "config error: $1"
  cat "$OUT" >&2
  flush_output
  exit 2
}

if [[ -z $RCLONE_REMOTE && $MODE != verify ]]; then
  die_config "RCLONE_REMOTE is not set"
fi
if [[ $MODE != verify ]] && ! command -v "$RCLONE" >/dev/null 2>&1; then
  die_config "rclone not found: $RCLONE"
fi
RCLONE_REMOTE=${RCLONE_REMOTE%/}

DRY=()
if ((DRY_RUN)); then DRY=(--dry-run); fi

rclone_run() {
  # RCLONE_FLAGS는 일부러 낱말로 나눔
  # shellcheck disable=SC2086
  "$RCLONE" $RCLONE_FLAGS ${DRY[@]+"${DRY[@]}"} "$@" >>"$OUT" 2>&1
}

compose() {
  docker compose -f "$COMPOSE_DIR/compose.yaml" "$@"
}

days_ago() {  # UTC 날짜 YYYY-MM-DD. GNU date, 안 되면 BSD date
  date -u -d "$1 days ago" +%Y-%m-%d 2>/dev/null || date -u -v-"$1"d +%Y-%m-%d
}

# 같은 작업이 겹쳐 돌지 않게 함. 0 = 잠금 잡음, 1 = 이전 실행이 아직 돎(건너뜀), 2 = 너무 오래 돎(실패)
take_lock() {
  local dir="${BACKUP_TMP%/}/uwb-backup-$1.lock" pid
  if mkdir "$dir" 2>/dev/null; then
    echo $$ >"$dir/pid"
    HELD_LOCKS="$HELD_LOCKS $dir"
    return 0
  fi
  pid=$(cat "$dir/pid" 2>/dev/null || true)
  if [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null; then
    if [[ -n $(find "$dir" -maxdepth 0 -mmin +"$LOCK_STALE_MIN" 2>/dev/null) ]]; then
      return 2
    fi
    return 1
  fi
  rm -rf "$dir"  # 죽은 실행이 남긴 잠금
  take_lock "$1"
}

# ---------------------------------------------------------------- 단계
# 각 단계는 실패하면 1을 돌려줍니다. 단계 함수는 `||` 안에서 불리므로 set -e에 기대지 않고 직접 확인합니다.

backup_raw() {
  if [[ ! -d $DATA_DIR/raw ]]; then
    say "no $DATA_DIR/raw"
    return 1
  fi
  # raw 파일은 이어 쓰기만 하므로, 보내는 중에 늘어나도 처음 본 크기까지만 보냄(--local-no-check-updated).
  # 잘린 끝 레코드는 재색인이 건너뜀. 다음 실행에서 더 큰 파일을 다시 보냄.
  # 원격 보관 기간보다 오래된 파일은 보내지 않음(지운 뒤 다시 올리는 일을 막음).
  rclone_run copy "$DATA_DIR/raw" "$RCLONE_REMOTE/raw" \
    --local-no-check-updated --max-age "${RAW_REMOTE_DAYS}d" || return 1
  if [[ $RAW_REMOTE_PRUNE == 1 ]]; then
    rclone_run delete "$RCLONE_REMOTE/raw" --min-age "${RAW_REMOTE_DAYS}d" || return 1
    rclone_run rmdirs "$RCLONE_REMOTE/raw" --leave-root || return 1
  fi
}

backup_archive() {
  if [[ ! -d $DATA_DIR/archive ]]; then
    say "no $DATA_DIR/archive yet (no walking bouts archived)"
    return 0
  fi
  # 영구 원본: copy만, 원격의 같은 이름 파일을 덮어쓰지 않음(다르면 오류 → 알림). 쓰는 중인 임시 파일은 뺌.
  rclone_run copy "$DATA_DIR/archive" "$RCLONE_REMOTE/archive" \
    --immutable --exclude '.*.tmp-*' || return 1
}

backup_db() {
  local stamp name file
  stamp=$(date -u +%Y-%m-%dT%H%MZ)
  name="uwb-$stamp.dump"
  file="$WORK/$name"
  if ((DRY_RUN)); then
    say "dry-run: would run: docker compose exec -T db pg_dump -U $DB_OWNER -d $DB_NAME -Fc > $name"
    say "dry-run: would upload $name to $RCLONE_REMOTE/db/"
  else
    compose exec -T db pg_dump -U "$DB_OWNER" -d "$DB_NAME" -Fc >"$file" 2>>"$OUT" || return 1
    if [[ ! -s $file ]]; then
      say "pg_dump wrote an empty file"
      return 1
    fi
    # 덤프를 읽을 수 있는지 확인(목차만 읽음)
    compose exec -T db pg_restore --list <"$file" >/dev/null 2>>"$OUT" || { say "dump is not readable"; return 1; }
    rclone_run copyto "$file" "$RCLONE_REMOTE/db/$name" || return 1
    say "uploaded $name ($(wc -c <"$file" | tr -d ' ') bytes)"
  fi
  prune_dumps || return 1
}

# DUMP_KEEP_DAYS보다 오래된 덤프를 지움. 달마다 첫 덤프(보통 1일)는 남김.
prune_dumps() {
  local cutoff listing name day month kept_months=" "
  cutoff=$(days_ago "$DUMP_KEEP_DAYS")
  # shellcheck disable=SC2086
  listing=$("$RCLONE" $RCLONE_FLAGS lsf --files-only "$RCLONE_REMOTE/db" 2>>"$OUT") || return 1
  while IFS= read -r name; do
    if [[ ! $name =~ ^uwb-(([0-9]{4}-[0-9]{2})-[0-9]{2})T[0-9]{4}Z\.dump$ ]]; then continue; fi
    day=${BASH_REMATCH[1]}
    month=${BASH_REMATCH[2]}
    if [[ $kept_months != *" $month "* ]]; then
      kept_months="$kept_months$month "
      continue  # 그 달의 첫 덤프
    fi
    if [[ $day < $cutoff ]]; then
      rclone_run deletefile "$RCLONE_REMOTE/db/$name" || return 1
      if ((DRY_RUN)); then say "dry-run: would delete old dump $name"; else say "deleted old dump $name"; fi
    fi
  done < <(printf '%s\n' "$listing" | sort)
}

verify_archive() {
  if ((DRY_RUN)); then
    say "dry-run: would run: docker compose exec -T core python tools/verify_archive.py"
    return 0
  fi
  compose exec -T core python tools/verify_archive.py >>"$OUT" 2>&1 || return 1
}

# ---------------------------------------------------------------- 실행

FAILED=""
run_step() {  # 잠금 이름, 단계 함수
  local lock=$1 fn=$2 rc=0
  take_lock "$lock" || rc=$?
  if ((rc == 1)); then
    say "skip $fn: previous $lock backup is still running"
    return 0
  elif ((rc == 2)); then
    say "previous $lock backup has been running for over $LOCK_STALE_MIN min"
    FAILED="$FAILED $fn(stuck)"
    return 0
  fi
  say "start $fn"
  rc=0
  "$fn" || rc=$?
  if ((rc == 0)); then
    say "ok $fn"
  else
    say "FAILED $fn"
    FAILED="$FAILED $fn"
  fi
}

case $MODE in
  files)
    run_step files backup_raw
    run_step archive backup_archive
    ;;
  db)
    run_step db backup_db
    ;;
  verify)
    run_step verify verify_archive
    ;;
  all)
    run_step files backup_raw
    run_step archive backup_archive
    run_step db backup_db
    ;;
esac

if [[ -n $FAILED ]]; then
  alert "failed:$FAILED. last output: $(tail -n 5 "$OUT")"
  cat "$OUT" >&2
  flush_output
  exit 1
fi
if ((VERBOSE)); then cat "$OUT"; fi
flush_output
exit 0
