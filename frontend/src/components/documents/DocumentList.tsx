"use client";

import React from "react";
import { Document } from "@/lib/api";
import { FileText, Trash2, CheckCircle2, Clock, AlertTriangle, Layers } from "lucide-react";

interface DocumentListProps {
  documents: Document[];
  onDelete: (id: string) => void;
}

export function DocumentList({ documents, onDelete }: DocumentListProps) {
  if (documents.length === 0) {
    return (
      <div className="p-8 rounded-2xl border border-dashed border-slate-800 bg-slate-900/30 text-center space-y-2">
        <FileText className="h-8 w-8 text-slate-500 mx-auto" />
        <p className="text-sm font-semibold text-slate-300">No Documents Uploaded</p>
        <p className="text-xs text-slate-500">Upload your PDF course materials above to activate RAG tutoring.</p>
      </div>
    );
  }

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  };

  return (
    <div className="space-y-3">
      {documents.map((doc) => (
        <div
          key={doc.id}
          className="flex items-center justify-between p-4 rounded-xl border border-slate-800 bg-slate-900/60 hover:bg-slate-800/50 transition-colors"
        >
          <div className="flex items-center gap-3 min-w-0">
            <div className="p-2.5 rounded-lg bg-sky-500/10 text-sky-400 border border-sky-500/20 shrink-0">
              <FileText className="h-5 w-5" />
            </div>

            <div className="min-w-0">
              <h4 className="text-sm font-bold text-white truncate">{doc.title}</h4>
              <div className="flex items-center gap-3 text-xs text-slate-400 mt-0.5">
                <span>{formatBytes(doc.file_size_bytes)}</span>
                <span>•</span>
                <span className="flex items-center gap-1 text-slate-300">
                  <Layers className="h-3 w-3 text-sky-400" /> {doc.chunk_count} Vector Chunks
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-4 shrink-0">
            {/* Status Badge */}
            {doc.status === "READY" && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-950/80 border border-emerald-800/60 text-emerald-400 text-xs font-semibold">
                <CheckCircle2 className="h-3.5 w-3.5" /> Ready for RAG
              </span>
            )}

            {doc.status === "PROCESSING" && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-950/80 border border-amber-800/60 text-amber-400 text-xs font-semibold">
                <Clock className="h-3.5 w-3.5 animate-spin" /> Chunking...
              </span>
            )}

            {doc.status === "FAILED" && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-rose-950/80 border border-rose-800/60 text-rose-400 text-xs font-semibold" title={doc.error_message || "Extraction failed"}>
                <AlertTriangle className="h-3.5 w-3.5" /> Extraction Failed
              </span>
            )}

            <button
              onClick={() => onDelete(doc.id)}
              title="Delete Document"
              className="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-950/40 transition-colors"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
