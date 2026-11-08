"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import { api, Subject, Document, DocumentStatus } from "@/lib/api";
import { DocumentUploader } from "@/components/documents/DocumentUploader";
import { DocumentList } from "@/components/documents/DocumentList";
import { RagTutor } from "@/components/rag/RagTutor";
import { QuizSection } from "@/components/quiz/QuizSection";
import { FlashcardSection } from "@/components/flashcards/FlashcardSection";
import { StudentIntelligence } from "@/components/analytics/StudentIntelligence";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { BookOpen, BrainCircuit, ArrowLeft, Bot, Sparkles, FileText, Layers, FolderOpen, Activity } from "lucide-react";

const PROCESS_POLL_INTERVAL_MS = 2000;
const PROCESS_POLL_MAX_TICKS = 15;

export default function SubjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const subjectId = params?.id as string;
  const recommendedTopic = searchParams.get("topic")?.trim() || null;
  const focusSection = searchParams.get("focus");

  const { user, isAuthenticated, initialize } = useAuthStore();
  const [subject, setSubject] = useState<Subject | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reprocessingId, setReprocessingId] = useState<string | null>(null);
  const [performanceRefreshKey, setPerformanceRefreshKey] = useState(0);

  const pollTimers = useRef<Array<ReturnType<typeof setTimeout>>>([]);

  useEffect(() => {
    initialize();
  }, [initialize]);

  useEffect(() => {
    if (isAuthenticated && subjectId) {
      fetchSubjectData();
    } else if (!isAuthenticated) {
      router.push("/login");
      setIsLoading(false);
    }
  }, [isAuthenticated, subjectId]);

  useEffect(() => {
    const timers = pollTimers.current;
    return () => {
      timers.forEach(clearTimeout);
      timers.length = 0;
    };
  }, []);

  const fetchSubjectData = async () => {
    setIsLoading(true);
    try {
      const [subjData, docsData] = await Promise.all([
        api.getSubject(subjectId),
        api.getSubjectDocuments(subjectId),
      ]);
      setSubject(subjData);
      setDocuments(docsData);
      setLoadError(null);
      docsData
        .filter((doc) => doc.status === "PROCESSING")
        .forEach((doc) => watchDocument(doc.id));
    } catch (err: any) {
      console.error("Failed to load subject details:", err);
      setLoadError(err.message || "Failed to load this subject.");
    } finally {
      setIsLoading(false);
    }
  };

  const applyStatus = useCallback((docId: string, status: DocumentStatus) => {
    setDocuments((prev) =>
      prev.map((doc) =>
        doc.id === docId
          ? {
              ...doc,
              status: status.status,
              error_message: status.error_message ?? null,
              chunk_count: status.chunk_count,
            }
          : doc,
      ),
    );
  }, []);

  const watchDocument = useCallback(
    (docId: string, tick = 0) => {
      if (tick >= PROCESS_POLL_MAX_TICKS) return;

      pollTimers.current.push(
        setTimeout(async () => {
          let status: DocumentStatus;
          try {
            status = await api.getDocumentStatus(docId);
          } catch {
            return;
          }
          applyStatus(docId, status);
          if (status.status === "PROCESSING") {
            watchDocument(docId, tick + 1);
          }
        }, PROCESS_POLL_INTERVAL_MS),
      );
    },
    [applyStatus],
  );

  const handleDocumentUploaded = (newDoc: Document) => {
    setDocuments((prev) => [newDoc, ...prev]);
    watchDocument(newDoc.id);
  };

  const handleReprocessDocument = async (docId: string) => {
    setReprocessingId(docId);
    try {
      const status = await api.reprocessDocument(docId);
      applyStatus(docId, status);
      setLoadError(null);
      watchDocument(docId);
    } catch (err: any) {
      console.error("Failed to reprocess document:", err);
      setLoadError(err.message || "Failed to process this document again.");
    } finally {
      setReprocessingId(null);
    }
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

  const scrollToSection = (sectionId: string) => {
    const element = document.getElementById(sectionId);
    if (element) {
      element.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  useEffect(() => {
    if (!isLoading && (recommendedTopic || focusSection)) {
      const sectionId =
        focusSection === "materials"
          ? "study-materials"
          : focusSection === "flashcards"
            ? "flashcards"
            : "quiz-engine";
      const element = document.getElementById(sectionId);
      if (element) {
        element.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  }, [focusSection, recommendedTopic, isLoading]);

  if (isLoading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <div className="h-8 w-8 border-4 border-primary border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  if (!subject) {
    return (
      <div className="min-h-screen bg-slate-950 p-8 text-slate-300 space-y-4">
        {loadError ? (
          <>
            <ErrorBanner message={loadError} />
            <button
              onClick={fetchSubjectData}
              className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-white"
            >
              Try again
            </button>
          </>
        ) : (
          <p>Subject not found.</p>
        )}
        <button
          onClick={() => router.push("/dashboard")}
          className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-white"
        >
          Back to Dashboard
        </button>
      </div>
    );
  }

  const totalChunks = documents.reduce((sum, d) => sum + (d.chunk_count || 0), 0);
  const readyDocs = documents.filter((d) => d.status === "READY").length;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 pb-12">
      {/* Header Bar */}
      <header className="sticky top-0 z-40 border-b border-border bg-slate-900/80 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <button
            onClick={() => router.push("/dashboard")}
            className="flex items-center gap-2 text-slate-400 hover:text-white text-xs font-semibold transition-colors"
          >
            <ArrowLeft className="h-4 w-4" /> Back to Dashboard
          </button>

          <div className="flex items-center gap-2">
            <span
              className="h-3 w-3 rounded-full"
              style={{ backgroundColor: subject.color }}
            />
            <span className="font-bold text-sm text-white">{subject.title}</span>
          </div>
        </div>
      </header>

      {/* Main Container */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
        {/* Subject Hero Header */}
        <div className="relative overflow-hidden rounded-3xl border border-border bg-slate-900/60 p-6 sm:p-8">
          <div className="absolute top-0 left-0 right-0 h-2" style={{ backgroundColor: subject.color }} />

          <div className="flex flex-col md:flex-row md:items-center justify-between gap-6">
            <div className="space-y-2 max-w-2xl">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-lg bg-slate-800 text-slate-300 text-xs font-semibold">
                <BookOpen className="h-3.5 w-3.5" style={{ color: subject.color }} /> Subject Module
              </div>
              <h1 className="text-3xl font-extrabold text-white tracking-tight">{subject.title}</h1>
              <p className="text-sm text-slate-400 leading-relaxed">
                {subject.description || "Upload textbooks and PDF study guides below to enable grounded RAG tutoring."}
              </p>
            </div>

            {/* Downstream RAG & Quiz Quick Action Buttons */}
            <div className="flex flex-wrap items-center gap-3 shrink-0">
              <button
                disabled={readyDocs === 0}
                onClick={() => scrollToSection("rag-tutor")}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-primary text-primary text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                <Bot className="h-4 w-4" /> Ask RAG Tutor
              </button>
              <button
                disabled={readyDocs === 0}
                onClick={() => scrollToSection("quiz-engine")}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-700 bg-slate-800/60 text-slate-300 text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                <Sparkles className="h-4 w-4" /> Generate Quiz
              </button>
              <button
                disabled={readyDocs === 0}
                onClick={() => scrollToSection("flashcards")}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-slate-700 bg-slate-800/60 text-slate-300 text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                <Layers className="h-4 w-4" /> Build Flashcards
              </button>
            </div>
          </div>
        </div>

        {/* Stats Row */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="p-4 rounded-2xl border border-border bg-slate-900/40 flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
              <FileText className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-slate-400 font-medium">Uploaded Documents</p>
              <p className="text-xl font-bold text-white">{documents.length} Files ({readyDocs} Ready)</p>
            </div>
          </div>

          <div className="p-4 rounded-2xl border border-border bg-slate-900/40 flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
              <Layers className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-slate-400 font-medium">Extracted RAG Vectors</p>
              <p className="text-xl font-bold text-white">{totalChunks} Chunks</p>
            </div>
          </div>

          <div className="p-4 rounded-2xl border border-border bg-slate-900/40 flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20">
              <BrainCircuit className="h-5 w-5" />
            </div>
            <div>
              <p className="text-xs text-slate-400 font-medium">Vector Indexing</p>
              <p className="text-xl font-bold text-white">Gemini 768-dim</p>
            </div>
          </div>
        </div>

        {/* Upload & Documents Section */}
        <section id="study-materials" className="space-y-6 scroll-mt-20">
          <div className="space-y-1">
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <FolderOpen className="h-5 w-5 text-sky-400" /> Study Materials & PDFs
            </h2>
            <p className="text-xs text-slate-400">
              Upload course PDFs to extract text chunks and vector embeddings for contextual AI tutoring.
            </p>
          </div>

          <DocumentUploader
            subjectId={subjectId}
            onUploadSuccess={handleDocumentUploaded}
          />

          <section className="pt-4 space-y-3">
            {loadError && <ErrorBanner message={loadError} />}
            <DocumentList
              documents={documents}
              onDelete={handleDeleteDocument}
              onReprocess={handleReprocessDocument}
              reprocessingId={reprocessingId}
            />
          </section>
        </section>

        {/* RAG Tutor Section */}
        <section id="rag-tutor" className="space-y-6 scroll-mt-20">
          <div>
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Bot className="h-5 w-5 text-sky-400" /> RAG Tutor — Ask AI
            </h2>
            <p className="text-xs text-slate-400">
              Ask questions about this subject. Answers are strictly grounded in the indexed chunks from your uploaded
              PDFs, with per-source citations.
            </p>
          </div>
          <RagTutor subjectId={subjectId} hasReadyDocuments={readyDocs > 0} />
        </section>

        {/* Quiz Engine Section */}
        <section id="quiz-engine" className="space-y-6 scroll-mt-20">
          <div>
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-sky-400" /> Quiz Engine
            </h2>
            <p className="text-xs text-slate-400">
              Configure a multiple-choice quiz generated from this subject&apos;s study materials, complete and submit
              it, then review explanations and sources.
            </p>
          </div>
          <QuizSection
            subjectId={subjectId}
            hasReadyDocuments={readyDocs > 0}
            initialTopic={recommendedTopic ?? undefined}
            onAttemptComplete={() => setPerformanceRefreshKey((k) => k + 1)}
          />
        </section>

        {/* Flashcards Section */}
        <section id="flashcards" className="space-y-6 scroll-mt-20">
          <div>
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Layers className="h-5 w-5 text-cyan-400" /> Flashcards
            </h2>
            <p className="text-xs text-slate-400">
              Generate a deck of question-and-answer cards from this subject&apos;s materials, then self-grade each card
              to track your progress. Every card keeps a citation back to the source it came from.
            </p>
          </div>
          <FlashcardSection
            subjectId={subjectId}
            hasReadyDocuments={readyDocs > 0}
            initialTopic={recommendedTopic ?? undefined}
          />
        </section>

        {/* Student Intelligence Section */}
        <section id="student-intelligence" className="space-y-6 scroll-mt-20">
          <div>
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Activity className="h-5 w-5 text-emerald-400" /> Student Intelligence
            </h2>
            <p className="text-xs text-slate-400">
              Quiz performance converted into learning data — attempt history, accuracy, and confidence-aware
              mastery for each topic in this subject.
            </p>
          </div>
          <StudentIntelligence subjectId={subjectId} refreshKey={performanceRefreshKey} />
        </section>
      </main>
    </div>
  );
}