RagLearn
Learn smarter with AI.
RagLearn is an AI-powered learning platform designed to make studying more interactive, personalized, and effective.
It combines intelligent AI tutoring, document-based question answering, flashcards, quizzes, adaptive study planning, and learning analytics in one platform.
Built using Next.js, FastAPI, PostgreSQL, pgvector, and Google Gemini AI.

Features

AI Tutor: Ask questions and get AI-powered explanations.
Document-Based Learning: Upload learning materials and interact with their content through retrieval-augmented generation (RAG).
Flashcards: Generate and review flashcards to reinforce learning.
Quizzes: Test your understanding with interactive quizzes.
Study Planning: Organize learning activities through personalized study plans.
Learning Analytics: Track learning progress and identify areas that need improvement.
Subject Management: Organize learning materials and activities by subject.



Technology Stack



Layer
Technologies




Frontend
Next.js, TypeScript, Tailwind CSS


State Management
Zustand, TanStack Query


Backend
Python, FastAPI, SQLAlchemy 2.0


Database
PostgreSQL 16, pgvector


AI Integration
Google Gemini AI


Infrastructure
Docker Compose


Testing
Pytest, Vitest




Project Architecture
RagLearn follows a frontend-backend architecture:

Frontend: Next.js application responsible for the user interface and client-side interactions.
Backend: FastAPI application providing REST APIs, authentication, learning services, and AI-powered functionality.
Database: PostgreSQL with pgvector for persistent data and vector-based retrieval.


Local Development Setup
Prerequisites

Python 3.11+
Node.js and npm
Docker and Docker Compose
Google Gemini API key

1. Start the Database
From the repository root:
docker-compose up -d

The PostgreSQL database will run on localhost:5432 with database name ai_learning_assistant.
2. Backend Setup
Navigate to the backend directory:
cd backend

Create a virtual environment:
python -m venv venv

Activate it:
Windows PowerShell
.\venv\Scripts\Activate.ps1

Linux / macOS
source venv/bin/activate

Install dependencies:
pip install -c constraints.txt -r requirements.txt

Configure environment variables:
cp .env.example .env

Configure the required environment values in .env. Keep your actual secrets private.
Start the backend:
uvicorn app.main:app --reload --port 8000

Backend endpoints:

API Documentation: http://localhost:8000/api/v1/docs
Health Check: http://localhost:8000/api/v1/health
Database Health: http://localhost:8000/api/v1/health/db

3. Frontend Setup
Open another terminal and navigate to the frontend directory:
cd frontend

Install dependencies:
npm ci

Configure environment variables:
cp .env.example .env.local

Start the development server:
npm run dev

Open:
http://localhost:3000

Project Structure
RagLearn/
├── .github/
│   └── workflows/
│       └── ci.yml
├── backend/
│   ├── app/
│   │   ├── api/v1/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── rag/
│   │   └── services/
│   ├── tests/
│   ├── .env.example
│   ├── app/main.py
│   ├── pytest.ini
│   ├── requirements.txt
│   └── constraints.txt
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── lib/
│   │   ├── store/
│   │   └── test/
│   ├── package.json
│   ├── package-lock.json
│   ├── vitest.config.ts
│   └── tsconfig.json
├── docker-compose.yml
├── .gitignore
└── README.md


Verification and Testing
Run these commands to verify the application.
Backend
cd backend

python -m pytest -q
python -m compileall -q app

PostgreSQL integration tests require a running pgvector database:
docker-compose up -d postgres

Then:
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_learning_assistant \
    python -m pytest -q -m postgres

Frontend
cd frontend

npm ci
npm run typecheck
npm test
npm run lint
npm run build


Continuous Integration
The GitHub Actions workflow at .github/workflows/ci.yml runs three jobs:

Backend SQLite tests.
Backend PostgreSQL/pgvector integration tests.
Frontend typechecking, linting, tests, and production build.

Dependency versions are pinned to improve reproducibility. The backend uses requirements.txt and constraints.txt, while the frontend uses package-lock.json with npm ci.

Future Improvements

Further improvements to adaptive learning and recommendations.
Expanded AI tutoring capabilities.
Additional learning analytics and progress insights.
Improvements to the overall learning experience.


License
License information will be added when the project license is selected.
