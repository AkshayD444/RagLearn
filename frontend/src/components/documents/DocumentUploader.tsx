"use client";

import React, { useState, useRef } from "react";
import { Upload, FileText, AlertCircle, CheckCircle2 } from "lucide-react";
import { api, Document } from "@/lib/api";

interface DocumentUploaderProps {
  subjectId: string;
  onUploadSuccess: (doc: Document) => void;
}

export function DocumentUploader({ subjectId, onUploadSuccess }: DocumentUploaderProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = async (file: File | undefined) => {
    if (!file) return;

    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF (.pdf) documents are supported.");
      return;
    }

    if (file.size > 20 * 1024 * 1024) {
      setError("File size exceeds maximum 20MB limit.");
      return;
    }

    setIsUploading(true);
    setError(null);

    try {
      const doc = await api.uploadDocument(subjectId, file);
      onUploadSuccess(doc);
    } catch (err: any) {
      setError(err.message || "Failed to upload document.");
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <div className="w-full">
      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf"
        className="hidden"
        onChange={(e) => handleFileChange(e.target.files?.[0])}
      />

      <div
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          handleFileChange(e.dataTransfer.files?.[0]);
        }}
        onClick={() => fileInputRef.current?.click()}
        className={`relative overflow-hidden cursor-pointer rounded-2xl border-2 border-dashed p-6 text-center transition-all ${
          isDragging
            ? "border-sky-500 bg-sky-500/10"
            : "border-slate-800 bg-slate-900/40 hover:border-slate-700 hover:bg-slate-900/80"
        }`}
      >
        <div className="flex flex-col items-center gap-3">
          <div className="p-3 rounded-2xl bg-sky-500/10 text-sky-400 border border-sky-500/20">
            {isUploading ? (
              <span className="h-6 w-6 border-2 border-sky-400 border-t-transparent rounded-full animate-spin"></span>
            ) : (
              <Upload className="h-6 w-6" />
            )}
          </div>

          <div>
            <p className="text-sm font-semibold text-white">
              {isUploading ? "Uploading & Extracting PDF Chunks..." : "Click or Drag & Drop PDF Study Materials"}
            </p>
            <p className="text-xs text-slate-400 mt-1">
              Supports textbooks, lecture notes, syllabus (Max 20MB)
            </p>
          </div>
        </div>
      </div>

      {error && (
        <div className="mt-3 p-3 rounded-xl bg-rose-950/60 border border-rose-800/60 text-rose-300 text-xs flex items-center gap-2">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}
