# mysite-backend

개인 사이트의 여러 "방"이 공유하는 백엔드 API 서버. 프론트엔드는 별도의
[mysite](../mysite) 레포에 있음.

- **스케치북** (`sketchbook.html`) — 번호가 매겨진 페이지를 넘기며
  그리는 캔버스. 그림 저장/실시간 동기화에 REST API + WebSocket 사용.
- **기록 보관소** (`library.html`) — 읽거나 본 책/애니/영화/음악 기록.
  REST API로 CRUD, 사진은 S3에 업로드하고 URL만 저장. 그중 하나를
  카테고리마다 최대 3개까지 "대표작"으로 표시할 수 있음(로비의 책장
  위 액자/영화 포스터/애니 진열장 맨 앞자리에 걸림).
- **로비** — 방마다 3D 씬 색 팔레트(`wood`/`night`/`pastel`)를 고를 수
  있음.

모든 데이터는 **방(room)** 단위로 나뉨 — 처음 방문하면 자동으로 방
코드(6자리)와 비밀 토큰을 발급받고(로그인 없음), 그 코드를 아는
사람은 누구나 방을 구경할 수 있지만(읽기 전용) 토큰을 가진 본인만
그리거나 기록을 남길 수 있음. 브라우저 localStorage가 지워지거나
기기를 옮긴 경우, 구글 계정을 한 번 연결해두면 그 계정으로 다시
로그인해서 토큰을 복구할 수 있음(로그인 시스템은 아님).

## 스택

- **[FastAPI](https://fastapi.tiangolo.com/)** — REST API(`/api/rooms/{code}/...`)와
  페이지별 실시간 브로드캐스트용 WebSocket(`/ws/rooms/{code}/pages/{n}`).
- **PostgreSQL** — SQLAlchemy ORM으로 접근. `DATABASE_URL` 환경 변수만
  바꾸면 동일한 코드로 로컬 테스트용 SQLite도 그대로 동작(테스트가 이 방식 사용).
- **Alembic** — Postgres 스키마 변경 이력 관리. 기동할 때마다 자동으로
  최신 리비전까지 적용됨(SQLite 테스트는 매번 새로 만들어서 필요 없음).
- **S3(boto3)** — 기록 보관소 사진 저장소. `POST /api/uploads`가 파일을
  받아 S3에 올리고 URL을 돌려줌.
- **Docker + docker-compose** — API 컨테이너와 PostgreSQL 컨테이너를 한 번에 실행.
- **GitHub Actions** — 푸시할 때마다 테스트 실행 → 통과하면 Docker 이미지를
  빌드해서 GitHub Container Registry(ghcr.io)에 푸시.

## 로컬 실행

```bash
docker compose up --build
```

- API: `http://localhost:8000`
- 자동 생성된 API 문서: `http://localhost:8000/docs`

`.env.example`을 `.env`로 복사해서 값을 바꿀 수 있음(docker-compose가 자동으로 읽음).

## 배포 (Render)

`render.yaml`이 API 서비스 + PostgreSQL DB를 함께 정의해둔 Render
Blueprint. Render 대시보드에서 "New +" → "Blueprint"로 이 GitHub 레포를
연결하면 두 서비스가 자동으로 생성되고, 이후 `main`에 push할 때마다
자동 배포됨.

스키마 변경은 [Alembic](https://alembic.sqlalchemy.org/)으로 관리함
(`alembic/versions/`). 기동할 때마다 `app/migrations.py`가 자동으로
`alembic upgrade head`를 실행하므로, 새 리비전을 추가해서 push하면
그걸로 끝 — 별도 수동 배포 단계 없음. 새 컬럼 등을 추가할 땐:

```bash
docker compose up -d db
DATABASE_URL=postgresql+psycopg2://sketchbook:sketchbook@localhost:5432/sketchbook \
  alembic revision --autogenerate -m "무엇을 바꿨는지"
```

으로 리비전 파일을 만들고, 생성된 내용을 검토한 뒤 커밋함.

기록 보관소의 사진 업로드를 쓰려면 S3 버킷도 하나 필요함:

1. AWS에 S3 버킷 생성 (리전 아무거나, 나중에 `AWS_REGION`에 맞춰 적으면 됨).
2. 버킷 정책으로 `GetObject`를 퍼블릭 허용 (업로드된 사진 URL이 그대로
   `<img>` 태그에서 로딩돼야 하므로 — "Block all public access" 해제 필요).
3. 그 버킷에만 `PutObject` 권한을 가진 IAM 사용자를 만들고 액세스 키 발급.
4. Render 대시보드 → `sketchbook-api` → Environment 탭에서
   `S3_BUCKET_NAME`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY` 네 값을 채워 넣기 (`render.yaml`에는
   `sync: false`로만 선언돼 있어 git에는 값이 올라가지 않음).

구글 계정으로 방 복구 기능을 쓰려면 `GOOGLE_CLIENT_ID`도 필요함:

1. Google Cloud Console에서 OAuth 2.0 클라이언트 ID(웹 애플리케이션)를
   발급.
2. Render 대시보드 → `sketchbook-api` → Environment 탭에서
   `GOOGLE_CLIENT_ID` 값 채우기(이것도 `sync: false`라 git에는 안 올라감).
3. `mysite` 레포의 `js/shared/config.js`에 있는 프론트엔드 client_id와
   반드시 같은 값이어야 함.
4. 안 채워도 나머지 기능은 그대로 동작함(`POST /api/auth/google`만 실패).

## 프론트엔드와 연결하기

1. Render에 배포한 뒤, `CORS_ORIGINS` 환경 변수에 프론트엔드가 서비스되는
   실제 주소(예: `https://<사용자명>.github.io`)를 넣기(현재 `render.yaml`
   기본값은 `*`).
2. `mysite` 레포의 `sketchbook.html` 상단 `API_BASE` 상수를 이 서버의 배포된
   주소(`https://sketchbook-api.onrender.com` 형태)로 바꾸기.

## 테스트

```bash
pip install -r requirements-dev.txt
pytest -q
```

`tests/test_api.py`는 페이지 저장/조회, 페이지 추가, 페이지 삭제 시 뒤
페이지 번호들이 한 칸씩 당겨지는 로직, 기록 보관소 CRUD/대표작
지정, 사진 업로드, 구글 계정 연동/복구를 검증.

## 페이지 삭제가 안전한 이유

`page_number`에는 DB 레벨 UNIQUE 제약이 걸려 있음. 페이지를 삭제하면 뒤에
있던 페이지 번호를 전부 하나씩 당겨야 하는데, 이를
`UPDATE pages SET page_number = page_number - 1 WHERE page_number > n`
한 줄로 처리하면 실행 도중 번호가 일시적으로 겹쳐 UNIQUE 제약을 위반할 수
있음. `app/crud.py`의 `delete_page`는 대상 페이지들의 번호를 먼저 전부
음수로 뒤집었다가 다시 `-1`을 적용해 최종 값을 맞추는 2단계로 처리해서
이 충돌을 피함(음수 구간과 양수 구간은 절대 겹치지 않음).
