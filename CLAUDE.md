# mysite-backend

개인 사이트의 여러 "방"이 공유하는 백엔드 API 서버. 모든 데이터는
**room**(사용자별 공간) 단위로 격리됨 — 방마다 자기만의 스케치북 +
기록 보관소를 가짐.

- **스케치북**: 번호가 매겨진 페이지를 넘기며 그리는 캔버스가 그림을
  저장/실시간 동기화하는 데 쓰는 REST API + WebSocket.
- **기록 보관소**: 읽거나 본 책/애니/영화를 기록하는 목록의 CRUD API.
  사진은 S3에 올리고 URL만 DB에 저장.

## 방(Room) 모델 — 반드시 이해해야 할 핵심 개념

- 방은 `code`(6자리, URL/공유용, 예: `X7K2QM`)와 `token`(길고 비밀,
  소유자만 앎)을 가짐. `POST /api/rooms`로 새 방을 만들면 이 둘을
  한 번만 돌려받음 — 서버는 token을 다시 보여주지 않으니 클라이언트가
  받는 즉시 저장해야 함(프론트는 localStorage에 저장).
- 모든 `/api/rooms/{code}/...` 엔드포인트 중 **GET은 code만 있으면
  누구나 호출 가능**(읽기 전용 — 남의 방을 구경할 때 씀).
  **POST/PUT/DELETE는 `X-Room-Token` 헤더가 그 방의 진짜 token과
  일치해야만** 통과함(`main.py`의 `require_owner` 의존성). 이게 바로
  "남의 방은 보기만 가능, 내 방만 수정 가능"의 전부 — 별도의
  로그인/세션은 없음.
- `Page`/`Record`는 둘 다 `room_id`를 가지며, `Page`의
  `(room_id, page_number)`가 유니크 — 방마다 자기만의 1..count
  페이지 번호 체계를 가짐(예전엔 `page_number` 전역 유니크였음).

## 스택 및 이유

- **FastAPI** — REST(`/api/...`)와 페이지별 실시간 브로드캐스트용
  WebSocket(`/ws/rooms/{code}/pages/{n}`)을 같은 프레임워크로 처리.
- **PostgreSQL + SQLAlchemy ORM** — `DATABASE_URL` 환경 변수만 바꾸면
  동일 코드로 테스트용 SQLite도 그대로 동작 (테스트가 이 방식 사용).
- **S3(boto3)** — 기록 보관소의 사진 저장소. DB에는 이미지 바이트 대신
  `photo_url`만 들어감.
- **Docker + docker-compose** — `api` 컨테이너 + `db`(Postgres) 컨테이너.
- **GitHub Actions** (`.github/workflows/ci.yml`) — push마다 pytest 실행 →
  통과하면 Docker 이미지 빌드해서 `ghcr.io/<owner>/sketchbook-api`에 푸시
  (참고용 빌드일 뿐, 실제 배포는 아래 Render가 소스에서 직접 빌드).

배포 호스트는 **Render**. `render.yaml`(Blueprint)이 API 서비스와
PostgreSQL DB를 함께 정의하고, `main` push마다 자동 재배포됨.
`DATABASE_URL`은 `fromDatabase`로 자동 주입. S3 관련 4개 변수
(`S3_BUCKET_NAME`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`)는 `render.yaml`에 `sync: false`로만 선언돼
있고 실제 값은 Render 대시보드 Environment 탭에서 수동으로 채워야 함.

## 빌드/테스트 명령

```bash
docker compose up --build          # 로컬 실행 (API: localhost:8000, 문서: /docs)
pip install -r requirements-dev.txt && pytest -q   # 테스트
```

## 저장소 구조

```
app/
  main.py       # FastAPI 앱, CORS, WebSocket ConnectionManager, 모든 라우트
  models.py     # SQLAlchemy 모델 (Room, Page, Record)
  schemas.py    # Pydantic 스키마
  crud.py       # DB 접근 로직, 방 코드/토큰 생성
  storage.py    # S3 사진 업로드
  migrations.py # rooms 도입 전 프로덕션 DB를 위한 1회성 스키마 보정
tests/test_api.py
```

## 반드시 지켜야 할 규칙

- **페이지 번호는 각 방 안에서 항상 1부터 연속되게 유지한다.** 중간에
  빈 번호가 생기면 안 됨. `crud.delete_page`는 삭제된 뒤 페이지들을
  한 번에 `page_number = page_number - 1`로 당기지 않고, 먼저 전부
  음수로 뒤집었다가 다시 `-1`을 적용하는 2단계로 처리한다. 한 번에
  처리하면 `(room_id, page_number)` UNIQUE 제약이 실행 도중 일시적으로
  충돌할 수 있기 때문 — 이 로직을 건드릴 때는 이 이유를 유지한 채로
  수정할 것.
- REST 에러 메시지 문자열이 프론트엔드(`mysite` 레포)와 계약처럼 맞물려
  있음: `GET`/`PUT /api/rooms/{code}/pages/{n}`의 404는
  `"page not found"`, `DELETE`의 404는 `"not_found"`, 마지막 페이지
  삭제 시도는 400 `"last_page"`, 방 코드 자체가 없으면 404
  `"room not found"`, 토큰이 없거나 틀리면 403 `"not room owner"`.
  이 문자열을 바꾸면 `mysite`의 프론트 코드도 같이 고쳐야 함.
- `PUT /api/rooms/{code}/pages/{n}`은 부분 수정이 아니라 해당 페이지의
  `strokes` 전체를 교체하는 API. 저장 성공 시 같은 (room, page)를 보고
  있는 다른 WebSocket 클라이언트에게
  `{"type": "strokes", "strokes": [...]}`을 브로드캐스트한다.
- `POST /api/rooms/{code}/uploads`는 사진 파일을 받아 S3에 올리고
  URL만 반환한다(소유자만). 기록을 저장/수정할 때는 이 URL을 먼저
  받아서 `photo_url`로 넘겨야 함 — 사진 바이트 자체를
  `/api/rooms/{code}/records`로 보내지 않는다.
- `CORS_ORIGINS` 환경 변수로 허용 도메인 제어. 현재 값은 배포된 프론트
  주소(`https://ddonni.github.io`)로 좁혀져 있음.
- `migrations.py`는 프로덕션(Postgres)에서만 동작하고 SQLite(테스트)는
  즉시 스킵한다 — 테스트용 DB는 매번 `create_all()`로 처음부터 현재
  스키마로 만들어지기 때문에 보정할 게 없음. 이 파일은 프로덕션이
  rooms 이전 스키마를 완전히 벗어난 뒤(=확인 후)에는 삭제해도 됨.

## 관련 저장소

프론트엔드는 별도 레포 `mysite`에 있음 — `sketchbook.html`(그림판),
`library.html`(기록 보관소), `index.html`(로비, 방 코드 입력/생성).
서버가 꺼져 있으면 `sketchbook.html`은 자동으로 이 기기 로컬 저장
모드로 동작하도록 되어 있음(백엔드 쪽에서 별도 대응 불필요).
`library.html`은 서버 의존적이라 별도 폴백 없음.
