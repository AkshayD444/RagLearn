const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api/v1";

export interface User {
  id: string;
  email: string;
  full_name: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface Subject {
  id: string;
  user_id: string;
  title: string;
  description: string;
  color: string;
  created_at: string;
}

export interface Document {
  id: string;
  subject_id: string;
  user_id: string;
  title: string;
  file_size_bytes: number;
  status: "PROCESSING" | "READY" | "FAILED";
  error_message?: string | null;
  chunk_count: number;
  created_at: string;
}

export interface RagSource {
  chunk_id: string;
  document_id: string;
  document_title: string;
  chunk_index: number;
  page_number: number;
  content: string;
  score: number;
}

export interface RagAnswerResponse {
  subject_id: string;
  question: string;
  answer: string;
  sources: RagSource[];
  model_used: string;
}

export type QuizDifficulty = "easy" | "medium" | "hard";

export interface QuizQuestion {
  id: string;
  question_text: string;
  option_a: string;
  option_b: string;
  option_c: string;
  option_d: string;
  difficulty: string;
  source_document_id: string | null;
  source_document_title: string | null;
  source_chunk_id: string | null;
  source_page_number: number | null;
  source_snippet: string | null;
}

export interface Quiz {
  id: string;
  subject_id: string;
  title: string;
  difficulty: string;
  question_count: number;
  topic: string | null;
  created_at: string;
  questions: QuizQuestion[];
}

export type SelectedOption = "A" | "B" | "C" | "D";

export interface AnswerItem {
  question_id: string;
  selected_option: SelectedOption;
}

export interface QuestionResult {
  question_id: string;
  question_text: string;
  selected_option: string;
  correct_option: string;
  is_correct: boolean;
  explanation: string;
  difficulty: string;
  source_document_id: string | null;
  source_document_title: string | null;
  source_chunk_id: string | null;
  source_page_number: number | null;
  source_snippet: string | null;
}

export interface QuizResultResponse {
  quiz_id: string;
  title: string;
  total_questions: number;
  score: number;
  percentage: number;
  results: QuestionResult[];
}

class ApiClient {
  private getToken(): string | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem("access_token");
  }

  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const token = this.getToken();
    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string>),
    };

    if (!(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }

    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: "Request failed" }));
      throw new Error(errorData.detail || `HTTP Error ${response.status}`);
    }

    if (response.status === 204) {
      return {} as T;
    }

    return response.json();
  }

  // Auth Endpoints
  async register(payload: { email: string; full_name: string; password: string }): Promise<AuthResponse> {
    return this.request<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async login(payload: { email: string; password: string }): Promise<AuthResponse> {
    return this.request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async getMe(): Promise<User> {
    return this.request<User>("/auth/me");
  }

  // Subject Endpoints
  async getSubjects(): Promise<Subject[]> {
    return this.request<Subject[]>("/subjects");
  }

  async getSubject(id: string): Promise<Subject> {
    return this.request<Subject>(`/subjects/${id}`);
  }

  async createSubject(payload: { title: string; description?: string; color?: string }): Promise<Subject> {
    return this.request<Subject>("/subjects", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async deleteSubject(id: string): Promise<void> {
    return this.request<void>(`/subjects/${id}`, {
      method: "DELETE",
    });
  }

  // Document Endpoints
  async uploadDocument(subjectId: string, file: File): Promise<Document> {
    const formData = new FormData();
    formData.append("subject_id", subjectId);
    formData.append("file", file);

    return this.request<Document>("/documents/upload", {
      method: "POST",
      body: formData,
    });
  }

  async getSubjectDocuments(subjectId: string): Promise<Document[]> {
    return this.request<Document[]>(`/documents/subject/${subjectId}`);
  }

  async getDocument(documentId: string): Promise<Document> {
    return this.request<Document>(`/documents/${documentId}`);
  }

  async deleteDocument(documentId: string): Promise<void> {
    return this.request<void>(`/documents/${documentId}`, {
      method: "DELETE",
    });
  }

  // RAG Tutor Endpoints
  async askRagTutor(
    subjectId: string,
    question: string,
    top_k = 5,
  ): Promise<RagAnswerResponse> {
    return this.request<RagAnswerResponse>("/rag/ask", {
      method: "POST",
      body: JSON.stringify({ subject_id: subjectId, question, top_k }),
    });
  }

  // Quiz Endpoints
  async generateQuiz(
    subjectId: string,
    questionCount: number,
    difficulty: QuizDifficulty,
    topic?: string | null,
  ): Promise<Quiz> {
    return this.request<Quiz>("/quizzes/generate", {
      method: "POST",
      body: JSON.stringify({
        subject_id: subjectId,
        question_count: questionCount,
        difficulty,
        topic: topic || null,
      }),
    });
  }

  async submitQuiz(quizId: string, answers: AnswerItem[]): Promise<QuizResultResponse> {
    return this.request<QuizResultResponse>(`/quizzes/${quizId}/submit`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    });
  }
}

export const api = new ApiClient();
