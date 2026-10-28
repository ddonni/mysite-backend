# mysite-backend

개인 사이트의 여러 "방"이 공유하는 백엔드 API 서버. 프론트엔드는 별도의
[mysite](../mysite) 레포에 있음.

- **스케치북** (`sketchbook.html`) — 번호가 매겨진 페이지를 넘기며
  그리는 캔버스. 그림 저장/실시간 동기화에 REST API + WebSocket 사용.
- **기록 보관소** (`library.html`) — 읽거나 본 책/애니/영화/음악 기록.
  REST API로 CRUD, 사진은 S3에 업로드하고 URL만 저장. 그중 하나를
  카테고리마다 최대 3개까지 "인생작"으로 표시할 수 있음(로비의 책장
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
- **S3(boto3)** — 기록 보관소 사진 저장소. `POST /api/rooms/{code}/uploads`가 파일을
  받아 S3에 올리고 URL을 돌려줌.
- **Docker + docker-compose** — API 컨테이너와 PostgreSQL 컨테이너를 한 번에 실행.
- **GitHub Actions** — 푸시·PR마다 테스트 실행 → `main`에 push됐고 통과하면
  운영 서버(EC2)에 자동 배포(아래 "배포").

## 로컬 실행

```bash
docker compose up --build
```

- API: `http://localhost:8000`
- 자동 생성된 API 문서: `http://localhost:8000/docs`

`.env.example`을 `.env`로 복사해서 값을 바꿀 수 있음(docker-compose가 자동으로 읽음).

## 배포 (직접 운영하는 EC2)

2026-10-18부터 AWS 서울 리전의 EC2 한 대에서 직접 운영함
(`https://mysite-ddonni.duckdns.org`). 그 전에는 Render(무료) + Neon(무료 PostgreSQL)이었음.
서버 구성, 선택 근거, 전환 절차, 장애 실험 기록은
[mysite-infra](https://github.com/ddonni/mysite-infra) 저장소에 있음.

```
브라우저 ──HTTPS/WSS──▶ Nginx(:443, Let's Encrypt) ──▶ uvicorn(127.0.0.1:8000, systemd)
                                                         ├──▶ PostgreSQL 18 (같은 서버, 127.0.0.1)
                                                         └──▶ S3 사진 버킷 (EC2 역할, 키 없음)
```

- 앱은 systemd 서비스로, 로그인할 수 없는 전용 계정으로 실행함. 죽으면 자동으로 다시 뜸.
- 환경 변수는 서버의 root 전용 파일(600)에 있음. AWS 키는 쓰지 않고 EC2 역할로 S3에 접근함.
- DB는 매일 S3로 백업하고, 복원 훈련까지 해 봄.
- **배포는 자동**(2026-10-28부터): `main`에 push → 테스트 통과 → GitHub OIDC로 AWS 역할을
  빌림(저장된 AWS 키 없음) → SSM으로 서버의 배포 스크립트 실행(SSH 없음) →
  `/health/db` 확인. 테스트를 통과한 **바로 그 커밋**만 배포하고, 실패하면 디스코드로 알림이 옴.
- 손으로 배포하거나 이전 버전으로 되돌릴 때도 같은 스크립트를 씀:
  `sudo mysite-deploy <커밋 SHA 40자리>`. 롤백 방법은 mysite-infra README "롤백·복구 방법".

Render 서비스와 Neon DB는 병행 운영을 마치고 2026-10-25에 삭제함(`render.yaml`도 지움).

스키마 변경은 [Alembic](https://alembic.sqlalchemy.org/)으로 관리함
(`alembic/versions/`). 기동할 때마다 `app/migrations.py`가 자동으로
`alembic upgrade head`를 실행하므로, 서버에 새 코드를 받고 재시작하면
마이그레이션도 같이 적용됨. 새 컬럼 등을 추가할 땐:

```bash
docker compose up -d db
DATABASE_URL=postgresql+psycopg2://sketchbook:sketchbook@localhost:5432/sketchbook \
  alembic revision --autogenerate -m "무엇을 바꿨는지"
```

으로 리비전 파일을 만들고, 생성된 내용을 검토한 뒤 커밋함.

기록 보관소의 사진 업로드를 쓰려면 S3 버킷도 하나 필요함:

1. 앱 서버와 같은 리전에 S3 버킷 생성 (운영: `mysite-photos-ddonni`, 서울).
2. 버킷 정책으로 **`records/*`만** `GetObject`를 퍼블릭 허용 (업로드된 사진 URL이
   그대로 `<img>` 태그에서 로딩돼야 하므로). 퍼블릭 액세스 차단은 ACL 관련 두 개만
   켜 두고, 버킷 정책 관련 두 개를 끔.
3. 앱이 도는 곳에 업로드 권한 주기 — 앱이 하는 일은 `records/<uuid>.<확장자>`로
   `put_object` 하나뿐이라 `s3:PutObject` on `arn:aws:s3:::<버킷>/records/*`면 충분함.
   - **EC2(운영)**: 인스턴스 역할에 이 정책을 붙임. 액세스 키는 필요 없음.
   - 키가 필요한 환경(로컬 docker 등): 같은 권한만 가진 IAM 사용자의 키를
     `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`로 넣음(`.env`, git에 올리지 않음).
4. `S3_BUCKET_NAME`, `AWS_REGION`을 환경 변수로 넣음.

구글 계정으로 방 복구 기능을 쓰려면 `GOOGLE_CLIENT_ID`도 필요함:

1. Google Cloud Console에서 OAuth 2.0 클라이언트 ID(웹 애플리케이션)를
   발급.
2. 서버 환경 변수에 `GOOGLE_CLIENT_ID` 값을 넣기(git에는 올리지 않음).
3. `mysite` 레포의 `js/shared/config.js`에 있는 프론트엔드 client_id와
   반드시 같은 값이어야 함.
4. 안 채워도 나머지 기능은 그대로 동작함(`POST /api/auth/google`만 실패).

## 프론트엔드와 연결하기

1. `CORS_ORIGINS` 환경 변수에 프론트엔드가 서비스되는 실제 주소를 넣기
   (쉼표로 여러 개 가능). 운영 값은 `https://ddonni.github.io`. 변수를 아예
   비워두면 코드 기본값인 `*`(모두 허용)가 적용됨.
2. `mysite` 레포의 `js/shared/config.js`에 있는 `API_BASE`를 이 서버의
   배포된 주소(운영: `https://mysite-ddonni.duckdns.org`)로 맞추기.
   WebSocket 주소도 이 값에서 만들어짐(`https` → `wss`).
   스케치북·기록 보관소·로비가 모두 이 값 하나를 같이 씀. localhost에서
   열면 자동으로 `http://localhost:8000`을 봄.

## 테스트

```bash
pip install -r requirements-dev.txt
pytest -q
```

`tests/test_api.py`는 페이지 저장/조회, 페이지 추가, 페이지 삭제 시 뒤
페이지 번호들이 한 칸씩 당겨지는 로직, 기록 보관소 CRUD/인생작
지정, 사진 업로드, 구글 계정 연동/복구를 검증.

## 페이지 삭제가 안전한 이유

`(room_id, page_number)`에는 DB 레벨 UNIQUE 제약이 걸려 있음(방마다 자기만의
1..count 번호 체계). 페이지를 삭제하면 같은 방에서 뒤에 있던 페이지 번호를
전부 하나씩 당겨야 하는데, 이를
`UPDATE pages SET page_number = page_number - 1 WHERE room_id = r AND page_number > n`
한 줄로 처리하면 실행 도중 번호가 일시적으로 겹쳐 UNIQUE 제약을 위반할 수
있음. `app/crud.py`의 `delete_page`는 대상 페이지들의 번호를 먼저 전부
음수로 뒤집었다가 다시 `-1`을 적용해 최종 값을 맞추는 2단계로 처리해서
이 충돌을 피함(음수 구간과 양수 구간은 절대 겹치지 않음).
