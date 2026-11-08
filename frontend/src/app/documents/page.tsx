"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import { api, Document } from "@/lib/api";
import { DocumentUploader } from "@/components/documents/DocumentUploader";
import { DocumentList } from "@/components/documents/DocumentList";
import { Sidebar } from "@/components/Sidebar";

export default function DocumentsPage() {
  const router = useRouter();
  const { user, isAuthenticated, initialize } = useAuthStore();
  const [subjects, setSubjects] = useState<{ id: string; title: string }[]>([]);
  const [subject, setSubject] = useState<{ id: string; title: string } | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    initialize();
  }, [initialize]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchSubjects();
      fetchDocuments();
    }
  }, [isAuthenticated]);

  const fetchSubjects = async () => {
    try {
      const data = await api.getSubjects();
      setSubjects(data.map((s) => ({ id: s.id, title: s.title })));
    } catch (err) {
      console.error("Failed to fetch subjects:", err);
    }
  };

  const fetchDocuments = async () => {
    if (!subject) return;
    setIsLoading(true);
    try {
      const docs = await api.getSubjectDocuments(subject.id);
      setDocuments(docs);
      setLoadError(null);
    } catch (err: any) {
      setLoadError(err.message || "Failed to load documents");
      console.error("Failed to load documents:", err);
    } finally {
      setIsLoading(false);
    }
  };

const handleSubjectChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const selectedId = e.target.value;
    setSubject({ id: selectedId, title: e.target.value });
    setDocuments([]);
    if (selectedId) {
      await fetchDocuments();
    }
  };

  const handleDocumentUpload = async (newDoc: Document) => {
    setDocuments((prev) => [newDoc, ...prev]);
    await fetchDocuments();
  };

  const handleDeleteDocument = async (docId: string) => {
    if (!confirm("Are you sure you want to delete this document and its vector embeddings?")) return;
    try {
      await api.deleteDocument(docId);
      setDocuments((prev) => prev.filter((d) => d.id !== docId));
    } catch (err) {
      console.error("Failed to delete document:", err);
    }
  };

  const handleReprocessDocument = async (docId: string) => {
    setIsLoading(true);
    try {
      const status = await api.reprocessDocument(docId);
      const updatedDocs = documents.map((doc) =>
        doc.id === docId ? { ...doc, ...status } : doc,
      );
      setDocuments(updatedDocs);
      setLoadError(null);
    } catch (err: any) {
      setLoadError(err.message || "Failed to process this document again");
      console.error("Failed to reprocess document:", err);
    } finally {
      setIsLoading(false);
    }
  };

  if (!isAuthenticated || !user) {
    return (
      <main className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="text-center">
          <div className="inline-flex p-3 rounded-full bg-slate-700/60 text-slate-400">
            <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="17 3 21 3 23 9 23 15 19 15 11 9 3 7 3 3 7"></polyline>
            </svg>
          </div>
          <h2 className="mt-4 text-2xl font-extrabold text-white">Documents</h2>
          <p className="mt-2 text-slate-400">Upload and manage PDFs</p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 pb-12">
      <header className="sticky top-0 z-40 border-b border-border bg-slate-900/80 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <svg
              className="h-3 w-3 rounded-full"
              style={{ backgroundColor: "#10b981" }}
            />
            <span className="font-bold text-sm text-white">
              {subject?.title || "All Documents"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSubject(null)}
              className="flex items-center gap-1 px-2 py-1 rounded border border-border text-xs font-medium hover:text-white hover:bg-border/80 transition-colors"
              title="All Documents"
            >
              All
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="space-y-6">
          {/* Subject Selector */}
          {subjects.length > 0 && (
            <div className="rounded-2xl border border-border bg-slate-900/60 p-4 sm:p-5">
              <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider mb-2">Select Subject</h2>
              <div className="space-y-1.5">
                <select
                  onChange={handleSubjectChange}
                  className="w-full px-3 py-2 rounded-xl border border-border bg-slate-800/50 text-sm font-medium text-white focus:outline-none focus:border-emerald-500 focus:shadow-sm focus:shadow-emerald-400/20"
                >
                  <option value="">All Subjects</option>
                  {subjects.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.title}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}

          {/* Document Uploader and List */}
          <section className="space-y-6">
            <div>
              <DocumentUploader
                subjectId={subject?.id || ""}
                onUploadSuccess={handleDocumentUpload}
              />
            </div>

            {subject && documents.length > 0 && (
              <DocumentList
                documents={documents}
                onDelete={handleDeleteDocument}
                onReprocess={handleReprocessDocument}
              />
            )}

            {(!subject || documents.length === 0) && loadError && (
              <div className="p-4 rounded-xl border border-red-500/20 bg-red-500/10 text-red-400 text-sm mb-4">
                <svg className="h-4 w-4 text-red-400 mb-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10"></circle>
                  <path d="M8 12l-4 4 4 4"></path>
                  <line x1="12" y1="20" x2="12" y2="10"></line>
                  <line x1="16" y1="12" x2="20" y2="12"></line>
                </svg>
                <span>{loadError}</span>
              </div>
            )}
          </section>
        </div>
      </main>
    </main>
  );
}