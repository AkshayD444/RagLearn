"use client";

import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/useAuthStore";

export default function ProfilePage() {
  const router = useRouter();
  const { user, isAuthenticated } = useAuthStore();

  if (!isAuthenticated || !user) {
    return (
      <main className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="text-center">
          <div className="inline-flex p-3 rounded-full bg-slate-700/60 text-slate-400">
            <svg className="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <path d="M8 12l-4 4 4 4"></path>
              <line x1="12" y1="20" x2="12" y2="10"></line>
              <line x1="16" y1="12" x2="20" y2="12"></line>
            </svg>
          </div>
          <h2 className="mt-4 text-2xl font-extrabold text-white">Profile</h2>
          <p className="mt-2 text-slate-400">Your profile and details</p>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 pb-12">
      <header className="sticky top-0 z-40 border-b border-border bg-slate-900/80 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <button
            onClick={() => router.push("/dashboard")}
            className="flex items-center gap-2 text-slate-400 hover:text-white text-xs font-semibold transition-colors"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="3" y1="6" x2="21" y2="6"></line>
              <line x1="3" y1="12" x2="21" y2="12"></line>
              <line x1="3" y1="18" x2="21" y2="18"></line>
            </svg>
            Back to Dashboard
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="max-w-2xl mx-auto space-y-6">
          <div className="p-6 rounded-2xl border border-border bg-slate-900/60">
            <h2 className="text-xl font-bold text-white mb-3">Profile</h2>

            <div className="space-y-4">
              <div>
                <p className="text-sm text-slate-400">Email</p>
                <p className="font-medium text-white">{user?.email || "—"}</p>
              </div>
              <div>
                <p className="text-sm text-slate-400">Name</p>
                <p className="font-medium text-white">{user?.full_name || "—"}</p>
              </div>
              <div>
                <p className="text-sm text-slate-400">Member since</p>
                <p className="font-medium text-slate-500">—</p>
              </div>
            </div>
          </div>
        </div>
      </main>
    </main>
  );
}