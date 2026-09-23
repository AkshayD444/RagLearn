"use client";

import React, { useState } from "react";
import { api, RagAnswerResponse } from "@/lib/api";
import { Bot, Send, BookOpen, FileText, CopyX, RefreshCw, Quote } from "lucide-react";
import { Spinner } from "@/components/shared/Spinner";
import { ErrorBanner } from "@/components/shared/ErrorBanner";
import { EmptyState } from "@/components/shared/EmptyState";

interface RagTutorProps {
  subjectId: string;
  hasReadyDocuments: boolean;
}

export function RagTutor({ subjectId, hasReadyDocuments }: RagTutorProps) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<RagAnswerResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canAsk =
    hasReadyDocuments &&
    question.trim().length > 0 &&
    !isLoading;

  const handleAsk = async () => {
    const trimmed = question.trim();
    if (!trimmed || !hasReadyDocuments) return;

    setIsLoading(true);
    setError(null);
    try {
      const response = await api.askRagTutor(subjectId, trimmed);
      setAnswer(response);
    } catch (err: any) {
      setAnswer(null);
      setError(err.message || "Failed to get an answer. Please try again.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleReset = () => {
    setAnswer(null);
    setError(null);
  };

  if (!hasReadyDocuments) {
    return (
      <EmptyState
        icon={<Bot className="h-6 w-6" />}
        title="RAG Tutor is waiting for study materials"
        description="Upload at least one PDF and wait for it to finish processing before asking the tutor. Questions are answered strictly from your own indexed documents."
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Question Composer */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4 sm:p-5 space-y-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-400">
          <Bot className="h-4 w-4 text-sky-400" />
          <span>Ask a question grounded in this subject&apos;s documents</span>
        </div>

        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              handleAsk();
            }
          }}
          rows={3}
          placeholder="e.g. Explain how photosynthesis converts sunlight into chemical energy."
          className="w-full rounded-xl bg-slate-950 border border-slate-800 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 outline-none text-sm text-white placeholder:text-slate-500 px-4 py-3 resize-none transition-colors"
        />

        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] text-slate-500">
            Press <kbd className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">Ctrl</kbd> +{" "}
            <kbd className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">Enter</kbd> to ask
          </p>
          <button
            onClick={handleAsk}
            disabled={!canAsk}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-gradient-to-r from-sky-500 to-blue-600 text-white text-xs font-semibold shadow-lg shadow-sky-500/20 hover:from-sky-400 hover:to-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {isLoading ? <Spinner className="h-3.5 w-3.5" /> : <Send className="h-3.5 w-3.5" />}
            {isLoading ? "Consulting your notes..." : "Ask RAG Tutor"}
          </button>
        </div>

        {error && <ErrorBanner message={error} />}
      </div>

      {/* Answer Display */}
      {isLoading && (
        <div className="flex items-center gap-3 p-4 rounded-2xl border border-slate-800 bg-slate-900/40">
          <Spinner className="h-5 w-5" />
          <p className="text-xs text-slate-400">
            Retrieving relevant chunks and composing a grounded answer...
          </p>
        </div>
      )}

      {!isLoading && answer && (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 overflow-hidden">
          <div className="p-4 sm:p-5 border-b border-slate-800 flex items-start justify-between gap-3">
            <div className="space-y-1.5 min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Your Question</p>
              <p className="text-sm font-bold text-white">{answer.question}</p>
            </div>
            <span
              className={`shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-semibold border ${
                answer.model_used.toLowerCase().includes("gemini")
                  ? "bg-sky-950/80 border-sky-800/60 text-sky-400"
                  : "bg-emerald-950/80 border-emerald-800/60 text-emerald-400"
              }`}
            >
              <Bot className="h-3 w-3" />
              {answer.model_used.toLowerCase().includes("gemini") ? "Gemini AI" : "Grounded Fallback"}
            </span>
          </div>

          <div className="p-4 sm:p-5 space-y-5">
            <div className="space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Grounded Answer</p>
              <p className="text-sm text-slate-200 leading-relaxed whitespace-pre-wrap">{answer.answer}</p>
            </div>

            <div className="space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                Source Citations{answer.sources.length > 0 && ` (${answer.sources.length})`}
              </p>

              {answer.sources.length === 0 ? (
                <div className="p-4 rounded-xl border border-dashed border-slate-800 bg-slate-950/40 flex items-start gap-2 text-xs text-slate-400">
                  <CopyX className="h-4 w-4 shrink-0 text-slate-500" />
                  <span>
                    No source excerpts were returned for this answer. The response was composed from the subject&apos;s
                    best matching material, but verify from the original documents.
                  </span>
                </div>
              ) : (
                <ul className="space-y-2.5">
                  {answer.sources.map((source) => (
                    <li
                      key={source.chunk_id}
                      className="p-4 rounded-xl border border-slate-800 bg-slate-950/40"
                    >
                      <div className="flex items-center justify-between gap-3 mb-2">
                        <span className="inline-flex items-center gap-1.5 text-xs font-bold text-sky-300 min-w-0">
                          <FileText className="h-3.5 w-3.5 shrink-0" />
                          <span className="truncate">{source.document_title}</span>
                        </span>
                        <div className="flex items-center gap-2 shrink-0">
                          <span className="inline-flex items-center gap-1 text-[10px] text-slate-400">
                            <BookOpen className="h-3 w-3" /> Page {source.page_number}
                          </span>
                          <span className="text-[10px] font-semibold text-slate-500">
                            {Math.round(source.score * 100)}% match
                          </span>
                        </div>
                      </div>
                      <p className="text-xs text-slate-400 leading-relaxed italic line-clamp-3">
                        <Quote className="h-3 w-3 inline -mt-0.5 mr-1 opacity-50" />
                        {source.content}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="p-4 border-t border-slate-800 flex justify-end">
            <button
              onClick={handleReset}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl border border-slate-700 bg-slate-800/60 text-slate-300 hover:text-white text-xs font-semibold transition-colors"
            >
              <RefreshCw className="h-3.5 w-3.5" /> Ask Another Question
            </button>
          </div>
        </div>
      )}
    </div>
  );
}