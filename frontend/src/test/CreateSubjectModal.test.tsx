import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CreateSubjectModal } from "@/components/dashboard/CreateSubjectModal";

beforeEach(() => {
  vi.restoreAllMocks();
});

function titleInput(): HTMLInputElement {
  return screen.getByPlaceholderText("e.g. Machine Learning Systems");
}

function descriptionInput(): HTMLTextAreaElement {
  return screen.getByPlaceholderText("Short overview of syllabus or topic focus...");
}

function submitButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /Save Subject|Creating/ });
}

describe("CreateSubjectModal visibility", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <CreateSubjectModal isOpen={false} onClose={vi.fn()} onCreate={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("renders the form when open", () => {
    render(<CreateSubjectModal isOpen onClose={vi.fn()} onCreate={vi.fn()} />);

    expect(screen.getByText("Create New Subject")).toBeInTheDocument();
    expect(titleInput()).toBeInTheDocument();
    expect(descriptionInput()).toBeInTheDocument();
  });

  it("closes from the X button", () => {
    const onClose = vi.fn();
    render(<CreateSubjectModal isOpen onClose={onClose} onCreate={vi.fn()} />);

    fireEvent.click(screen.getAllByRole("button")[0]);

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes from Cancel without creating anything", () => {
    const onClose = vi.fn();
    const onCreate = vi.fn();
    render(<CreateSubjectModal isOpen onClose={onClose} onCreate={onCreate} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onCreate).not.toHaveBeenCalled();
  });
});

describe("CreateSubjectModal validation", () => {
  it("does not create a subject without a title", () => {
    const onCreate = vi.fn();
    render(<CreateSubjectModal isOpen onClose={vi.fn()} onCreate={onCreate} />);

    fireEvent.change(titleInput(), { target: { value: "   " } });
    fireEvent.click(submitButton());

    expect(onCreate).not.toHaveBeenCalled();
  });

  it("marks the title as required", () => {
    render(<CreateSubjectModal isOpen onClose={vi.fn()} onCreate={vi.fn()} />);

    expect(titleInput()).toBeRequired();
  });
});

describe("CreateSubjectModal submission", () => {
  it("sends the title, description, and the default colour", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<CreateSubjectModal isOpen onClose={onClose} onCreate={onCreate} />);

    fireEvent.change(titleInput(), { target: { value: "Machine Learning" } });
    fireEvent.change(descriptionInput(), { target: { value: "Supervised learning" } });
    fireEvent.click(submitButton());

    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledWith({
        title: "Machine Learning",
        description: "Supervised learning",
        color: "#0284c7",
      }),
    );
  });

  it("sends the colour of the selected preset", async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(<CreateSubjectModal isOpen onClose={vi.fn()} onCreate={onCreate} />);

    const presets = screen
      .getAllByRole("button")
      .filter((button) => button.getAttribute("style") !== null);
    expect(presets).toHaveLength(6);

    fireEvent.click(presets[2]);
    fireEvent.change(titleInput(), { target: { value: "Biology" } });
    fireEvent.click(submitButton());

    await waitFor(() =>
      expect(onCreate).toHaveBeenCalledWith(
        expect.objectContaining({ color: "#ec4899" }),
      ),
    );
  });

  it("closes the modal after a successful create", async () => {
    const onClose = vi.fn();
    render(
      <CreateSubjectModal
        isOpen
        onClose={onClose}
        onCreate={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    fireEvent.change(titleInput(), { target: { value: "Biology" } });
    fireEvent.click(submitButton());

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it("keeps the modal open and shows the error when creation fails", async () => {
    const onClose = vi.fn();
    render(
      <CreateSubjectModal
        isOpen
        onClose={onClose}
        onCreate={vi.fn().mockRejectedValue(new Error("Subject already exists."))}
      />,
    );

    fireEvent.change(titleInput(), { target: { value: "Biology" } });
    fireEvent.click(submitButton());

    expect(await screen.findByText("Subject already exists.")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("falls back to a generic creation error", async () => {
    render(
      <CreateSubjectModal
        isOpen
        onClose={vi.fn()}
        onCreate={vi.fn().mockRejectedValue({})}
      />,
    );

    fireEvent.change(titleInput(), { target: { value: "Biology" } });
    fireEvent.click(submitButton());

    expect(await screen.findByText("Failed to create subject")).toBeInTheDocument();
  });
});
