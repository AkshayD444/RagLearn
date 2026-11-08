"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";
import { api, Subject } from "@/lib/api";
import { SubjectCard } from "@/components/dashboard/SubjectCard";
import { CreateSubjectModal } from "@/components/dashboard/CreateSubjectModal";
import { Sidebar } from "@/components/Sidebar";

export default function SubjectsPage() {
  const router = useRouter();
  const { user, isAuthenticated, initialize, logout } = useAuthStore();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isFetchingSubjects, setIsFetchingSubjects] = useState(false);

  useEffect(() => {
    initialize();
  }, [initialize]);

  useEffect(() => {
    if (isAuthenticated) {
      fetchSubjects();
    }
  }, [isAuthenticated]);

  const fetchSubjects = async () => {
    setIsFetchingSubjects(true);
    try {
      const data = await api.getSubjects();
      setSubjects(data);
    } catch (err) {
      console.error("Failed to fetch subjects:", err);
    } finally {
      setIsFetchingSubjects(false);
    }
  };

  const handleCreateSubject = async (payload: { title: string; description: string; color: string }) => {
    const newSubject = await api.createSubject(payload);
    setSubjects((prev) => [newSubject, ...prev]);
    setIsCreateModalOpen(false);
  };

  const handleDeleteSubject = async (id: string) => {
    if (!confirm("Are you sure you want to delete this subject?")) return;
    try {
      await api.deleteSubject(id);
      setSubjects((prev) => prev.filter((s) => s.id !== id));
    } catch (err) {
      console.error("Failed to delete subject:", err);
    }
  };

  if (!isAuthenticated || !user) {
    return (
      <main className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="text-center">
          <div className="inline-flex p-3 rounded-full bg-slate-700/60 text-slate-400">
            <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 4h18a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7"></path>
              <polygon points="9 9 9 13 5 13 5 9 9 9"></polygon>
            </svg>
          </div>
          <h2 className="mt-4 text-2xl font-extrabold text-white">Subjects</h2>
          <p className="mt-2 text-slate-400">Manage your course modules</p>
        </div>
      </main>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Application Shell */}
      <Sidebar />

      {/* Main Container */}
      <main className="ml-64 sm:ml-80 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        {/* Top Section - Page heading with welcome and primary action */}
        <section className="flex flex-col sm:flex-row gap-6 items-start sm:items-center">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-2">
              <h1 className="text-xl font-extrabold text-white tracking-tight">
                My Subjects
              </h1>
            </div>
            <p className="text-slate-400 text-xs sm:text-base">
              Organize your academic courses into subjects below to begin uploading PDFs, generating grounded RAG study materials, and running adaptive quizzes.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsCreateModalOpen(true)}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-sm shadow-emerald-400 transition-all"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 5v14M5 12h14"></path>
              </svg>
              Add Subject
            </button>
          </div>
        </section>

        {/* Statistics Row */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
          <div className="p-3 rounded-xl border border-slate-700 bg-slate-800/50 flex items-center gap-3">
            <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
              <svg className="h-4 w-4 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="17 3 21 3 23 9 23 15 19 15 11 9 3 7 3 3 7"></polyline>
              </svg>
            </div>
            <div>
              <p className="text-xs text-slate-500 font-medium">Enrolled Subjects</p>
              <p className="text-lg font-bold text-white">{subjects.length}</p>
            </div>
          </div>
        </div>

        {/* Main Content - Structured dashboard layout */}
        <div className="grid grid-cols-1 gap-6">
          {/* My subjects grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {isFetchingSubjects ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="h-48 rounded-2xl bg-slate-800/40 border border-slate-700 animate-pulse" />
                ))}
              </div>
            ) : subjects.length === 0 ? (
              <div className="p-8 rounded-2xl border border-slate-700 bg-slate-800/30 text-center space-y-4">
                <div className="inline-flex p-3 rounded-full bg-slate-700/60 text-slate-400">
                  <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 4h18a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7"></path>
                    <polygon points="9 9 9 13 5 13 5 9 9 9"></polygon>
                  </svg>
                </div>
                <div>
                  <h3 className="text-base font-bold text-white">No Subjects Added Yet</h3>
                  <p className="text-sm text-slate-500 mt-1 max-w-sm mx-auto">
                    Click Create Subject above to set up your first course module. Add subjects to organize your courses, upload PDFs, and generate RAG-powered study materials.
                  </p>
                  <button
                    onClick={() => setIsCreateModalOpen(true)}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 text-xs font-semibold hover:bg-emerald-600/30 transition-colors"
                  >
                    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 5v14M5 12h14"></path>
                    </svg>
                    Create First Subject
                  </button>
                </div>
              </div>
            ) : subjects.map((subject) => (
              <SubjectCard
                key={subject.id}
                subject={subject}
                onDelete={handleDeleteSubject}
              />
            ))}
          </div>
        </div>
      </main>
      <CreateSubjectModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onCreate={handleCreateSubject}
      />
    </div>
  );
}