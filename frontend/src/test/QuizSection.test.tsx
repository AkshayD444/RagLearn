import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeQuiz, makeQuizResult } from "./factories";

vi.mock("@/lib/api", () => ({
  api: {
    generateQuiz: vi.fn(),
    submitQuiz: vi.fn(),
  },
}));

import { api } from "@/lib/api";
import { QuizSection } from "@/components/quiz/QuizSection";

const generateQuiz = vi.mocked(api.generateQuiz);
const submitQuiz = vi.mocked(api.submitQuiz);

beforeEach(() => {
  generateQuiz.mockReset();
  submitQuiz.mockReset();
});

function optionButton(text: string) {
  return screen.getByRole("button", { name: new RegExp(text) });
}

describe("QuizSection gating", () => {
  it("blocks quiz generation until a document is indexed", () => {
    render(<QuizSection subjectId="subject-1" hasReadyDocuments={false} />);

    expect(screen.getByText("Quiz Engine needs indexed materials")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Generate Quiz/ })).not.toBeInTheDocument();
    expect(generateQuiz).not.toHaveBeenCalled();
  });

  it("prefills the topic from the initialTopic prop", () => {
    render(
      <QuizSection subjectId="subject-1" hasReadyDocuments initialTopic="Nucleophilic substitution" />,
    );

    expect(screen.getByPlaceholderText("e.g. Photosynthesis")).toHaveValue(
      "Nucleophilic substitution",
    );
  });

  it("defaults to 5 medium questions with no topic", () => {
    render(<QuizSection subjectId="subject-1" hasReadyDocuments />);

    expect(screen.getByRole("button", { name: "5" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Medium" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByPlaceholderText("e.g. Photosynthesis")).toHaveValue("");
  });
});

describe("QuizSection generation", () => {
  it("sends the selected count, difficulty, and topic", async () => {
    generateQuiz.mockResolvedValue(makeQuiz());

    render(<QuizSection subjectId="subject-1" hasReadyDocuments />);
    fireEvent.click(screen.getByRole("button", { name: "10" }));
    fireEvent.click(screen.getByRole("button", { name: "Hard" }));
    fireEvent.change(screen.getByPlaceholderText("e.g. Photosynthesis"), {
      target: { value: "  SN2 reactions  " },
    });
    fireEvent.click(screen.getByRole("button", { name: /Generate Quiz/ }));

    await waitFor(() =>
      expect(generateQuiz).toHaveBeenCalledWith("subject-1", 10, "hard", "SN2 reactions"),
    );
  });

  it("sends a null topic when the field is left blank", async () => {
    generateQuiz.mockResolvedValue(makeQuiz());

    render(<QuizSection subjectId="subject-1" hasReadyDocuments />);
    fireEvent.click(screen.getByRole("button", { name: /Generate Quiz/ }));

    await waitFor(() => expect(generateQuiz).toHaveBeenCalledWith("subject-1", 5, "medium", null));
  });

  it("moves into the quiz-taking view", async () => {
    generateQuiz.mockResolvedValue(makeQuiz());

    render(<QuizSection subjectId="subject-1" hasReadyDocuments />);
    fireEvent.click(screen.getByRole("button", { name: /Generate Quiz/ }));

    expect(await screen.findByText("Question 1 of 2 • Answers are revealed after submission")).toBeInTheDocument();
    expect(screen.getByText("Which reagent drives an SN2 reaction?")).toBeInTheDocument();
  });

  it("surfaces a generation error", async () => {
    generateQuiz.mockRejectedValue(new Error("No indexed material for this subject."));

    render(<QuizSection subjectId="subject-1" hasReadyDocuments />);
    fireEvent.click(screen.getByRole("button", { name: /Generate Quiz/ }));

    expect(await screen.findByText("No indexed material for this subject.")).toBeInTheDocument();
  });

  it("falls back to a generic generation error when the error has no message", async () => {
    generateQuiz.mockRejectedValue({});

    render(<QuizSection subjectId="subject-1" hasReadyDocuments />);
    fireEvent.click(screen.getByRole("button", { name: /Generate Quiz/ }));

    expect(await screen.findByText("Failed to generate quiz. Please try again.")).toBeInTheDocument();
  });
});

describe("QuizSection taking a quiz", () => {
  async function startQuiz() {
    generateQuiz.mockResolvedValue(makeQuiz());
    render(<QuizSection subjectId="subject-1" hasReadyDocuments />);
    fireEvent.click(screen.getByRole("button", { name: /Generate Quiz/ }));
    await screen.findByText("Question 1 of 2 • Answers are revealed after submission");
  }

  it("keeps Next disabled until the current question is answered", async () => {
    await startQuiz();

    expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();

    fireEvent.click(optionButton("Nucleophile"));

    expect(screen.getByRole("button", { name: /Next/ })).toBeEnabled();
  });

  it("keeps Previous disabled on the first question", async () => {
    await startQuiz();

    expect(screen.getByRole("button", { name: /Previous/ })).toBeDisabled();
  });

  it("tracks the answered count in the progress badge", async () => {
    await startQuiz();

    expect(screen.getByText(/0\/2 answered/)).toBeInTheDocument();

    fireEvent.click(optionButton("Nucleophile"));
    expect(screen.getByText(/1\/2 answered/)).toBeInTheDocument();
  });

  it("lets the user change a selection before moving on", async () => {
    await startQuiz();

    const nucleophile = optionButton("Nucleophile");
    fireEvent.click(nucleophile);
    expect(nucleophile).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(optionButton("Electrophile"));
    expect(optionButton("Electrophile")).toHaveAttribute("aria-pressed", "true");
    expect(nucleophile).toHaveAttribute("aria-pressed", "false");
  });

  it("returns to the previous question and keeps its answer", async () => {
    await startQuiz();

    fireEvent.click(optionButton("Nucleophile"));
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));

    expect(await screen.findByText("Question 2 of 2 • Answers are revealed after submission")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Previous/ }));
    expect(await screen.findByText("Question 1 of 2 • Answers are revealed after submission")).toBeInTheDocument();
    expect(optionButton("Nucleophile")).toHaveAttribute("aria-pressed", "true");
  });

  it("shows Submit on the last question and keeps it disabled until all are answered", async () => {
    await startQuiz();

    fireEvent.click(optionButton("Nucleophile"));
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));

    const submit = await screen.findByRole("button", { name: /Submit Quiz/ });
    expect(submit).toBeDisabled();

    fireEvent.click(optionButton("Chelating agent"));
    expect(submit).toBeEnabled();
  });

  it("abandons the quiz back to the configuration view on Cancel", async () => {
    await startQuiz();

    fireEvent.click(optionButton("Nucleophile"));
    fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));

    expect(await screen.findByText("Number of Questions")).toBeInTheDocument();
    expect(generateQuiz).toHaveBeenCalledTimes(1);
  });
});

describe("QuizSection submission", () => {
  const onAttemptComplete = vi.fn();

  beforeEach(() => {
    onAttemptComplete.mockReset();
  });

  async function answerAll() {
    const quiz = makeQuiz();
    generateQuiz.mockResolvedValue(quiz);
    render(
      <QuizSection subjectId="subject-1" hasReadyDocuments onAttemptComplete={onAttemptComplete} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Generate Quiz/ }));
    await screen.findByText("Question 1 of 2 • Answers are revealed after submission");

    fireEvent.click(optionButton("Nucleophile"));
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    // Both questions share the same option texts, so wait for the transition
    // before picking the second answer.
    expect(
      await screen.findByText("Question 2 of 2 • Answers are revealed after submission"),
    ).toBeInTheDocument();
    fireEvent.click(optionButton("Chelating agent"));
    fireEvent.click(screen.getByRole("button", { name: /Submit Quiz/ }));
    return quiz;
  }

  it("submits every answer with the recorded per-question time", async () => {
    const quiz = await answerAll();

    await waitFor(() => expect(submitQuiz).toHaveBeenCalledTimes(1));
    const [quizId, answers, options] = submitQuiz.mock.calls[0];
    expect(quizId).toBe(quiz.id);
    expect(answers).toHaveLength(2);
    expect(answers.map((a) => a.selected_option)).toEqual(["A", "C"]);
    expect(answers[0].question_id).toBe(quiz.questions[0].id);
    expect(typeof answers[0].time_taken).toBe("number");
    expect(options?.startedAt).toEqual(expect.any(String));
  });

  it("shows the score summary and per-question review", async () => {
    submitQuiz.mockResolvedValue(makeQuizResult(makeQuiz()));
    await answerAll();

    expect(await screen.findByText("50%")).toBeInTheDocument();
    expect(screen.getByText(/You scored/)).toHaveTextContent(
      "You scored 1 out of 2 questions correct.",
    );
    expect(screen.getByText("Review & Explanations")).toBeInTheDocument();
    expect(screen.getByText(/Attempt saved/)).toBeInTheDocument();
    expect(screen.getByText("1 Correct")).toBeInTheDocument();
    expect(screen.getByText("1 Incorrect")).toBeInTheDocument();
    expect(screen.getByText("Your answer: A")).toBeInTheDocument();
    expect(screen.getByText("Correct: A")).toBeInTheDocument();
    expect(screen.getByText("A nucleophile attacks the electrophile.")).toBeInTheDocument();
    expect(screen.getByText("lecture-01.pdf")).toBeInTheDocument();
    expect(screen.getByText("• Page 4")).toBeInTheDocument();
    // The live result renders through the shared review card, which only lists
    // the option texts when an attempt review can recover them.
    expect(screen.queryByText("Nucleophile")).not.toBeInTheDocument();
    expect(screen.queryByText("Electrophile")).not.toBeInTheDocument();
  });

  it("rounds the displayed percentage", async () => {
    submitQuiz.mockResolvedValue(makeQuizResult(makeQuiz(), { percentage: 66.6666 }));
    await answerAll();

    expect(await screen.findByText("67%")).toBeInTheDocument();
  });

  it("notifies the parent so analytics can refresh", async () => {
    submitQuiz.mockResolvedValue(makeQuizResult(makeQuiz()));
    await answerAll();

    await waitFor(() => expect(onAttemptComplete).toHaveBeenCalledTimes(1));
  });

  it("omits the saved-attempt note when the backend did not persist an attempt", async () => {
    submitQuiz.mockResolvedValue(makeQuizResult(makeQuiz(), { attempt_id: null }));
    await answerAll();

    await screen.findByText("50%");
    expect(screen.queryByText(/Attempt saved/)).not.toBeInTheDocument();
  });

  it("surfaces a submission error", async () => {
    submitQuiz.mockRejectedValue(new Error("Quiz not found."));
    await answerAll();

    expect(await screen.findByText("Quiz not found.")).toBeInTheDocument();
  });

  it("falls back to a generic submission error when the error has no message", async () => {
    submitQuiz.mockRejectedValue({});
    await answerAll();

    expect(await screen.findByText("Failed to submit answers. Please try again.")).toBeInTheDocument();
  });

  it("returns to the configuration view after Take Another Quiz", async () => {
    submitQuiz.mockResolvedValue(makeQuizResult(makeQuiz()));
    await answerAll();

    fireEvent.click(await screen.findByRole("button", { name: /Take Another Quiz/ }));

    expect(await screen.findByText("Number of Questions")).toBeInTheDocument();
  });
});
