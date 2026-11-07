import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeLearningTotals, makeAnalyticsOverview, makeSubject } from "./factories";

const pushMock = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getSubjects: vi.fn(),
    createSubject: vi.fn(),
    deleteSubject: vi.fn(),
    getAnalyticsOverview: vi.fn(),
    getNextLearning: vi.fn(),
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

vi.mock("@/components/dashboard/DueTodaySection", () => ({
  DueTodaySection: () => <div>due-today-stub</div>,
}));

vi.mock("@/components/dashboard/NextBestAction", () => ({
  NextBestAction: () => <div>next-best-action-stub</div>,
}));

// Stubbed, but with the navigation callback re-exposed so the wiring from the
// dashboard into the router is still covered here rather than only in isolation.
vi.mock("@/components/history/HistorySection", () => ({
  HistorySection: ({ onNavigateToSubject }: { onNavigateToSubject?: (id: string) => void }) => (
    <button type="button" onClick={() => onNavigateToSubject?.("subject-from-history")}>
      history-section-stub
    </button>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
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
const getAnalyticsOverview = vi.mocked(api.getAnalyticsOverview);
const getNextLearning = vi.mocked(api.getNextLearning);
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
    isAuthenticated: true,
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
  pushMock.mockReset();
  getSubjects.mockReset();
  createSubject.mockReset();
  deleteSubject.mockReset();
  getAnalyticsOverview.mockReset();
  getNextLearning.mockReset();
  useAuthStoreMock.mockReset();
  getSubjects.mockResolvedValue([]);
  getAnalyticsOverview.mockResolvedValue(makeAnalyticsOverview());
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("DashboardPage auth gating", () => {
  it("shows the loading screen while the session is being restored", () => {
    mockAuth({ isLoading: true });

    render(<DashboardPage />);

    expect(screen.getByText("Loading AI Assistant workspace...")).toBeInTheDocument();
  });

  it("shows only the auth modal when there is no session", () => {
    mockAuth({ user: null, isAuthenticated: false });

    render(<DashboardPage />);

    expect(screen.getByText("auth-modal-stub")).toBeInTheDocument();
    expect(screen.queryByText("My Subjects")).not.toBeInTheDocument();
  });

  it("shows only the auth modal when there is a session but no user", () => {
    mockAuth({ user: null });

    render(<DashboardPage />);

    expect(screen.getByText("auth-modal-stub")).toBeInTheDocument();
  });

  it("restores the session on mount", () => {
    const state = mockAuth();

    render(<DashboardPage />);

    expect(state.initialize).toHaveBeenCalledTimes(1);
  });

  it("does not fetch subjects without a session", () => {
    mockAuth({ user: null, isAuthenticated: false });

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

  it("renders live chunk and mastery stats from the analytics overview", async () => {
    mockAuth();
    getAnalyticsOverview.mockResolvedValue(
      makeAnalyticsOverview({
        totals: makeLearningTotals({ chunk_count: 36, average_topic_mastery: 62.5 }),
      }),
    );

    render(<DashboardPage />);

    expect(screen.getByText("RAG Document Chunks")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("36 Chunks")).toBeInTheDocument());
    expect(screen.getByText("Topic Mastery Score")).toBeInTheDocument();
    expect(screen.getByText("63%")).toBeInTheDocument();
  });

  it("keeps placeholder stats when the analytics overview fails to load", async () => {
    mockAuth();
    getAnalyticsOverview.mockRejectedValue(new Error("Overview down"));

    render(<DashboardPage />);

    await waitFor(() => expect(console.error).toHaveBeenCalled());
    expect(screen.getByText("0 Chunks")).toBeInTheDocument();
    expect(screen.getByText("0%")).toBeInTheDocument();
  });

  it("mounts the next best action section", async () => {
    mockAuth();

    render(<DashboardPage />);

    expect(screen.getByText("Next Best Action")).toBeInTheDocument();
    expect(screen.getByText("next-best-action-stub")).toBeInTheDocument();
  });

  it("mounts the recommendations and study-plan sections", () => {
    mockAuth();

    render(<DashboardPage />);

    expect(screen.getByText("recommendations-stub")).toBeInTheDocument();
    expect(screen.getByText("study-plan-stub")).toBeInTheDocument();
  });

  it("mounts the learning history section", async () => {
    mockAuth();

    render(<DashboardPage />);

    expect(await screen.findByText("history-section-stub")).toBeInTheDocument();
  });

  it("routes a subject chosen inside the history section", async () => {
    mockAuth();

    render(<DashboardPage />);
    fireEvent.click(await screen.findByText("history-section-stub"));

    expect(pushMock).toHaveBeenCalledWith("/subjects/subject-from-history");
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

describe("DashboardPage subject navigation", () => {
  const subject = makeSubject();

  it("renders the due today section alongside the rest of the dashboard", async () => {
    mockAuth();
    getSubjects.mockResolvedValue([subject]);

    render(<DashboardPage />);

    expect(await screen.findByText("due-today-stub")).toBeInTheDocument();
  });

  it("opens the subject module from the card's own action", async () => {
    mockAuth();
    getSubjects.mockResolvedValue([subject]);

    render(<DashboardPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Open Module/ }));

    expect(pushMock).toHaveBeenCalledWith(`/subjects/${subject.id}`);
  });

  it("opens the module when the card body is clicked, not just the action", async () => {
    mockAuth();
    getSubjects.mockResolvedValue([subject]);

    render(<DashboardPage />);
    fireEvent.click(await screen.findByText(subject.title));

    expect(pushMock).toHaveBeenCalledWith(`/subjects/${subject.id}`);
  });

  it("deletes without navigating away from the dashboard", async () => {
    mockAuth();
    getSubjects.mockResolvedValue([subject]);
    deleteSubject.mockResolvedValue(undefined as any);
    const confirmSpy = vi
      .spyOn(window, "confirm")
      .mockReturnValue(true as unknown as boolean);

    render(<DashboardPage />);
    fireEvent.click(await screen.findByTitle("Delete Subject"));

    await waitFor(() => expect(deleteSubject).toHaveBeenCalledWith(subject.id));
    expect(pushMock).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
