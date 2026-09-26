# AI Learning Assistant

A personalized, AI-powered learning platform built with **Next.js**, **FastAPI**, **PostgreSQL + pgvector**, and **Google Gemini AI**.

---

## Technical Stack Overview

- **Frontend:** Next.js (App Router), TypeScript, Tailwind CSS, TanStack Query, Zustand
- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2.0 (Async), AsyncPG, Pydantic v2, Google GenAI SDK
- **Database:** PostgreSQL 16 + `pgvector` extension
- **Infrastructure:** Docker Compose

---

## Local Development Setup

### 1. Database (PostgreSQL + pgvector via Docker Compose)

Start the database container:
```bash
docker-compose up -d
```
The database will run on `localhost:5432` with database `ai_learning_assistant`.

---

### 2. Backend Setup (FastAPI)

1. Navigate to the `backend` directory:
   ```bash
   cd backend
   ```
2. Create and activate a Python virtual environment:
   ```bash
   python -m venv venv
   # On Windows PowerShell:
   .\venv\Scripts\Activate.ps1
   # On macOS/Linux:
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Configure environment variables:
   ```bash
   cp .env.example .env
   ```
5. Start the FastAPI development server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```
6. Access API Documentation & Health Endpoints:
   - **Interactive API Docs (Swagger):** `http://localhost:8000/api/v1/docs`
   - **API Health Check:** `http://localhost:8000/api/v1/health`
   - **Database Connection Check:** `http://localhost:8000/api/v1/health/db`

---

### 3. Frontend Setup (Next.js)

1. Navigate to the `frontend` directory:
   ```bash
   cd frontend
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Configure environment variables:
   ```bash
   cp .env.example .env.local
   ```
4. Start the Next.js development server:
   ```bash
   npm run dev
   ```
5. Open `http://localhost:3000` in your browser.

---

## Project Structure

```
AI-Learning-Assistant/
├── docker-compose.yml       # Docker Compose for PostgreSQL + pgvector
├── README.md                # Local setup instructions
├── .github/workflows/ci.yml # CI: backend tests, pgvector tests, frontend checks
├── backend/
│   ├── app/
│   │   ├── api/v1/          # REST API routers (auth, subjects, documents, rag,
│   │   │                    #   quizzes, analytics, recommendations, study-plans, health)
│   │   ├── core/            # Configuration & async SQLAlchemy database setup
│   │   ├── models/          # ORM models (Users, Subjects, Documents, Chunks, Quizzes, etc.)
│   │   ├── schemas/         # Pydantic request/response schemas
│   │   ├── rag/             # Grounded RAG retrieval & Gemini SDK wrappers
│   │   └── services/        # Business logic (Quiz Engine, Student Intelligence,
│   │                        #   Weak Topic Detection, Adaptive Learning,
│   │                        #   Study Planner)
│   ├── tests/               # Pytest suite (SQLite) + tests/integration (pgvector)
│   ├── .env.example
│   ├── app/main.py          # FastAPI entry point
│   ├── pytest.ini
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── app/             # Next.js App Router (Layouts & Pages)
    │   ├── components/      # Feature components (auth, documents, rag, quiz, ...)
    │   ├── lib/             # Typed API client and shared domain types
    │   ├── store/           # Zustand auth store
    │   └── test/            # Vitest unit tests and payload factories
    ├── package.json
    ├── vitest.config.ts
    ├── tailwind.config.ts
    └── tsconfig.json
```

---

## Verification Commands

Run from the repository root.

### Backend

```bash
cd backend
python -m pytest -q            # full SQLite suite
python -m compileall -q app     # byte-compile check
```

The `postgres`-marked tests skip automatically unless a live pgvector instance
is configured:

```bash
docker-compose up -d postgres
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant \
    python -m pytest -q -m postgres
```

### Frontend

```bash
cd frontend
npm install
npm run typecheck              # tsc --noEmit
npm test                       # Vitest
npm run lint                   # next lint
npm run build                  # production build (also type-checks and lints)
```

`.github/workflows/ci.yml` runs these same commands across three jobs: backend
(SQLite), backend pgvector integration tests, and frontend typecheck/lint/test/build.
