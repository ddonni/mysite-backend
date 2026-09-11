# mysite-backend

번호가 매겨진 페이지를 넘기며 그리는 "스케치북" 웹앱의 백엔드 API 서버.
프론트엔드(`sketchbook.html`)는 별도의 [mysite](../mysite) 레포에 있고,
이 서버는 그 프론트엔드가 그림을 저장/불러오고 실시간으로 동기화하는 데
사용하는 REST API + WebSocket을 제공.

## 스택

- **[FastAPI](https://fastapi.tiangolo.com/)** — REST API(`/api/...`)와
  페이지별 실시간 브로드캐스트용 WebSocket(`/ws/pages/{n}`).
- **PostgreSQL** — SQLAlchemy ORM으로 접근. `DATABASE_URL` 환경 변수만
  바꾸면 동일한 코드로 로컬 테스트용 SQLite도 그대로 동작(테스트가 이 방식 사용).
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

## 프론트엔드와 연결하기

1. 이 서버를 실제로 배포한 뒤(Render, Fly.io, AWS 등), `CORS_ORIGINS` 환경
   변수에 프론트엔드가 서비스되는 실제 주소(예: `https://<사용자명>.github.io`)를 넣기.
2. `mysite` 레포의 `sketchbook.html` 상단 `API_BASE` 상수를 이 서버의 배포된
   주소로 바꾸기.

## 테스트

```bash
pip install -r requirements-dev.txt
pytest -q
```

`tests/test_api.py`는 페이지 저장/조회, 페이지 추가, 그리고 페이지 삭제 시
뒤 페이지 번호들이 한 칸씩 당겨지는 로직을 검증.

## 페이지 삭제가 안전한 이유

`page_number`에는 DB 레벨 UNIQUE 제약이 걸려 있음. 페이지를 삭제하면 뒤에
있던 페이지 번호를 전부 하나씩 당겨야 하는데, 이를
`UPDATE pages SET page_number = page_number - 1 WHERE page_number > n`
한 줄로 처리하면 실행 도중 번호가 일시적으로 겹쳐 UNIQUE 제약을 위반할 수
있음. `app/crud.py`의 `delete_page`는 대상 페이지들의 번호를 먼저 전부
음수로 뒤집었다가 다시 `-1`을 적용해 최종 값을 맞추는 2단계로 처리해서
이 충돌을 피함(음수 구간과 양수 구간은 절대 겹치지 않음).
