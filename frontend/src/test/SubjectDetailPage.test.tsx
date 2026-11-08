import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { makeDocument, makeDocumentStatus, makeSubject } from "./factories";

const paramsMock = vi.fn();
const searchParamsMock = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => paramsMock(),
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => searchParamsMock(),
}));

vi.mock("@/store/useAuthStore", () => ({
  useAuthStore: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    getSubject: vi.fn(),
    getSubjectDocuments: vi.fn(),
    getDocumentStatus: vi.fn(),
    reprocessDocument: vi.fn(),
    deleteDocument: vi.fn(),
    updateSubject: vi.fn(),
  },
}));

vi.mock("@/components/documents/DocumentUploader", () => ({
  DocumentUploader: ({ onUploadSuccess }: { onUploadSuccess: (doc: unknown) => void }) => (
    <button
      type="button"
      onClick={() =>
        onUploadSuccess(
          makeDocument({ id: "doc-new", title: "fresh.pdf", status: "PROCESSING" }),
        )
      }
    >
      uploader-stub
    </button>
  ),
}));

vi.mock("@/components/rag/RagTutor", () => ({ RagTutor: () => <div>rag-stub</div> }));
vi.mock("@/components/quiz/QuizSection", () => ({ QuizSection: () => <div>quiz-stub</div> }));
vi.mock("@/components/flashcards/FlashcardSection", () => ({
  FlashcardSection: () => <div>flashcards-stub</div>,
}));
vi.mock("@/components/analytics/StudentIntelligence", () => ({
  StudentIntelligence: () => <div>intelligence-stub</div>,
}));

import SubjectDetailPage from "@/app/subjects/[id]/page";
import { api } from "@/lib/api";
import { useAuthStore, type AuthState } from "@/store/useAuthStore";

const getSubject = vi.mocked(api.getSubject);
const getSubjectDocuments = vi.mocked(api.getSubjectDocuments);
const getDocumentStatus = vi.mocked(api.getDocumentStatus);
const reprocessDocument = vi.mocked(api.reprocessDocument);
const useAuthStoreMock = vi.mocked(useAuthStore);

const SUBJECT_ID = "11111111-1111-4111-8111-111111111111";

function mockAuth(overrides: Partial<AuthState> = {}) {
  useAuthStoreMock.mockReturnValue({
    user: null,
    isAuthenticated: true,
    isLoading: false,
    error: null,
    initialize: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    clearError: vi.fn(),
    ...overrides,
  } as AuthState);
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  paramsMock.mockReturnValue({ id: SUBJECT_ID });
  searchParamsMock.mockReturnValue(new URLSearchParams());
  getSubject.mockReset();
  getSubjectDocuments.mockReset();
  getDocumentStatus.mockReset();
  reprocessDocument.mockReset();
  getSubject.mockResolvedValue(makeSubject({ id: SUBJECT_ID, title: "Organic Chemistry" }));
  getSubjectDocuments.mockResolvedValue([]);
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function renderReady() {
  mockAuth();
  render(<SubjectDetailPage />);
  await waitFor(() => expect(getSubject).toHaveBeenCalledWith(SUBJECT_ID));
  await waitFor(() => expect(getSubjectDocuments).toHaveBeenCalledWith(SUBJECT_ID));
  // The title also appears in the breadcrumb, so anchor on the uploader.
  await screen.findByText("uploader-stub");
}

describe("SubjectDetailPage auth gating", () => {
  it("settles the spinner when there is no session instead of loading forever", async () => {
    mockAuth({ isAuthenticated: false });
    render(<SubjectDetailPage />);

    await waitFor(() =>
      expect(screen.queryByText(/Loading|Chunking/)).not.toBeInTheDocument(),
    );
    expect(getSubject).not.toHaveBeenCalled();
  });

  it("waits for a session before fetching", async () => {
    mockAuth({ isAuthenticated: false });
    render(<SubjectDetailPage />);

    expect(getSubject).not.toHaveBeenCalled();
  });

  it("loads the subject and its documents once a session exists", async () => {
    await renderReady();

    expect(getSubjectDocuments).toHaveBeenCalledWith(SUBJECT_ID);
  });
});

describe("SubjectDetailPage document status polling", () => {
  it("does not poll for documents that are already settled", async () => {
    getSubjectDocuments.mockResolvedValue([
      makeDocument({ id: "doc-ready", status: "READY", chunk_count: 12 }),
    ]);

    await renderReady();
    await vi.advanceTimersByTimeAsync(10_000);

    expect(getDocumentStatus).not.toHaveBeenCalled();
    expect(screen.getByText("Ready for RAG")).toBeInTheDocument();
  });

  it("starts polling a document that was already processing on load", async () => {
    getSubjectDocuments.mockResolvedValue([
      makeDocument({ id: "doc-1", status: "PROCESSING", chunk_count: 0 }),
    ]);
    getDocumentStatus.mockResolvedValue(
      makeDocumentStatus({ id: "doc-1", status: "READY", chunk_count: 8 }),
    );

    await renderReady();
    expect(screen.getByText("Chunking...")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(2000);

    await waitFor(() => expect(screen.getByText("Ready for RAG")).toBeInTheDocument());
    expect(screen.getByText(/8 Vector Chunks/)).toBeInTheDocument();
  });

  it("keeps polling while the document is still processing, then stops", async () => {
    getSubjectDocuments.mockResolvedValue([
      makeDocument({ id: "doc-1", status: "PROCESSING" }),
    ]);
    getDocumentStatus
      .mockResolvedValueOnce(makeDocumentStatus({ id: "doc-1", status: "PROCESSING" }))
      .mockResolvedValueOnce(makeDocumentStatus({ id: "doc-1", status: "PROCESSING" }))
      .mockResolvedValue(makeDocumentStatus({ id: "doc-1", status: "READY", chunk_count: 4 }));

    await renderReady();
    await vi.advanceTimersByTimeAsync(2000);
    await vi.advanceTimersByTimeAsync(2000);
    expect(getDocumentStatus).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(2000);

    await waitFor(() => expect(screen.getByText("Ready for RAG")).toBeInTheDocument());
    const callsWhenReady = getDocumentStatus.mock.calls.length;

    // Bounded: once the row leaves PROCESSING the loop stops on its own.
    await vi.advanceTimersByTimeAsync(60_000);
    expect(getDocumentStatus).toHaveBeenCalledTimes(callsWhenReady);
  });

  it("only asks the cheap status endpoint while polling", async () => {
    getSubjectDocuments.mockResolvedValue([
      makeDocument({ id: "doc-1", status: "PROCESSING" }),
    ]);
    getDocumentStatus.mockResolvedValue(makeDocumentStatus({ id: "doc-1", status: "READY" }));

    await renderReady();
    const documentCallsBefore = getSubjectDocuments.mock.calls.length;
    await vi.advanceTimersByTimeAsync(2000);

    await waitFor(() => expect(screen.getByText("Ready for RAG")).toBeInTheDocument());
    expect(getSubjectDocuments).toHaveBeenCalledTimes(documentCallsBefore);
  });

  it("keeps the last known row state when a status read fails", async () => {
    getSubjectDocuments.mockResolvedValue([
      makeDocument({ id: "doc-1", status: "PROCESSING" }),
    ]);
    getDocumentStatus.mockRejectedValue(new Error("status endpoint down"));

    await renderReady();
    await vi.advanceTimersByTimeAsync(2000);

    // A transient failure ends that loop rather than hammering the endpoint.
    expect(screen.getByText("Chunking...")).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(getDocumentStatus).toHaveBeenCalledTimes(1);
  });

  it("stops polling when the page unmounts", async () => {
    getSubjectDocuments.mockResolvedValue([
      makeDocument({ id: "doc-1", status: "PROCESSING" }),
    ]);

    mockAuth();
    const { unmount } = render(<SubjectDetailPage />);
    await waitFor(() => expect(getSubjectDocuments).toHaveBeenCalled());
    unmount();

    await vi.advanceTimersByTimeAsync(30_000);
    expect(getDocumentStatus).not.toHaveBeenCalled();
  });

  it("tracks a freshly uploaded document", async () => {
    getDocumentStatus.mockResolvedValue(
      makeDocumentStatus({ id: "doc-new", status: "READY", chunk_count: 5 }),
    );

    await renderReady();
    fireEvent.click(screen.getByText("uploader-stub"));
    expect(await screen.findByText("fresh.pdf")).toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(2000);

    await waitFor(() => expect(screen.getByText("Ready for RAG")).toBeInTheDocument());
  });
});

describe("SubjectDetailPage reprocessing", () => {
  it("retries a failed document and follows it back to ready", async () => {
    getSubjectDocuments.mockResolvedValue([
      makeDocument({ id: "doc-1", status: "FAILED", error_message: "Bad PDF." }),
    ]);
    reprocessDocument.mockResolvedValue(
      makeDocumentStatus({ id: "doc-1", status: "PROCESSING", error_message: null }),
    );
    getDocumentStatus.mockResolvedValue(
      makeDocumentStatus({ id: "doc-1", status: "READY", chunk_count: 6 }),
    );

    await renderReady();
    expect(screen.getByText("Bad PDF.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(reprocessDocument).toHaveBeenCalledWith("doc-1"));
    // The previous failure is cleared as soon as the retry is accepted.
    await waitFor(() => expect(screen.queryByText("Bad PDF.")).not.toBeInTheDocument());

    await vi.advanceTimersByTimeAsync(2000);
    await waitFor(() => expect(screen.getByText("Ready for RAG")).toBeInTheDocument());
  });

  it("explains a rejected retry without losing the document row", async () => {
    getSubjectDocuments.mockResolvedValue([
      makeDocument({ id: "doc-1", status: "FAILED" }),
    ]);
    reprocessDocument.mockRejectedValue(new Error("Document is still processing."));

    await renderReady();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(
      await screen.findByText("Document is still processing."),
    ).toBeInTheDocument();
    expect(screen.getByText("Extraction Failed")).toBeInTheDocument();
  });

  it("disables retry for the row in flight and allows the next one", async () => {
    getSubjectDocuments.mockResolvedValue([
      makeDocument({ id: "doc-1", status: "FAILED" }),
      makeDocument({ id: "doc-2", status: "FAILED" }),
    ]);
    reprocessDocument.mockReturnValue(new Promise(() => {}));

    await renderReady();
    const buttons = screen.getAllByRole("button", { name: "Retry" });
    fireEvent.click(buttons[0]);

    await waitFor(() => expect(screen.getAllByRole("button", { name: "Retry" })[0]).toBeDisabled());
    expect(screen.getAllByRole("button", { name: "Retry" })[1]).toBeEnabled();
  });
});

describe("SubjectDetailPage load errors", () => {
  it("reports a failed subject load as recoverable rather than as not-found", async () => {
    mockAuth();
    getSubject.mockRejectedValue(new Error("Network down."));

    render(<SubjectDetailPage />);

    expect(await screen.findByText("Network down.")).toBeInTheDocument();
    expect(screen.queryByText("Subject not found.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("recovers when the retry succeeds and clears the stale error", async () => {
    mockAuth();
    getSubject.mockRejectedValueOnce(new Error("Network down."));
    render(<SubjectDetailPage />);
    expect(await screen.findByText("Network down.")).toBeInTheDocument();

    getSubject.mockResolvedValue(makeSubject({ id: SUBJECT_ID, title: "Organic Chemistry" }));
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await screen.findByText("uploader-stub");
    expect(screen.queryByText("Network down.")).not.toBeInTheDocument();
  });

  it("keeps the retry available while the retry itself is failing", async () => {
    mockAuth();
    getSubject.mockRejectedValue(new Error("Network down."));
    render(<SubjectDetailPage />);
    await screen.findByText("Network down.");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(getSubject).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says not-found when the subject genuinely does not exist", async () => {
    mockAuth();
    getSubject.mockResolvedValue(null as never);
    getSubjectDocuments.mockResolvedValue([]);

    render(<SubjectDetailPage />);

    expect(await screen.findByText("Subject not found.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });
});
