import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makePlanItem, makeStudyPlan, makeSubject } from "./factories";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: (...args: unknown[]) => push(...args) }),
}));

vi.mock("@/lib/api", () => ({
  api: {
    getLatestStudyPlan: vi.fn(),
    generateStudyPlan: vi.fn(),
    completeStudyPlanItem: vi.fn(),
  },
}));

import { api } from "@/lib/api";
import { StudyPlanSection } from "@/components/study-plan/StudyPlanSection";

const getLatestStudyPlan = vi.mocked(api.getLatestStudyPlan);
const generateStudyPlan = vi.mocked(api.generateStudyPlan);
const completeStudyPlanItem = vi.mocked(api.completeStudyPlanItem);

const chemistry = makeSubject({ id: "subject-1", title: "Organic Chemistry" });
const physics = makeSubject({ id: "subject-2", title: "Quantum Physics" });

beforeEach(() => {
  push.mockReset();
  getLatestStudyPlan.mockReset();
  generateStudyPlan.mockReset();
  completeStudyPlanItem.mockReset();
  getLatestStudyPlan.mockResolvedValue(null);
});

function subjectSelect() {
  return screen.getByRole("combobox") as HTMLSelectElement;
}

describe("StudyPlanSection with no subjects", () => {
  it("asks the user to create a subject first", () => {
    render(<StudyPlanSection subjects={[]} />);

    expect(screen.getByText("Create a subject to build a study plan")).toBeInTheDocument();
    expect(getLatestStudyPlan).not.toHaveBeenCalled();
  });
});

describe("StudyPlanSection subject selection", () => {
  it("defaults to the first subject and loads its latest plan", async () => {
    render(<StudyPlanSection subjects={[chemistry, physics]} />);

    await waitFor(() => expect(getLatestStudyPlan).toHaveBeenCalledWith("subject-1"));
    expect(subjectSelect().value).toBe("subject-1");
  });

  it("reloads the latest plan when another subject is selected", async () => {
    render(<StudyPlanSection subjects={[chemistry, physics]} />);
    await waitFor(() => expect(getLatestStudyPlan).toHaveBeenCalledWith("subject-1"));

    fireEvent.change(subjectSelect(), { target: { value: "subject-2" } });

    await waitFor(() => expect(getLatestStudyPlan).toHaveBeenCalledWith("subject-2"));
  });

  it("shows the empty state when the subject has no plan yet", async () => {
    render(<StudyPlanSection subjects={[chemistry]} />);

    expect(await screen.findByText("No study plan yet")).toBeInTheDocument();
  });

  it("surfaces a load error", async () => {
    getLatestStudyPlan.mockRejectedValue(new Error("Subject not found."));

    render(<StudyPlanSection subjects={[chemistry]} />);

    expect(await screen.findByText("Subject not found.")).toBeInTheDocument();
  });

  it("falls back to a generic load error when the error has no message", async () => {
    getLatestStudyPlan.mockRejectedValue({});

    render(<StudyPlanSection subjects={[chemistry]} />);

    expect(await screen.findByText("Failed to load the latest study plan.")).toBeInTheDocument();
  });
});

describe("StudyPlanSection plan rendering", () => {
  it("renders the plan header, session, and item metrics", async () => {
    getLatestStudyPlan.mockResolvedValue(makeStudyPlan());

    render(<StudyPlanSection subjects={[chemistry]} />);

    // Once in the subject <select> and once in the plan header.
    expect(await screen.findAllByText("Organic Chemistry")).toHaveLength(2);
    expect(screen.getByText(/^Generated /)).toBeInTheDocument();
    expect(screen.getByText(/45 min planned/)).toBeInTheDocument();
    expect(screen.getByText("Session 1")).toBeInTheDocument();
    expect(screen.getByText(/1 topic/)).toBeInTheDocument();
    expect(screen.getByText("Nucleophilic substitution")).toBeInTheDocument();
    expect(screen.getByText("Mastery 31% • 10 questions • 6 missed")).toBeInTheDocument();
    expect(screen.getByText("PRACTICE")).toBeInTheDocument();
    expect(screen.getByText("Priority 82")).toBeInTheDocument();
    expect(screen.getByText("30 min estimated")).toBeInTheDocument();
  });

  it("warns when the subject has no indexed material", async () => {
    getLatestStudyPlan.mockResolvedValue(makeStudyPlan({ material_available: false }));

    render(<StudyPlanSection subjects={[chemistry]} />);

    expect(
      await screen.findByText(/Indexed study material is unavailable for this subject/),
    ).toBeInTheDocument();
  });

  it("reports how many topics were omitted to fit the session limits", async () => {
    getLatestStudyPlan.mockResolvedValue(makeStudyPlan({ omitted_topic_count: 3 }));

    render(<StudyPlanSection subjects={[chemistry]} />);

    expect(await screen.findByText(/3 lower-priority topics omitted/)).toBeInTheDocument();
  });

  it("uses the singular form for a single omitted topic", async () => {
    getLatestStudyPlan.mockResolvedValue(makeStudyPlan({ omitted_topic_count: 1 }));

    render(<StudyPlanSection subjects={[chemistry]} />);

    expect(await screen.findByText(/1 lower-priority topic omitted/)).toBeInTheDocument();
  });

  it("asks for more quiz data when the plan has no sessions", async () => {
    getLatestStudyPlan.mockResolvedValue(makeStudyPlan({ sessions: [] }));

    render(<StudyPlanSection subjects={[chemistry]} />);

    expect(await screen.findByText("More quiz data is needed")).toBeInTheDocument();
  });

  it("falls back to a priority label when no target date is set", async () => {
    getLatestStudyPlan.mockResolvedValue(makeStudyPlan({ target_date: null }));

    render(<StudyPlanSection subjects={[chemistry]} />);

    expect(await screen.findByText("Priority sequence")).toBeInTheDocument();
  });
});

describe("StudyPlanSection generation", () => {
  it("sends only the subject when the optional fields are blank", async () => {
    generateStudyPlan.mockResolvedValue(makeStudyPlan());

    render(<StudyPlanSection subjects={[chemistry]} />);
    fireEvent.click(await screen.findByRole("button", { name: /Generate Study Plan/ }));

    await waitFor(() =>
      expect(generateStudyPlan).toHaveBeenCalledWith({
        subject_id: "subject-1",
        target_date: null,
        available_minutes_per_day: null,
        requested_sessions: null,
        max_topics_per_session: null,
      }),
    );
  });

  it("forwards the target date and numeric options", async () => {
    generateStudyPlan.mockResolvedValue(makeStudyPlan());

    render(<StudyPlanSection subjects={[chemistry]} />);
    await screen.findByRole("button", { name: /Generate Study Plan/ });

    fireEvent.change(screen.getByLabelText(/Target date/), { target: { value: "2026-01-17" } });
    fireEvent.change(screen.getByLabelText(/Minutes per day/), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText(/Study sessions/), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText(/Topics per session/), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /Generate Study Plan/ }));

    await waitFor(() =>
      expect(generateStudyPlan).toHaveBeenCalledWith({
        subject_id: "subject-1",
        target_date: "2026-01-17",
        available_minutes_per_day: 30,
        requested_sessions: 4,
        max_topics_per_session: 2,
      }),
    );
  });

  it("renders the freshly generated plan", async () => {
    generateStudyPlan.mockResolvedValue(
      makeStudyPlan({
        sessions: [
          {
            id: "session-7",
            session_number: 3,
            scheduled_date: "2026-01-09",
            estimated_minutes: 60,
            items: [makePlanItem({ topic: "Carbocation stability" })],
          },
        ],
      }),
    );

    render(<StudyPlanSection subjects={[chemistry]} />);
    fireEvent.click(await screen.findByRole("button", { name: /Generate Study Plan/ }));

    expect(await screen.findByText("Session 3")).toBeInTheDocument();
    expect(screen.getByText("Carbocation stability")).toBeInTheDocument();
  });

  it("surfaces a generation error", async () => {
    generateStudyPlan.mockRejectedValue(new Error("Not enough measured topics."));

    render(<StudyPlanSection subjects={[chemistry]} />);
    fireEvent.click(await screen.findByRole("button", { name: /Generate Study Plan/ }));

    expect(await screen.findByText("Not enough measured topics.")).toBeInTheDocument();
  });

  it("falls back to a generic generation error when the error has no message", async () => {
    generateStudyPlan.mockRejectedValue({});

    render(<StudyPlanSection subjects={[chemistry]} />);
    fireEvent.click(await screen.findByRole("button", { name: /Generate Study Plan/ }));

    expect(await screen.findByText("Failed to generate a study plan.")).toBeInTheDocument();
  });
});

describe("StudyPlanSection completion", () => {
  it("marks an incomplete item complete and swaps the label", async () => {
    const plan = makeStudyPlan();
    const completed = makeStudyPlan({
      sessions: [
        {
          ...plan.sessions[0],
          items: [{ ...plan.sessions[0].items[0], completed: true, completed_at: "2026-01-05T00:00:00Z" }],
        },
      ],
    });
    getLatestStudyPlan.mockResolvedValue(plan);
    completeStudyPlanItem.mockResolvedValue(completed);

    render(<StudyPlanSection subjects={[chemistry]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark complete" }));

    expect(completeStudyPlanItem).toHaveBeenCalledWith(plan.sessions[0].items[0].id, true);
    expect(await screen.findByRole("button", { name: "Completed" })).toBeInTheDocument();
  });

  it("reopens a completed item by sending completed=false", async () => {
    const plan = makeStudyPlan({
      sessions: [
        {
          ...makeStudyPlan().sessions[0],
          items: [makePlanItem({ completed: true, completed_at: "2026-01-05T00:00:00Z" })],
        },
      ],
    });
    getLatestStudyPlan.mockResolvedValue(plan);
    completeStudyPlanItem.mockResolvedValue(makeStudyPlan());

    render(<StudyPlanSection subjects={[chemistry]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Completed" }));

    expect(completeStudyPlanItem).toHaveBeenCalledWith(plan.sessions[0].items[0].id, false);
  });

  it("surfaces a completion error", async () => {
    getLatestStudyPlan.mockResolvedValue(makeStudyPlan());
    completeStudyPlanItem.mockRejectedValue(new Error("Study plan item not found."));

    render(<StudyPlanSection subjects={[chemistry]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark complete" }));

    expect(await screen.findByText("Study plan item not found.")).toBeInTheDocument();
  });

  it("falls back to a generic completion error when the error has no message", async () => {
    getLatestStudyPlan.mockResolvedValue(makeStudyPlan());
    completeStudyPlanItem.mockRejectedValue({});

    render(<StudyPlanSection subjects={[chemistry]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Mark complete" }));

    expect(await screen.findByText("Failed to update study-plan progress.")).toBeInTheDocument();
  });
});

describe("StudyPlanSection deep links", () => {
  it("links LEARN items to the materials view", async () => {
    getLatestStudyPlan.mockResolvedValue(
      makeStudyPlan({
        sessions: [
          {
            ...makeStudyPlan().sessions[0],
            items: [makePlanItem({ action: "LEARN", topic: "SN1 mechanism" })],
          },
        ],
      }),
    );

    render(<StudyPlanSection subjects={[chemistry]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Materials" }));

    expect(push).toHaveBeenCalledWith("/subjects/subject-1?topic=SN1%20mechanism&focus=materials");
  });

  it("links non-LEARN items to practice without the materials focus", async () => {
    getLatestStudyPlan.mockResolvedValue(
      makeStudyPlan({
        sessions: [
          {
            ...makeStudyPlan().sessions[0],
            items: [makePlanItem({ action: "REVIEW", topic: "Leaving groups" })],
          },
        ],
      }),
    );

    render(<StudyPlanSection subjects={[chemistry]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Practice" }));

    expect(push).toHaveBeenCalledWith("/subjects/subject-1?topic=Leaving%20groups");
  });
});

describe("StudyPlanSection subject removal", () => {
  it("resets the plan when the selected subject disappears", async () => {
    getLatestStudyPlan.mockImplementation(async (subjectId?: string) =>
      subjectId === "subject-1" ? makeStudyPlan() : null,
    );

    const { rerender } = render(<StudyPlanSection subjects={[chemistry, physics]} />);
    expect(await screen.findByText("Nucleophilic substitution")).toBeInTheDocument();

    rerender(<StudyPlanSection subjects={[physics]} />);

    await waitFor(() => expect(getLatestStudyPlan).toHaveBeenLastCalledWith("subject-2"));
    expect(await screen.findByText("No study plan yet")).toBeInTheDocument();
  });
});

describe("StudyPlanSection accessibility", () => {
  it("labels every configuration input", async () => {
    getLatestStudyPlan.mockResolvedValue(null);

    render(<StudyPlanSection subjects={[chemistry]} />);

    expect(within(screen.getByRole("combobox")).getByRole("option", { name: "Organic Chemistry" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Target date/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Minutes per day/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Study sessions/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Topics per session/)).toBeInTheDocument();
  });
});
