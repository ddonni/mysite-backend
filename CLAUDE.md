# mysite-backend

개인 사이트의 여러 "방"이 공유하는 백엔드 API 서버.

- **스케치북**: 번호가 매겨진 페이지를 넘기며 그리는 캔버스가 그림을
  저장/실시간 동기화하는 데 쓰는 REST API + WebSocket.
- **기록 보관소**: 읽거나 본 책/애니/영화를 기록하는 목록의 CRUD API.
  사진은 S3에 올리고 URL만 DB에 저장.

## 스택 및 이유

- **FastAPI** — REST(`/api/...`)와 페이지별 실시간 브로드캐스트용
  WebSocket(`/ws/pages/{n}`)을 같은 프레임워크로 처리.
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
  models.py     # SQLAlchemy 모델 (Page, Record)
  schemas.py    # Pydantic 스키마
  crud.py       # DB 접근 로직
  storage.py    # S3 사진 업로드
tests/test_api.py
```

## 반드시 지켜야 할 규칙

- **페이지 번호는 항상 1부터 연속되게 유지한다.** 중간에 빈 번호가 생기면 안 됨.
  `crud.delete_page`는 삭제된 뒤 페이지들을 한 번에
  `page_number = page_number - 1`로 당기지 않고, 먼저 전부 음수로 뒤집었다가
  다시 `-1`을 적용하는 2단계로 처리한다. 한 번에 처리하면 UNIQUE 제약(음수
  변환 없이)이 실행 도중 일시적으로 충돌할 수 있기 때문 — 이 로직을 건드릴
  때는 이 이유를 유지한 채로 수정할 것.
- REST 에러 메시지 문자열이 프론트엔드(`mysite` 레포)와 계약처럼 맞물려
  있음: `GET`/`PUT /api/pages/{n}`의 404는 `"page not found"`,
  `DELETE`의 404는 `"not_found"`, 마지막 페이지 삭제 시도는 400
  `"last_page"`. 이 문자열을 바꾸면 `mysite`의 `sketchbook.html`도 같이
  고쳐야 함.
- `PUT /api/pages/{n}`은 부분 수정이 아니라 해당 페이지의 `strokes`
  전체를 교체하는 API. 저장 성공 시 같은 페이지를 보고 있는 다른
  WebSocket 클라이언트에게 `{"type": "strokes", "strokes": [...]}`을
  브로드캐스트한다.
- `POST /api/uploads`는 사진 파일을 받아 S3에 올리고 URL만 반환한다.
  기록을 저장/수정할 때는 이 URL을 먼저 받아서 `photo_url`로 넘겨야
  함 — 사진 바이트 자체를 `/api/records`로 보내지 않는다.
- `CORS_ORIGINS` 환경 변수로 허용 도메인 제어. 현재 값은 배포된 프론트
  주소(`https://ddonni.github.io`)로 좁혀져 있음.

## 관련 저장소

프론트엔드는 별도 레포 `mysite`에 있음 — `sketchbook.html`(그림판),
`library.html`(기록 보관소). 이 서버가 꺼져 있으면 `sketchbook.html`은
자동으로 이 기기 로컬 저장 모드로 동작하도록 되어 있음(백엔드 쪽에서
별도 대응 불필요). `library.html`은 서버 의존적이라 별도 폴백 없음.
