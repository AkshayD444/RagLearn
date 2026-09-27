import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SubjectCard } from "@/components/dashboard/SubjectCard";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { Spinner } from "@/components/shared/Spinner";
import { makeSubject } from "./factories";

const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

describe("Spinner", () => {
  it("is hidden from assistive technology and uses the default size", () => {
    const { container } = render(<Spinner />);

    const spinner = container.firstElementChild as HTMLElement;
    expect(spinner).toHaveAttribute("aria-hidden", "true");
    expect(spinner).toHaveClass("h-4", "w-4", "animate-spin");
  });

  it("accepts a custom size", () => {
    const { container } = render(<Spinner className="h-5 w-5" />);

    expect(container.firstElementChild).toHaveClass("h-5", "w-5");
    expect(container.firstElementChild).not.toHaveClass("h-4");
  });
});

describe("ErrorBanner", () => {
  it("renders the message", () => {
    render(<ErrorBanner message="Subject not found." />);

    expect(screen.getByText("Subject not found.")).toBeInTheDocument();
  });

  it("renders a long message without truncating it", () => {
    const message = "The upload was rejected because the file exceeds the 20 MB limit.";
    render(<ErrorBanner message={message} />);

    expect(screen.getByText(message)).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("renders the icon, title, and description", () => {
    render(
      <EmptyState icon={<span data-testid="icon" />} title="No documents" description="Upload a PDF." />,
    );

    expect(screen.getByTestId("icon")).toBeInTheDocument();
    expect(screen.getByText("No documents")).toBeInTheDocument();
    expect(screen.getByText("Upload a PDF.")).toBeInTheDocument();
  });
});

describe("SubjectCard", () => {
  it("renders the subject title and description", () => {
    render(
      <SubjectCard
        subject={makeSubject({ title: "Organic Chemistry", description: "Reaction mechanisms" })}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText("Organic Chemistry")).toBeInTheDocument();
    expect(screen.getByText("Reaction mechanisms")).toBeInTheDocument();
  });

  it("falls back to placeholder copy when there is no description", () => {
    render(<SubjectCard subject={makeSubject({ description: "" })} onDelete={vi.fn()} />);

    expect(screen.getByText("No description provided.")).toBeInTheDocument();
  });

  it("requests deletion with the subject id", () => {
    const onDelete = vi.fn();
    render(
      <SubjectCard subject={makeSubject({ id: "subject-1" })} onDelete={onDelete} />,
    );

    fireEvent.click(screen.getByTitle("Delete Subject"));

    expect(onDelete).toHaveBeenCalledWith("subject-1");
  });

  it("does not select the subject when the delete button is clicked", () => {
    const onSelect = vi.fn();
    const onDelete = vi.fn();
    render(
      <SubjectCard subject={makeSubject()} onDelete={onDelete} onSelect={onSelect} />,
    );

    fireEvent.click(screen.getByTitle("Delete Subject"));

    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("passes the whole subject to onSelect", () => {
    const onSelect = vi.fn();
    const subject = makeSubject({ id: "subject-1" });
    render(<SubjectCard subject={subject} onDelete={vi.fn()} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: /Open Module/ }));

    expect(onSelect).toHaveBeenCalledWith(subject);
  });

  it("navigates to the subject module when no onSelect handler is given", () => {
    const subject = makeSubject({ id: "subject-1" });
    render(<SubjectCard subject={subject} onDelete={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /Open Module/ }));

    // The action is never dead: without an override it still goes somewhere.
    expect(pushMock).toHaveBeenCalledWith("/subjects/subject-1");
  });

  it("prefers onSelect over the router when both are available", () => {
    const onSelect = vi.fn();
    render(<SubjectCard subject={makeSubject()} onDelete={vi.fn()} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: /Open Module/ }));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("applies the subject colour to the accent bar", () => {
    const { container } = render(
      <SubjectCard subject={makeSubject({ color: "#ff0000" })} onDelete={vi.fn()} />,
    );

    const accent = container.querySelector("div[style]") as HTMLElement;
    expect(accent.style.backgroundColor).toBe("rgb(255, 0, 0)");
  });
});
