"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import { api } from "@/lib/api";
import { FlashcardSection } from "@/components/flashcards/FlashcardSection";
import { Sidebar } from "@/components/Sidebar";

export default function FlashcardsPage() {
  const router = useRouter();
  const { user, isAuthenticated, initialize } = useAuthStore();
  const [subjects, setSubjects] = useState<{ id: string; title: string }[]>([]);
  const [selectedSubjectId, setSelectedSubjectId] = useState<string | null>(null);
  const [hasReadyDocuments, setHasReadyDocuments] = useState(false);

  useEffect(() => {
    initialize();
  }, [initialize]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchSubjects();
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

  useEffect(() => {
    (async () => {
      if (!selectedSubjectId || !isAuthenticated) {
        setHasReadyDocuments(false);
        return;
      }
      try {
        const docs = await api.getSubjectDocuments(selectedSubjectId);
        const readyDocs = docs.filter(
          (d) => d.status === "READY" && (d.chunk_count || 0) > 0
        );
        setHasReadyDocuments(readyDocs.length > 0);
      } catch (err) {
        console.error("Failed to check document status:", err);
        setHasReadyDocuments(false);
      }
    })();
  }, [selectedSubjectId, isAuthenticated]);

  if (!isAuthenticated || !user) {
    return (
      <main className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="text-center">
          <div className="inline-flex p-3 rounded-full bg-slate-700/60 text-slate-400">
            <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
              <line x1="16" y1="2" x2="20" y2="2"></line>
              <line x1="5" y1="8" x2="11" y2="8"></line>
              <line x1="16" y1="14" x2="20" y2="14"></line>
              <line x1="5" y1="14" x2="11" y2="14"></line>
            </svg>
          </div>
          <h2 className="mt-4 text-2xl font-extrabold text-white">Flashcards</h2>
          <p className="mt-2 text-slate-400">Review with spaced repetition</p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 pb-12">
      <header className="sticky top-0 z-40 border-b border-border bg-slate-900/80 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <svg className="h-3 w-3 rounded-full" style={{ backgroundColor: "#10b981" }} />
            <span className="font-bold text-sm text-white">Flashcards</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => router.push("/dashboard")}
              className="flex items-center gap-1 text-slate-400 hover:text-white text-xs font-semibold transition-colors"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="3" y1="6" x2="21" y2="6"></line>
                <line x1="3" y1="12" x2="21" y2="12"></line>
                <line x1="3" y1="18" x2="21" y2="18"></line>
              </svg>
              Back to Dashboard
            </button>
          </div>
        </div>
      </header>

      <main className="min-h-screen bg-slate-950 text-slate-100 pb-12">
        <div className="space-y-6">
          {/* Subject Selector */}
          <div className="rounded-2xl border border-border bg-slate-900/60 p-4 sm:p-5">
            <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider mb-2">Select Subject</h2>
            <div className="space-y-1.5">
              <select
                value={selectedSubjectId || ""}
                onChange={(e) => setSelectedSubjectId(e.target.value)}
                className="w-full px-3 py-2 rounded-xl border border-border bg-slate-800/50 text-sm font-medium text-white focus:outline-none focus:border-emerald-500 focus:shadow-sm focus:shadow-emerald-400/20"
              >
                <option value="">Select a subject...</option>
                {subjects.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Flashcard Section */}
          {selectedSubjectId && hasReadyDocuments && (
<FlashcardSection
              subjectId={selectedSubjectId}
              hasReadyDocuments={hasReadyDocuments}
              onReviewComplete={() => {}}
/>)}

          {(!selectedSubjectId || selectedSubjectId === "") && (
            <div className="p-8 rounded-2xl border border-slate-700 bg-slate-800/30 text-center space-y-4">
              <svg className="inline-flex h-6 w-6 rounded-full bg-slate-700/60 text-slate-400 mx-auto mb-3">
                <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                <line x1="16" y1="2" x2="20" y2="2"></line>
                <line x1="5" y1="8" x2="11" y2="8"></line>
                <line x1="16" y1="14" x2="20" y2="14"></line>
                <line x1="5" y1="14" x2="11" y2="14"></line>
              </svg>
              <h3 className="text-base font-bold text-white">No Subject Selected</h3>
              <p className="text-slate-500 mt-2">
                Select a subject above to generate flashcards from your uploaded PDF materials.
              </p>
              <p className="text-slate-500 text-sm">
                Upload PDFs to a subject first, then generate flashcards to review with spaced repetition.
              </p>
            </div>
          )}
        </div>
      </main>
    </main>
  );
}