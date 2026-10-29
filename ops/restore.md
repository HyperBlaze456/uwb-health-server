# 복구 절차

백업에서 서버를 되살리는 절차입니다. 두 가지가 있습니다.

- **분기마다 하는 복구 연습**(SPEC OP-3): 빈 compose에 백업을 넣어 정말 되살아나는지 확인합니다.
- **디스크가 죽었을 때**(SPEC OP-4): 실제로 되살립니다.

백업이 어떻게 쌓이는지는 `ops/backup.sh` 맨 위 설명에 있습니다. 원격 뿌리를 아래에서 `$R`라고 씁니다.

```
$R/raw/<장치>/<부팅>/<YYYY-MM-DDTHH>.bin     원본 전체, 35일
$R/archive/<장치>/<YYYY-MM>/<...>.bin        걷기 원본, 영구
$R/db/uwb-<YYYY-MM-DDTHHMMZ>.dump            DB 덤프(pg_dump -Fc), 14일 + 달마다 첫 덤프
```

결과 원문(`result/`), 궤적(`trajectory/`), 거절한 묶음(`rejected/`)은 백업하지 않습니다. 결과와 궤적은 재분석으로 다시 만듭니다.

서버 밖에 따로 보관해 두어야 하는 것: `.env`(비밀번호), rclone 설정 파일(원격 접속 키), 배포한 커밋(`CORE_VERSION`). 서버 디스크에만 있으면 디스크와 함께 사라집니다.

---

## 1. 분기마다 하는 복구 연습 (OP-3)

목표는 "빈 compose에 덤프를 넣고, archive 일부를 받아 `verify_archive.py`가 통과하고, 구간 하나를 재분석해 새 run이 생긴다"를 직접 확인하는 것입니다. 한 번 마치면 원본 삭제 스위치를 켜는 조건 하나(AR-7)를 채웁니다.

### 1.0 준비

운영 서버가 아닌 다른 컴퓨터에서 하는 것이 좋습니다(서버가 통째로 없어진 상황을 흉내 냄). 같은 VM에서 해도 되지만, 아래처럼 compose 프로젝트 이름(`-p uwb-drill`)과 데이터 폴더를 따로 써서 운영 서버와 섞이지 않게 합니다.

필요한 것: Docker, git, rclone(원격을 읽을 수 있는 설정), uv는 없어도 됩니다.

```sh
git clone <저장소> uwb-drill && cd uwb-drill
git checkout <운영 서버의 CORE_VERSION>

cp .env.example .env
# .env에서 고칠 것:
#   DB_PASSWORD, DB_*_PASSWORD   연습용 아무 값
#   DATA_DIR=/srv/uwb-drill
#   PURGE_ENABLED=false           반드시 false
#   GAITFUSION_IMAGE              운영과 같은 이미지

sudo mkdir -p /srv/uwb-drill/{raw,rejected,archive,result,trajectory}
sudo chown -R 10001:10001 /srv/uwb-drill     # 이미지의 사용자 UID(Dockerfile)

export R='<RCLONE_REMOTE>'                    # 예: b2:uwb-backup
DC="docker compose -p uwb-drill"
$DC build
```

시작 시각을 적어 둡니다. 단계마다 걸린 시간을 기록합니다.

### 1.1 빈 compose에 DB 덤프 넣기

DB 역할(`intake_rw`, `core_rw`, `api_rw`)은 클러스터에 하나씩이라 덤프에 들어 있지 않습니다. 그래서 `migrate`를 한 번 돌려 역할을 만든 뒤, 빈 DB를 다시 만들어 덤프를 넣습니다.

```sh
$DC up -d db
$DC run --rm migrate                          # 역할 셋을 만듦
$DC exec -T db dropdb -U uwb uwb
$DC exec -T db createdb -U uwb uwb

rclone lsf $R/db | sort | tail -n 3           # 가장 최근 덤프를 고름
rclone copyto $R/db/uwb-2026-10-28T1830Z.dump ./drill.dump
$DC exec -T db pg_restore -U uwb -d uwb --exit-on-error < drill.dump

$DC run --rm migrate                          # 덤프 뒤에 추가된 마이그레이션 적용, 역할 비밀번호를 이 .env 값으로
```

확인:

```sh
$DC exec -T db psql -U uwb -d uwb -c "SELECT max(name) FROM pipeline.migration" \
  -c "SELECT count(*) AS devices FROM registry.device" \
  -c "SELECT count(*) AS ranges, max(created_at) AS last_range FROM archive.range"
```

`pg_restore`가 오류 없이 끝나고, 장치 수와 archive 행 수가 운영 서버와 비슷하면 됩니다(덤프 시각만큼 차이).

### 1.2 archive 일부 받기

장치 하나의 한 달치를 받습니다.

```sh
rclone lsd $R/archive                                   # 장치 목록
rclone lsd $R/archive/head-e072a1aca858                 # 달 목록
rclone copy $R/archive/head-e072a1aca858/2026-10 /srv/uwb-drill/archive/head-e072a1aca858/2026-10
sudo chown -R 10001:10001 /srv/uwb-drill
```

### 1.3 `verify_archive.py`

받은 범위만 대조합니다(`--prefix`). 받지 않은 달의 행이 "파일 없음"으로 나오지 않게 하기 위함입니다.

```sh
$DC run --rm --no-deps core python tools/verify_archive.py \
  --prefix /data/archive/head-e072a1aca858/2026-10
```

- 마지막 줄 `verify_archive_done`의 `problems`가 `{}`이고 종료 코드가 0이면 통과입니다.
- `no_row`가 나오면 덤프 뒤에 만들어진 archive 파일입니다(정상). `--reindex`를 붙여 다시 돌려 행이 되살아나는지 봅니다. 이것도 연습의 일부입니다(TL-4).
- `sha256_mismatch`·`bytes_mismatch`·`missing_file`이 나오면 백업이 깨진 것입니다. 연습을 멈추고 원인을 찾습니다.

### 1.4 구간 하나 재분석

받은 archive가 덮는 구간 하나를 고릅니다.

```sh
$DC exec -T db psql -U uwb -d uwb -c "
  SELECT segment_id, count(*) AS ranges, min(start_at) AS first_walk
  FROM archive.range
  WHERE path LIKE '/data/archive/head-e072a1aca858/2026-10/%'
  GROUP BY segment_id ORDER BY segment_id DESC LIMIT 5"

$DC exec -T db psql -U uwb -d uwb -c "
  SELECT id, status, input, finished_at FROM analysis.run WHERE segment_id = <구간 id> ORDER BY id"
```

재분석 작업을 넣고 core와 gaitfusion을 띄웁니다.

```sh
$DC run --rm --no-deps api python tools/admin.py reanalyze <구간 id>
$DC up -d gaitfusion core
$DC logs -f core | grep -E '"event": "(analysis|archive)'
```

raw를 받지 않았으므로 재분석은 archive를 씁니다(AN-4, AR-8). 확인:

```sh
$DC exec -T db psql -U uwb -d uwb -c "
  SELECT id, status, input, n_chunks_sent, finished_at FROM analysis.run
  WHERE segment_id = <구간 id> ORDER BY id DESC LIMIT 2" \
  -c "SELECT run_id, count(*) AS bouts FROM analysis.bout
      WHERE run_id IN (SELECT id FROM analysis.run WHERE segment_id = <구간 id>) GROUP BY run_id"
```

- 새 run이 `input = 'archive'`, `status = 'done'`(걷기가 없으면 `skipped`)으로 생기면 통과입니다.
- gaitfusion 버전이 같으면 새 run의 걷기 구간 수가 옛 run과 같아야 합니다.
- 덤프에 대기 중이던 다른 작업도 core가 집어 들고, raw가 없어 실패할 수 있습니다. 연습에서는 무시합니다. 새 run의 archive 작업도 raw가 필요한 범위가 있으면 실패할 수 있습니다(무시).

### 1.5 기록과 정리

기록할 것: 날짜, 쓴 덤프 이름, 단계마다 걸린 시간, `verify_archive_done` 줄, 재분석한 구간과 새 run id, 막힌 곳과 고친 것.

```sh
$DC down -v                                   # 연습용 DB 볼륨까지 지움
sudo rm -rf /srv/uwb-drill ./drill.dump
```

---

## 2. 디스크가 죽었을 때 (OP-4)

### 잃는 것

| 무엇 | 얼마나 | 되살리는 법 |
|---|---|---|
| raw | 마지막 백업 뒤 10분 이하(Head는 확인 응답 뒤 지웠음) | 없음 |
| DB 행 | 마지막 덤프 뒤 24시간 이하 | `intake.*`는 raw에서(TL-3), `archive.range`는 archive 파일에서(TL-4), 구간·분석 결과는 core가 다시 만듦. `registry`는 사람이 다시 넣음 |
| 결과 원문·궤적 파일 | 전부 | 필요한 구간을 재분석 |
| rejected 파일 | 전부 | 없음(진단용) |

### 2.1 새 서버 준비

```sh
# Docker, rclone, git 설치. rclone 설정과 .env는 서버 밖에 보관한 것을 가져옴.
git clone <저장소> /opt/uwb-health-server && cd /opt/uwb-health-server
git checkout <배포했던 커밋>
cp <보관한 .env> .env                          # 비밀번호가 같아야 기존 설정과 맞음

sudo mkdir -p /srv/uwb/{raw,rejected,archive,result,trajectory}
export R='<RCLONE_REMOTE>'
rclone copy $R/raw /srv/uwb/raw
rclone copy $R/archive /srv/uwb/archive
sudo chown -R 10001:10001 /srv/uwb

docker compose build
```

### 2.2 DB 되살리기

1.1과 같습니다(`-p uwb-drill` 없이).

```sh
docker compose up -d db
docker compose run --rm migrate
docker compose exec -T db dropdb -U uwb uwb
docker compose exec -T db createdb -U uwb uwb
rclone lsf $R/db | sort | tail -n 1
rclone copyto $R/db/<가장 최근 덤프> ./restore.dump
docker compose exec -T db pg_restore -U uwb -d uwb --exit-on-error < restore.dump
docker compose run --rm migrate
```

### 2.3 raw → `intake.*` (TL-3)

덤프 뒤에 받은 묶음의 행을 raw 파일에서 다시 만듭니다. 이미 있는 키는 건너뛰므로 전부 돌려도 됩니다. 빨리 하려면 `--since`에 덤프 시각의 1시간 전을 줍니다.

```sh
docker compose run --rm --no-deps intake python tools/reindex_raw.py --since 2026-10-28T17:30Z
```

마지막 줄 `reindex_raw_done`의 `inserted`가 다시 넣은 묶음 수입니다. 종료 코드 1이면 읽지 못한 레코드가 `raw_record_unreadable` 줄에 있습니다. 백업이 파일을 쓰는 중에 복사해 생긴 잘린 끝 레코드는 오류가 아니고 그냥 건너뜁니다. intake가 시작할 때(IN-9) 그 꼬리를 잘라 냅니다.

### 2.4 archive → `archive.range`, 첫 번째 (TL-4)

```sh
docker compose run --rm --no-deps core python tools/verify_archive.py --reindex
```

덤프 뒤에 만들어진 archive 파일의 행을 되살립니다. 그 파일이 속한 구간이 아직 DB에 없으면 `no_segment`로 건너뜁니다. 그 구간은 다음 단계에서 core가 raw 재색인분으로 만듭니다.

### 2.5 분석을 멈춘 채로 띄우기

`no_segment`가 남았으면, 그 행을 되살리기 전에 새 분석이 같은 범위의 archive 파일을 따로 만들지 않도록 분석을 잠시 멈춥니다. `.env`에서 `ANALYSIS_WORKERS=0`으로 바꾸고 띄웁니다. 그래도 구간은 만들어집니다. `no_segment`가 없었으면 이 단계와 2.6은 건너뛰고 2.7로 갑니다.

```sh
docker compose up -d
```

core가 다시 넣은 묶음을 다 처리했는지 봅니다. 두 값이 같아지면 됩니다.

```sh
docker compose exec -T db psql -U uwb -d uwb -c "
  SELECT (SELECT last_id FROM pipeline.cursor WHERE name = 'chunk') AS cursor,
         (SELECT max(id) FROM intake.chunk) AS max_chunk_id"
```

### 2.6 archive → `archive.range`, 두 번째

```sh
docker compose exec core python tools/verify_archive.py --reindex
```

종료 코드 0이면 모든 archive 파일에 행이 있습니다. `overlap`이 나오면 그 파일을 지우지 말고 기록해 두고 살펴봅니다(그 seq를 다른 행이 이미 덮고 있음). 그 뒤 `.env`의 `ANALYSIS_WORKERS`를 원래 값으로 돌리고 core를 다시 띄웁니다.

```sh
docker compose up -d core
```

### 2.7 분석 결과

- 덤프 때 대기 중이던 분석 작업은 core가 다시 돌립니다. 덤프 뒤에 닫힌 구간은 닫힐 때 작업이 들어갑니다(SG-5).
- 그래도 run이 없고 대기 작업도 없는 닫힌 구간을 찾아 재분석합니다.

```sh
docker compose exec -T db psql -U uwb -d uwb -At -c "
  SELECT s.id FROM segment.segment s
  WHERE s.state = 'closed'
    AND NOT EXISTS (SELECT 1 FROM analysis.run r WHERE r.segment_id = s.id)
    AND NOT EXISTS (SELECT 1 FROM pipeline.job j
                    WHERE j.kind = 'analysis' AND j.segment_id = s.id AND j.state IN ('queued','running'))" \
| while read -r id; do docker compose run --rm --no-deps api python tools/admin.py reanalyze "$id"; done
```

- 옛 run의 결과 원문과 궤적 파일은 없으므로 앱에서 404가 납니다. 필요한 구간은 재분석하면 새 run으로 다시 생깁니다.

### 2.8 사람 입력과 백업 다시 켜기

- 마지막 덤프 뒤에 넣은 `registry` 입력(장치 등록, 착용 기록, 계정)을 다시 넣습니다. 덤프 뒤에 등록한 장치는 토큰이 사라졌으므로 다시 등록하고 Head에 새 토큰을 넣습니다.
- `ops/crontab`을 설치하고 `ops/backup.sh -v all`을 한 번 돌려 성공하는지 봅니다.

---

## 3. 열이나 표를 지우는 마이그레이션 전 (OP-5)

달마다 첫 덤프 말고는 14일 뒤에 지워지므로, 따로 받아 따로 둡니다.

```sh
docker compose exec -T db pg_dump -U uwb -d uwb -Fc > before-NNN.dump
rclone copyto before-NNN.dump $R/db-manual/before-NNN.dump
```
