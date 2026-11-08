"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import { api } from "@/lib/api";
import { RagTutor } from "@/components/rag/RagTutor";
import { Sidebar } from "@/components/Sidebar";

export default function AiTutorPage() {
  const router = useRouter();
  const { user, isAuthenticated, initialize } = useAuthStore();
  const [currentQuestion, setCurrentQuestion] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);
  const [showAnswer, setShowAnswer] = useState(false);
  const [sources, setSources] = useState<any[]>([]);
  const [modelUsed, setModelUsed] = useState<string>("");

  useEffect(() => {
    initialize();
  }, [initialize]);

  useEffect(() => {
    if (isAuthenticated) {
      // Just enable the page; RagTutor handles subject selection internally
    }
  }, [isAuthenticated]);

  if (!isAuthenticated || !user) {
    return (
      <main className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="text-center">
          <div className="inline-flex p-3 rounded-full bg-slate-700/60 text-slate-400">
            <svg className="h-6 w-6 text-sky-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <path d="M8 12l-4 4 4 4"></path>
              <line x1="12" y1="20" x2="12" y2="10"></line>
              <line x1="16" y1="12" x2="20" y2="12"></line>
            </svg>
          </div>
          <h2 className="mt-4 text-2xl font-extrabold text-white">AI Tutor</h2>
          <p className="mt-2 text-slate-400">Ask questions about your uploaded materials</p>
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
            <span className="font-bold text-sm text-white">AI Tutor</span>
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

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="space-y-6">
          {/* Question Input */}
          <div className="p-6 rounded-2xl border border-border bg-slate-900/50">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!currentQuestion.trim() || isSubmitting) return;
                setIsSubmitting(true);
                api
                  .askRagTutor("", currentQuestion)
                  .then((res) => {
                    setAnswer(res.answer);
                    setSources(res.sources);
                    setModelUsed(res.model_used || "unknown");
                    setShowAnswer(true);
                  })
                  .finally(() => setIsSubmitting(false));
              }}
            >
              <div className="flex items-start gap-3">
                <div className="flex-1">
                  <textarea
                    value={currentQuestion}
                    onChange={(e) => setCurrentQuestion(e.target.value)}
                    placeholder="Ask a question about your materials..."
                    rows={1}
                    className="w-full px-3 py-2 rounded-xl border border-border bg-slate-800/50 text-sm font-medium text-white focus:outline-none focus:border-emerald-500 focus:shadow-sm focus:shadow-emerald-400/20 resize-none"
                    disabled={isSubmitting}
                    aria-label="Ask a question"
                    required
                  />
                </div>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm shadow-emerald-400 transition-all"
                >
                  <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="5"></circle>
                    <line x1="12" y1="1" x2="12" y2="3"></line>
                    <line x1="12" y1="21" x2="12" y2="23"></line>
                    <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
                    <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
                    <line x1="1" y1="12" x2="3" y2="12"></line>
                    <line x1="21" y1="12" x2="23" y2="12"></line>
                    <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
                    <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
                  </svg>
                  Ask
                </button>
              </div>
            </form>
          </div>

          {/* Answer Display */}
          {showAnswer && answer && (
            <div className="p-6 rounded-2xl border border-border bg-slate-900/50">
              <h2 className="text-lg font-bold text-white mb-3">Answer</h2>
              <p className="text-slate-300">{answer}</p>
              {modelUsed && (
                <p className="text-sm text-slate-500 mb-2">Model: {modelUsed}</p>
              )}
              {sources && sources.length > 0 && (
                <div className="mt-4 space-y-3 max-w-2xl">
                  <h3 className="text-sm font-medium text-slate-400">Sources</h3>
                  {sources.map((source, idx) => (
                    <div key={idx} className="p-3 rounded-lg bg-slate-800/50 border border-slate-700">
                      <p className="text-xs text-slate-400">
                        {source.document_title || "Document"} — Page {source.page_number}
                      </p>
                      <p className="text-slate-300 line-clamp-3">
                        {source.content.substring(0, 300)}...
                      </p>
                    </div>
                  ))}
                </div>
              )}

              {!sources || sources.length === 0 && (
                <p className="text-sm text-slate-500">No sources found.</p>
              )}
            </div>
          )}
        </div>
      </main>
    </main>
  );
}