import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeDocument } from "./factories";

vi.mock("@/lib/api", () => ({
  api: { uploadDocument: vi.fn() },
}));

import { api } from "@/lib/api";
import { DocumentUploader } from "@/components/documents/DocumentUploader";

const uploadDocument = vi.mocked(api.uploadDocument);

beforeEach(() => {
  uploadDocument.mockReset();
});

function pdfFile(name = "lecture.pdf", size = 1024): File {
  const file = new File(["%PDF-1.4"], name, { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

function fileInput(): HTMLInputElement {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

describe("DocumentUploader", () => {
  it("only offers to accept PDFs", () => {
    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);

    expect(fileInput()).toHaveAttribute("accept", ".pdf");
  });

  it("rejects a non-PDF file before calling the API", async () => {
    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(fileInput(), { target: { files: [file] } });

    expect(await screen.findByText("Only PDF (.pdf) documents are supported.")).toBeInTheDocument();
    expect(uploadDocument).not.toHaveBeenCalled();
  });

  it("rejects an uppercase .PDF-less name", async () => {
    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);

    const file = new File(["hello"], "notes.pages", { type: "application/pdf" });
    fireEvent.change(fileInput(), { target: { files: [file] } });

    expect(await screen.findByText("Only PDF (.pdf) documents are supported.")).toBeInTheDocument();
    expect(uploadDocument).not.toHaveBeenCalled();
  });

  it("accepts an uppercase .PDF name", async () => {
    uploadDocument.mockResolvedValue(makeDocument());

    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);
    fireEvent.change(fileInput(), { target: { files: [pdfFile("LECTURE.PDF")] } });

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(1));
  });

  it("rejects a file larger than 20 MB", async () => {
    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);

    fireEvent.change(fileInput(), {
      target: { files: [pdfFile("big.pdf", 20 * 1024 * 1024 + 1)] },
    });

    expect(await screen.findByText("File size exceeds maximum 20MB limit.")).toBeInTheDocument();
    expect(uploadDocument).not.toHaveBeenCalled();
  });

  it("accepts a file of exactly 20 MB", async () => {
    uploadDocument.mockResolvedValue(makeDocument());

    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);
    fireEvent.change(fileInput(), {
      target: { files: [pdfFile("limit.pdf", 20 * 1024 * 1024)] },
    });

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(1));
  });

  it("uploads the file for the given subject and reports success", async () => {
    const doc = makeDocument({ id: "doc-1", title: "lecture.pdf" });
    uploadDocument.mockResolvedValue(doc);
    const onUploadSuccess = vi.fn();

    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={onUploadSuccess} />);
    const file = pdfFile();
    fireEvent.change(fileInput(), { target: { files: [file] } });

    await waitFor(() => expect(onUploadSuccess).toHaveBeenCalledWith(doc));
    expect(uploadDocument).toHaveBeenCalledWith("subject-1", file);
  });

  it("shows the uploading copy while the request is in flight", async () => {
    uploadDocument.mockReturnValue(new Promise(() => {}));

    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);
    fireEvent.change(fileInput(), { target: { files: [pdfFile()] } });

    expect(await screen.findByText("Uploading & Extracting PDF Chunks...")).toBeInTheDocument();
  });

  it("surfaces an upload rejection message", async () => {
    uploadDocument.mockRejectedValue(new Error("File extension is not supported."));

    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);
    fireEvent.change(fileInput(), { target: { files: [pdfFile()] } });

    expect(await screen.findByText("File extension is not supported.")).toBeInTheDocument();
  });

  it("falls back to a generic upload error", async () => {
    uploadDocument.mockRejectedValue({});

    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);
    fireEvent.change(fileInput(), { target: { files: [pdfFile()] } });

    expect(await screen.findByText("Failed to upload document.")).toBeInTheDocument();
  });

  it("does not report success when the upload fails", async () => {
    uploadDocument.mockRejectedValue(new Error("boom"));
    const onUploadSuccess = vi.fn();

    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={onUploadSuccess} />);
    fireEvent.change(fileInput(), { target: { files: [pdfFile()] } });

    await screen.findByText("boom");
    expect(onUploadSuccess).not.toHaveBeenCalled();
  });

  it("clears the previous error when the next upload starts", async () => {
    uploadDocument.mockRejectedValueOnce(new Error("boom"));
    uploadDocument.mockResolvedValueOnce(makeDocument());

    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);
    fireEvent.change(fileInput(), { target: { files: [pdfFile()] } });
    await screen.findByText("boom");

    fireEvent.change(fileInput(), { target: { files: [pdfFile("second.pdf")] } });

    await waitFor(() => expect(screen.queryByText("boom")).not.toBeInTheDocument());
  });

  it("ignores a change event with no file", () => {
    render(<DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />);

    fireEvent.change(fileInput(), { target: { files: [] } });

    expect(uploadDocument).not.toHaveBeenCalled();
  });

  it("uploads a dropped PDF", async () => {
    uploadDocument.mockResolvedValue(makeDocument());
    const onUploadSuccess = vi.fn();

    const { container } = render(
      <DocumentUploader subjectId="subject-1" onUploadSuccess={onUploadSuccess} />,
    );
    const dropzone = container.querySelector("div.cursor-pointer") as HTMLElement;
    const file = pdfFile("dropped.pdf");

    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledWith("subject-1", file));
  });

  it("validates a dropped non-PDF file", async () => {
    const { container } = render(
      <DocumentUploader subjectId="subject-1" onUploadSuccess={vi.fn()} />,
    );
    const dropzone = container.querySelector("div.cursor-pointer") as HTMLElement;

    fireEvent.drop(dropzone, {
      dataTransfer: { files: [new File(["x"], "notes.docx")] },
    });

    expect(await screen.findByText("Only PDF (.pdf) documents are supported.")).toBeInTheDocument();
    expect(uploadDocument).not.toHaveBeenCalled();
  });
});
