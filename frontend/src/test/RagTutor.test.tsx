import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RagAnswerResponse, RagSource } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: { askRagTutor: vi.fn() },
}));

import { api } from "@/lib/api";
import { RagTutor } from "@/components/rag/RagTutor";

const askRagTutor = vi.mocked(api.askRagTutor);

function source(overrides: Partial<RagSource> = {}): RagSource {
  return {
    chunk_id: "88888888-8888-4888-8888-888888888888",
    document_id: "33333333-3333-4333-8333-333333333333",
    document_title: "lecture-01.pdf",
    chunk_index: 2,
    page_number: 7,
    content: "SN2 reactions proceed in a single concerted step.",
    score: 0.87,
    ...overrides,
  };
}

function answer(overrides: Partial<RagAnswerResponse> = {}): RagAnswerResponse {
  return {
    subject_id: "subject-1",
    question: "How does SN2 differ from SN1?",
    answer: "SN2 is concerted; SN1 is stepwise.",
    sources: [source()],
    model_used: "gemini-2.0-flash",
    ...overrides,
  };
}

beforeEach(() => {
  askRagTutor.mockReset();
});

function composer() {
  return screen.getByPlaceholderText(/Explain how photosynthesis/);
}

describe("RagTutor gating", () => {
  it("waits for study materials before accepting questions", () => {
    render(<RagTutor subjectId="subject-1" hasReadyDocuments={false} />);

    expect(screen.getByText("RAG Tutor is waiting for study materials")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Ask RAG Tutor/ })).not.toBeInTheDocument();
  });

  it("disables the ask button until a question is typed", () => {
    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);

    expect(screen.getByRole("button", { name: /Ask RAG Tutor/ })).toBeDisabled();
  });

  it("keeps the ask button disabled for whitespace-only input", () => {
    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);

    fireEvent.change(composer(), { target: { value: "   " } });

    expect(screen.getByRole("button", { name: /Ask RAG Tutor/ })).toBeDisabled();
  });

  it("enables the ask button once a question is typed", () => {
    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);

    fireEvent.change(composer(), { target: { value: "What is SN2?" } });

    expect(screen.getByRole("button", { name: /Ask RAG Tutor/ })).toBeEnabled();
  });
});

describe("RagTutor asking", () => {
  it("sends the trimmed question for the subject", async () => {
    askRagTutor.mockResolvedValue(answer());

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "  How does SN2 differ from SN1?  " } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));

    await waitFor(() =>
      expect(askRagTutor).toHaveBeenCalledWith("subject-1", "How does SN2 differ from SN1?"),
    );
  });

  it("asks on Ctrl+Enter", async () => {
    askRagTutor.mockResolvedValue(answer());

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "What is SN2?" } });
    fireEvent.keyDown(composer(), { key: "Enter", ctrlKey: true });

    await waitFor(() => expect(askRagTutor).toHaveBeenCalledTimes(1));
  });

  it("does not ask on a bare Enter press", () => {
    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "What is SN2?" } });

    fireEvent.keyDown(composer(), { key: "Enter" });

    expect(askRagTutor).not.toHaveBeenCalled();
  });

  it("shows the retrieval progress message while waiting", async () => {
    askRagTutor.mockReturnValue(new Promise(() => {}));

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "What is SN2?" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));

    expect(
      await screen.findByText("Retrieving relevant chunks and composing a grounded answer..."),
    ).toBeInTheDocument();
  });

  it("surfaces an API error", async () => {
    askRagTutor.mockRejectedValue(new Error("No indexed material for this subject."));

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "What is SN2?" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));

    expect(await screen.findByText("No indexed material for this subject.")).toBeInTheDocument();
  });

  it("falls back to a generic error when the error has no message", async () => {
    askRagTutor.mockRejectedValue({});

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "What is SN2?" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));

    expect(await screen.findByText("Failed to get an answer. Please try again.")).toBeInTheDocument();
  });

  it("drops a previously rendered answer when a new ask fails", async () => {
    askRagTutor.mockResolvedValueOnce(answer());
    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "First question" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));
    expect(await screen.findByText("SN2 is concerted; SN1 is stepwise.")).toBeInTheDocument();

    askRagTutor.mockRejectedValueOnce(new Error("boom"));
    fireEvent.change(composer(), { target: { value: "Second question" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));

    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.queryByText("SN2 is concerted; SN1 is stepwise.")).not.toBeInTheDocument();
  });
});

describe("RagTutor answer rendering", () => {
  it("echoes the question and answer", async () => {
    askRagTutor.mockResolvedValue(answer());

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "How does SN2 differ from SN1?" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));

    expect(await screen.findByText("How does SN2 differ from SN1?")).toBeInTheDocument();
    expect(screen.getByText("SN2 is concerted; SN1 is stepwise.")).toBeInTheDocument();
  });

  it("labels a Gemini answer as Gemini AI", async () => {
    askRagTutor.mockResolvedValue(answer({ model_used: "gemini-2.0-flash" }));

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "q" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));

    expect(await screen.findByText("Gemini AI")).toBeInTheDocument();
  });

  it("labels a deterministic answer as a grounded fallback", async () => {
    askRagTutor.mockResolvedValue(answer({ model_used: "deterministic-fallback" }));

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "q" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));

    expect(await screen.findByText("Grounded Fallback")).toBeInTheDocument();
  });

  it("lists each source with its page and match score", async () => {
    askRagTutor.mockResolvedValue(
      answer({
        sources: [
          source(),
          source({
            chunk_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            document_title: "handout.pdf",
            page_number: 12,
            score: 0.42,
            content: "Carbocations rearrange to gain stability.",
          }),
        ],
      }),
    );

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "q" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));

    expect(await screen.findByText("Source Citations (2)")).toBeInTheDocument();
    expect(screen.getByText("lecture-01.pdf")).toBeInTheDocument();
    expect(screen.getByText("handout.pdf")).toBeInTheDocument();
    expect(screen.getByText("Page 7")).toBeInTheDocument();
    expect(screen.getByText("Page 12")).toBeInTheDocument();
    expect(screen.getByText("87% match")).toBeInTheDocument();
    expect(screen.getByText("42% match")).toBeInTheDocument();
  });

  it("warns when the answer has no sources", async () => {
    askRagTutor.mockResolvedValue(answer({ sources: [] }));

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "q" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));

    expect(await screen.findByText("Source Citations")).toBeInTheDocument();
    expect(screen.getByText(/No source excerpts were returned/)).toBeInTheDocument();
  });

  it("clears the answer on Ask Another Question", async () => {
    askRagTutor.mockResolvedValue(answer());

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "q" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));
    await screen.findByText("SN2 is concerted; SN1 is stepwise.");

    fireEvent.click(screen.getByRole("button", { name: /Ask Another Question/ }));

    expect(screen.queryByText("SN2 is concerted; SN1 is stepwise.")).not.toBeInTheDocument();
  });

  it("keeps the question so it can be edited and re-asked", async () => {
    askRagTutor.mockResolvedValue(answer());

    render(<RagTutor subjectId="subject-1" hasReadyDocuments />);
    fireEvent.change(composer(), { target: { value: "Why is SN2 bimolecular?" } });
    fireEvent.click(screen.getByRole("button", { name: /Ask RAG Tutor/ }));
    await screen.findByText("SN2 is concerted; SN1 is stepwise.");

    fireEvent.click(screen.getByRole("button", { name: /Ask Another Question/ }));

    expect(composer()).toHaveValue("Why is SN2 bimolecular?");
    expect(screen.getByRole("button", { name: /Ask RAG Tutor/ })).toBeEnabled();
  });
});
