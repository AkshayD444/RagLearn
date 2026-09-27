import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DocumentList } from "@/components/documents/DocumentList";
import { makeDocument } from "./factories";

describe("DocumentList", () => {
  it("shows the upload prompt when there are no documents", () => {
    render(<DocumentList documents={[]} onDelete={vi.fn()} />);

    expect(screen.getByText("No Documents Uploaded")).toBeInTheDocument();
    expect(screen.queryByTitle("Delete Document")).not.toBeInTheDocument();
  });

  it("renders one row per document with its size and chunk count", () => {
    render(
      <DocumentList
        documents={[
          makeDocument({ title: "lecture-01.pdf", file_size_bytes: 2048, chunk_count: 12 }),
          makeDocument({
            id: "22222222-2222-4222-8222-222222222222",
            title: "handout.pdf",
            file_size_bytes: 1_048_576,
            chunk_count: 3,
            status: "PROCESSING",
          }),
        ]}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText("lecture-01.pdf")).toBeInTheDocument();
    expect(screen.getByText("handout.pdf")).toBeInTheDocument();
    expect(screen.getByText("2 KB")).toBeInTheDocument();
    expect(screen.getByText("1 MB")).toBeInTheDocument();
    expect(screen.getByText(/12 Vector Chunks/)).toBeInTheDocument();
    expect(screen.getByText(/3 Vector Chunks/)).toBeInTheDocument();
  });

  it("labels a zero-byte document as 0 Bytes", () => {
    render(
      <DocumentList
        documents={[makeDocument({ file_size_bytes: 0 })]}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText("0 Bytes")).toBeInTheDocument();
  });

  it("shows the READY badge for indexed documents", () => {
    render(
      <DocumentList documents={[makeDocument({ status: "READY" })]} onDelete={vi.fn()} />,
    );

    expect(screen.getByText("Ready for RAG")).toBeInTheDocument();
    expect(screen.queryByText("Chunking...")).not.toBeInTheDocument();
    expect(screen.queryByText("Extraction Failed")).not.toBeInTheDocument();
  });

  it("shows the PROCESSING badge while a document is being chunked", () => {
    render(
      <DocumentList
        documents={[makeDocument({ status: "PROCESSING" })]}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText("Chunking...")).toBeInTheDocument();
    expect(screen.queryByText("Ready for RAG")).not.toBeInTheDocument();
  });

  it("shows the FAILED badge and surfaces error_message as the tooltip", () => {
    render(
      <DocumentList
        documents={[
          makeDocument({
            status: "FAILED",
            error_message: "No extractable text found in the PDF.",
          }),
        ]}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText("Extraction Failed")).toBeInTheDocument();
    expect(screen.getByTitle("No extractable text found in the PDF.")).toBeInTheDocument();
  });

  it("falls back to a generic tooltip when a FAILED document has no error_message", () => {
    render(
      <DocumentList
        documents={[makeDocument({ status: "FAILED", error_message: null })]}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByTitle("Extraction failed")).toBeInTheDocument();
  });

  it("passes the document id to onDelete when its delete button is clicked", () => {
    const onDelete = vi.fn();
    render(
      <DocumentList
        documents={[
          makeDocument({ id: "first-doc" }),
          makeDocument({ id: "second-doc", title: "other.pdf" }),
        ]}
        onDelete={onDelete}
      />,
    );

    const buttons = screen.getAllByTitle("Delete Document");
    expect(buttons).toHaveLength(2);

    fireEvent.click(buttons[1]);

    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(onDelete).toHaveBeenCalledWith("second-doc");
  });
});

describe("DocumentList reprocessing", () => {
  it("offers a retry for a failed document and reports the row id", () => {
    const onReprocess = vi.fn();

    render(
      <DocumentList
        documents={[makeDocument({ id: "doc-1", title: "broken.pdf", status: "FAILED" })]}
        onDelete={vi.fn()}
        onReprocess={onReprocess}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onReprocess).toHaveBeenCalledWith("doc-1");
  });

  it("surfaces the failure reason that processing recorded", () => {
    render(
      <DocumentList
        documents={[
          makeDocument({ status: "FAILED", error_message: "Unsupported file type." }),
        ]}
        onDelete={vi.fn()}
        onReprocess={vi.fn()}
      />,
    );

    expect(screen.getByText("Unsupported file type.")).toBeInTheDocument();
  });

  it("disables only the row being retried", () => {
    const onReprocess = vi.fn();

    render(
      <DocumentList
        documents={[
          makeDocument({ id: "doc-1", status: "FAILED" }),
          makeDocument({ id: "doc-2", status: "FAILED" }),
        ]}
        onDelete={vi.fn()}
        onReprocess={onReprocess}
        reprocessingId="doc-1"
      />,
    );

    const buttons = screen.getAllByRole("button", { name: "Retry" });
    expect(buttons[0]).toBeDisabled();
    expect(buttons[1]).toBeEnabled();
  });

  it("stays read-only when no retry handler is wired up", () => {
    render(
      <DocumentList
        documents={[makeDocument({ status: "FAILED" })]}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText("Extraction Failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("does not offer a retry for a document that is still processing", () => {
    render(
      <DocumentList
        documents={[makeDocument({ status: "PROCESSING" })]}
        onDelete={vi.fn()}
        onReprocess={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });
});
