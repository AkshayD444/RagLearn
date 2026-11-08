
# ✦ RagLearn

### Learn smarter. Learn with AI.

<p align="center">
  An AI-powered learning platform designed to make studying more interactive, personalized, and effective.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Next.js-000000?style=for-the-badge&logo=next.js&logoColor=white" alt="Next.js" />
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white" alt="TypeScript" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL" />
  <img src="https://img.shields.io/badge/Google_Gemini-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white" alt="Gemini" />
</p>

---

## 📑 Table of Contents

- [Overview](#-overview)
- [Features](#-features)
- [Technology Stack](#-technology-stack)
- [System Architecture](#-system-architecture)
- [Project Structure](#-project-structure)
- [Getting Started](#-getting-started)
- [Environment Configuration](#-environment-configuration)
- [API Documentation](#-api-documentation)
- [Testing](#-testing)
- [Continuous Integration](#-continuous-integration)
- [Future Improvements](#-future-improvements)
- [Contributing](#-contributing)
- [License](#-license)

---

## 🎯 Overview

**RagLearn** is an AI-powered learning assistant that helps learners organize their study materials and interact with educational content through AI.

The platform combines document-based learning with AI-powered assistance to provide a more interactive learning experience.

Instead of simply reading through study materials, users can use RagLearn to interact with their learning resources, ask questions, and work with AI-assisted study tools.

### 💡 Core Idea

Transform traditional study materials into an interactive learning environment using **Retrieval-Augmented Generation (RAG)** and generative AI.

---

## ✨ Features

### 🤖 AI Learning Assistant

- Interact with an AI-powered learning assistant.
- Ask questions and receive contextual explanations.
- Use AI to understand learning materials.

### 📚 Document-Based Learning

- Upload and manage study documents.
- Organize documents under subjects.
- Retrieve relevant information from learning materials.
- Use document context to support AI-generated answers.

### 🧠 Flashcards

- Create and manage flashcards.
- Use flashcards for active recall.
- Review learning materials in smaller, manageable sections.

### 📝 Quizzes

- Generate and attempt quizzes.
- Test understanding of learning materials.
- Use quiz-based practice to reinforce concepts.

### 📅 Study Planning

- Organize learning activities.
- Manage study plans.
- Keep track of learning goals.

### 📊 Learning Analytics

- View learning-related statistics.
- Monitor study activity and progress.
- Get an overview of learning performance.

### 📂 Subject Management

- Organize learning materials by subject.
- Keep related documents and study resources together.
- Manage different areas of learning from one platform.

---

## 🛠️ Technology Stack

RagLearn uses a modern full-stack architecture.

| Layer | Technologies |
|---|---|
| Frontend | Next.js, React, TypeScript |
| Styling | Tailwind CSS |
| Data Fetching | TanStack Query |
| State Management | Zustand |
| Backend | Python, FastAPI |
| ORM | SQLAlchemy 2.0 Async |
| Database Driver | AsyncPG |
| Validation | Pydantic v2 |
| Database | PostgreSQL 16 |
| Vector Database | pgvector |
| AI Integration | Google Gemini API |
| Containerization | Docker, Docker Compose |
| Testing | Pytest, frontend testing tools |

---

## 🏗️ System Architecture

RagLearn follows a frontend-backend architecture.

```text
                    ┌──────────────────────┐
                    │       USER           │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │    NEXT.JS FRONTEND  │
                    │                      │
                    │  React + TypeScript  │
                    │  Tailwind CSS        │
                    │  TanStack Query      │
                    │  Zustand             │
                    └──────────┬───────────┘
                               │
                          HTTP / REST
                               │
                               ▼
                    ┌──────────────────────┐
                    │    FASTAPI BACKEND   │
                    │                      │
                    │  Authentication      │
                    │  API Endpoints       │
                    │  Business Logic      │
                    │  AI Integration      │
                    └──────────┬───────────┘
                               │
                  ┌────────────┴────────────┐
                  │                         │
                  ▼                         ▼
       ┌────────────────────┐   ┌────────────────────┐
       │    POSTGRESQL      │   │    GOOGLE GEMINI   │
       │                    │   │                    │
       │  Application Data  │   │  Generative AI     │
       │  User Data         │   │  Embeddings        │
       │  Document Data     │   │                    │
       │  pgvector          │   │                    │
       └────────────────────┘   └────────────────────┘
```

### Retrieval-Augmented Generation (RAG)

The document-based AI workflow is designed around Retrieval-Augmented Generation.

```text
      Study Documents
             │
             ▼
      Document Processing
             │
             ▼
        Text Extraction
             │
             ▼
       Text Chunking
             │
             ▼
        Embeddings
             │
             ▼
      Vector Storage
             │
             ▼
       User Question
             │
             ▼
      Relevant Retrieval
             │
             ▼
       Gemini API
             │
             ▼
      Contextual Answer
```

---

## 📁 Project Structure

```text
RagLearn/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── main.py
│   │
│   ├── tests/
│   ├── requirements.txt
│   ├── constraints.txt
│   └── alembic.ini
│
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── lib/
│   │   ├── services/
│   │   └── stores/
│   │
│   ├── package.json
│   └── package-lock.json
│
├── .github/
│   └── workflows/
│
├── docker-compose.yml
├── README.md
└── .gitignore
```

---

## 🚀 Getting Started

Follow these instructions to set up RagLearn locally.

### 📌 Prerequisites

Make sure you have the following installed:

- **Python 3.11+**
- **Node.js and npm**
- **Docker Desktop** or Docker Engine
- **Git**
- **Google Gemini API key**

### 1. Clone the Repository

```bash
git clone https://github.com/AkshayD444/RagLearn.git

cd RagLearn
```

### 2. Start the Database

Start the PostgreSQL database using Docker Compose.

```bash
docker-compose up -d
```

Check whether the containers are running:

```bash
docker-compose ps
```

The database is configured to use:

| Configuration | Value |
|---|---|
| Host | localhost |
| Port | 5432 |
| Database | ai_learning_assistant |

### 3. Set Up the Backend

Navigate to the backend directory:

```bash
cd backend
```

Create a Python virtual environment:

```bash
python -m venv .venv
```

Activate the virtual environment.

**Linux / macOS / WSL:**

```bash
source .venv/bin/activate
```

**Windows PowerShell:**

```powershell
.venv\Scripts\Activate.ps1
```

Install the dependencies:

```bash
pip install -c constraints.txt -r requirements.txt
```

### 4. Configure Backend Environment Variables

Create your environment file:

```bash
cp .env.example .env
```

Configure the required environment variables in `.env`.

**Important:** Never commit your `.env` file or expose API keys and secret credentials.

### 5. Run Database Migrations

Apply the database migrations:

```bash
alembic upgrade head
```

### 6. Start the Backend Server

```bash
uvicorn app.main:app --reload --port 8000
```

The backend will be available at:

```text
http://localhost:8000
```

### 7. Set Up the Frontend

Open another terminal and navigate to the frontend directory:

```bash
cd frontend
```

Install dependencies:

```bash
npm ci
```

Create the frontend environment file:

```bash
cp .env.example .env.local
```

Configure the required frontend environment variables.

### 8. Start the Frontend Server

```bash
npm run dev
```

The frontend will be available at:

```text
http://localhost:3000
```

---

## ⚙️ Environment Configuration

RagLearn requires environment variables for backend and frontend configuration.

### Backend

Configure the following in `backend/.env`, using the names and values required by `backend/.env.example`.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection URL |
| `SECRET_KEY` | Application authentication secret |
| `GEMINI_API_KEY` | Google Gemini API key |

### Frontend

Configure the frontend environment variables in `frontend/.env.local` according to `frontend/.env.example`.

**Note:** Do not share or commit environment files containing credentials.

---

## 🔌 API Documentation

RagLearn provides a REST API powered by FastAPI.

Once the backend server is running, access the interactive API documentation.

| Resource | URL |
|---|---|
| API Documentation | http://localhost:8000/api/v1/docs |
| Health Check | http://localhost:8000/api/v1/health |
| Database Health | http://localhost:8000/api/v1/health/db |

The Swagger UI allows you to explore available endpoints and test API requests.

---

## 🧪 Testing

RagLearn includes backend and frontend testing workflows.

### Backend Tests

Navigate to the backend directory:

```bash
cd backend
```

Run the test suite:

```bash
pytest
```

Run Python compilation checks:

```bash
python -m compileall app
```

### Frontend Tests

Navigate to the frontend directory:

```bash
cd frontend
```

Run TypeScript type checking:

```bash
npm run typecheck
```

Run frontend tests:

```bash
npm run test
```

Run linting:

```bash
npm run lint
```

Build the production application:

```bash
npm run build
```

---

## 🔄 Continuous Integration

RagLearn uses GitHub Actions for automated checks.

The CI workflow includes:

- **Backend testing:** Runs backend tests using SQLite.
- **PostgreSQL testing:** Runs tests requiring PostgreSQL and pgvector.
- **Frontend validation:** Runs type checking, linting, tests, and production builds.

These checks help identify issues before changes are merged into the project.

---

## 🔮 Future Improvements

Potential areas for further development include:

- Enhanced AI-generated learning recommendations.
- More advanced learning analytics.
- Improved flashcard and quiz generation.
- Personalized study schedules.
- Better document processing capabilities.
- Expanded support for different learning resources.
- Improved user experience and accessibility.

---

## 🤝 Contributing

Contributions, suggestions, and feedback are welcome.

If you would like to contribute:

1. Fork the repository.
2. Create a new branch.

```bash
git checkout -b feature/your-feature-name
```

3. Make your changes.
4. Commit your changes.

```bash
git commit -m "Add: your feature description"
```

5. Push your branch.

```bash
git push origin feature/your-feature-name
```

6. Open a Pull Request.

---

## 📄 License

This project is currently maintained as a personal project.

A formal license can be added when the project is ready for distribution.

---

<p align="center">
  <b>✦ RagLearn</b>
  <br />
  Learn smarter. Learn with AI.
</p>
