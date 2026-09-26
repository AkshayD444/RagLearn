import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeSubject } from "./factories";

vi.mock("@/lib/api", () => ({
  api: {
    getSubjects: vi.fn(),
    createSubject: vi.fn(),
    deleteSubject: vi.fn(),
  },
}));

vi.mock("@/store/useAuthStore", () => ({
  useAuthStore: vi.fn(),
}));

vi.mock("@/components/recommendations/RecommendedForYou", () => ({
  RecommendedForYou: () => <div>recommendations-stub</div>,
}));

vi.mock("@/components/study-plan/StudyPlanSection", () => ({
  StudyPlanSection: () => <div>study-plan-stub</div>,
}));

vi.mock("@/components/auth/AuthModal", () => ({
  AuthModal: () => <div>auth-modal-stub</div>,
}));

vi.mock("@/components/dashboard/CreateSubjectModal", () => ({
  CreateSubjectModal: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div>create-subject-modal-stub</div> : null,
}));

import DashboardPage from "@/app/dashboard/page";
import { api } from "@/lib/api";
import { useAuthStore, type AuthState } from "@/store/useAuthStore";

const getSubjects = vi.mocked(api.getSubjects);
const createSubject = vi.mocked(api.createSubject);
const deleteSubject = vi.mocked(api.deleteSubject);
const useAuthStoreMock = vi.mocked(useAuthStore);

const user = {
  id: "22222222-2222-4222-8222-222222222222",
  email: "ada@example.com",
  full_name: "Ada Lovelace",
  created_at: "2026-01-01T00:00:00Z",
};

function mockAuth(overrides: Partial<AuthState> = {}) {
  const state = {
    user,
    token: "jwt-token-value",
    isLoading: false,
    error: null,
    initialize: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    clearError: vi.fn(),
    ...overrides,
  } as AuthState;
  useAuthStoreMock.mockReturnValue(state);
  return state;
}

beforeEach(() => {
  getSubjects.mockReset();
  createSubject.mockReset();
  deleteSubject.mockReset();
  useAuthStoreMock.mockReset();
  getSubjects.mockResolvedValue([]);
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("DashboardPage auth gating", () => {
  it("shows the loading screen while the session is being restored", () => {
    mockAuth({ isLoading: true });

    render(<DashboardPage />);

    expect(screen.getByText("Loading AI Assistant workspace...")).toBeInTheDocument();
  });

  it("shows only the auth modal when there is no session", () => {
    mockAuth({ user: null, token: null });

    render(<DashboardPage />);

    expect(screen.getByText("auth-modal-stub")).toBeInTheDocument();
    expect(screen.queryByText("My Subjects")).not.toBeInTheDocument();
  });

  it("shows only the auth modal when there is a token but no user", () => {
    mockAuth({ user: null });

    render(<DashboardPage />);

    expect(screen.getByText("auth-modal-stub")).toBeInTheDocument();
  });

  it("restores the session on mount", () => {
    const state = mockAuth();

    render(<DashboardPage />);

    expect(state.initialize).toHaveBeenCalledTimes(1);
  });

  it("does not fetch subjects without a token", () => {
    mockAuth({ user: null, token: null });

    render(<DashboardPage />);

    expect(getSubjects).not.toHaveBeenCalled();
  });

  it("fetches subjects once a session exists", async () => {
    mockAuth();

    render(<DashboardPage />);

    await waitFor(() => expect(getSubjects).toHaveBeenCalledTimes(1));
  });

  it("logs out on demand", () => {
    const state = mockAuth();

    render(<DashboardPage />);
    fireEvent.click(screen.getByTitle("Logout"));

    expect(state.logout).toHaveBeenCalledTimes(1);
  });
});

describe("DashboardPage header and stats", () => {
  it("greets the user by their first name", () => {
    mockAuth();
    getSubjects.mockResolvedValue([makeSubject()]);

    render(<DashboardPage />);

    expect(screen.getByText("Welcome back, Ada!")).toBeInTheDocument();
    expect(screen.getByText("ada@example.com")).toBeInTheDocument();
  });

  it("counts the enrolled subjects", async () => {
    mockAuth();
    getSubjects.mockResolvedValue([
      makeSubject({ id: "s1", title: "Chemistry" }),
      makeSubject({ id: "s2", title: "Physics" }),
    ]);

    render(<DashboardPage />);

    const stats = screen.getByText("Enrolled Subjects").parentElement as HTMLElement;
    await waitFor(() => expect(within(stats).getByText("2")).toBeInTheDocument());
  });

  it("renders the placeholder chunk and mastery stats", () => {
    mockAuth();

    render(<DashboardPage />);

    expect(screen.getByText("RAG Document Chunks")).toBeInTheDocument();
    expect(screen.getByText("0 Chunks")).toBeInTheDocument();
    expect(screen.getByText("Topic Mastery Score")).toBeInTheDocument();
    expect(screen.getByText("0%")).toBeInTheDocument();
  });

  it("mounts the recommendations and study-plan sections", () => {
    mockAuth();

    render(<DashboardPage />);

    expect(screen.getByText("recommendations-stub")).toBeInTheDocument();
    expect(screen.getByText("study-plan-stub")).toBeInTheDocument();
  });
});

describe("DashboardPage subject list", () => {
  it("prompts for a first subject when the list is empty", async () => {
    mockAuth();

    render(<DashboardPage />);

    expect(await screen.findByText("No Subjects Added Yet")).toBeInTheDocument();
  });

  it("opens the create-subject modal from the header button", async () => {
    mockAuth();

    render(<DashboardPage />);
    expect(screen.queryByText("create-subject-modal-stub")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Add Subject/ }));

    expect(screen.getByText("create-subject-modal-stub")).toBeInTheDocument();
  });

  it("opens the create-subject modal from the empty state", async () => {
    mockAuth();

    render(<DashboardPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Create First Subject/ }));

    expect(screen.getByText("create-subject-modal-stub")).toBeInTheDocument();
  });
});

describe("DashboardPage error handling", () => {
  it("keeps the dashboard usable when subjects fail to load", async () => {
    mockAuth();
    getSubjects.mockRejectedValue(new Error("Network down"));

    render(<DashboardPage />);

    expect(await screen.findByText("No Subjects Added Yet")).toBeInTheDocument();
    expect(screen.getByText("Enrolled Subjects")).toBeInTheDocument();
  });
});
