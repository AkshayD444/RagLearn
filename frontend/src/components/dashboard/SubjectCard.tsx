"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { Subject } from "@/lib/api";
import { BookOpen, Trash2, ChevronRight, FileText } from "lucide-react";

interface SubjectCardProps {
  subject: Subject;
  onDelete: (id: string) => void;
  /** Overrides the default navigation, mainly to keep tests off the router. */
  onSelect?: (subject: Subject) => void;
}

export function SubjectCard({ subject, onDelete, onSelect }: SubjectCardProps) {
  const router = useRouter();

  // Navigation lives here rather than at each call site so "Open Module" cannot
  // render as a dead action in a usage that forgot to pass a handler.
  const open = () => {
    if (onSelect) onSelect(subject);
    else router.push(`/subjects/${subject.id}`);
  };

  return (
    <div 
      onClick={open}
      className="group relative rounded-2xl border border-slate-800 bg-slate-900/60 p-6 backdrop-blur-sm transition-all duration-300 hover:border-slate-700 hover:bg-slate-800/60 hover:shadow-xl hover:shadow-sky-950/20 flex flex-col justify-between cursor-pointer"
    >
      <div 
        className="absolute top-0 left-6 right-6 h-1 rounded-b-md transition-all group-hover:h-1.5"
        style={{ backgroundColor: subject.color || "#0284c7" }}
      />

      <div>
        <div className="flex items-start justify-between mb-4 pt-1">
          <div 
            className="p-2.5 rounded-xl border flex items-center justify-center"
            style={{ 
              backgroundColor: `${subject.color}15` || "#0284c715", 
              borderColor: `${subject.color}30` || "#0284c730",
              color: subject.color || "#0284c7"
            }}
          >
            <BookOpen className="h-5 w-5" />
          </div>

          <button
            onClick={(e) => {
              e.stopPropagation();
              onDelete(subject.id);
            }}
            title="Delete Subject"
            className="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-950/40 transition-colors"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>

        <h3 className="text-lg font-bold text-white mb-2 group-hover:text-sky-300 transition-colors">
          {subject.title}
        </h3>

        <p className="text-sm text-slate-400 line-clamp-2 mb-6 min-h-[2.5rem]">
          {subject.description || "No description provided."}
        </p>
      </div>

      <div className="pt-4 border-t border-slate-800/80 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs text-slate-400 font-medium">
          <FileText className="h-3.5 w-3.5 text-sky-400" />
          <span>Study Materials</span>
        </div>

        <button
          onClick={(e) => {
            e.stopPropagation();
            open();
          }}
          className="inline-flex items-center gap-1 text-xs font-semibold text-sky-400 group-hover:text-sky-300 transition-all group-hover:translate-x-1"
        >
          Open Module <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
