"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuthStore } from "@/store/useAuthStore";

export default function SettingsPage() {
  const router = useRouter();
  const { user, isAuthenticated, logout } = useAuthStore();
  const [theme, setTheme] = useState("light");

  if (!isAuthenticated || !user) {
    return (
      <main className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="text-center">
          <div className="inline-flex p-3 rounded-full bg-slate-700/60 text-slate-400">
            <svg className="h-6 w-6 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <path d="M8 12l-4 4 4 4"></path>
              <line x1="12" y1="20" x2="12" y2="10"></line>
              <line x1="16" y1="12" x2="20" y2="12"></line>
            </svg>
          </div>
          <h2 className="mt-4 text-2xl font-extrabold text-white">Settings</h2>
          <p className="mt-2 text-slate-400">Account and preferences</p>
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
            <h2 className="text-xl font-bold text-white mb-3">Settings</h2>

            {/* Theme toggle */}
            <div>
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <span className="font-medium text-white">Theme</span>
              </label>
              <button
                onClick={() => setTheme((prev) => (prev === "light" ? "dark" : "light"))}
                className="mt-2 w-full flex items-center gap-3 px-4 py-2.5 rounded-xl border border-border text-sm font-medium hover:bg-border/80 transition-colors"
              >
                <svg
                  className="h-4 w-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  {theme === "light" ? (
                    <svg>
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
                  ) : (
                    <svg>
                      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 6.82 21.02 8.01 9.27 3 9.27 3.91 8.26 12 2"></polygon>
                    </svg>
                  )}
                </svg>
                <span className="sr-only">Toggle {theme === "light" ? "dark" : "light"} mode</span>
              </button>
            </div>

            {/* Logout button */}
            <button
              onClick={logout}
              className="w-full flex items-center gap-2 px-4 py-2.5 rounded-xl bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold shadow-sm shadow-rose-400 transition-all"
              title="Logout"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="9" cy="9" r="2"></circle>
                <circle cx="20" cy="9" r="2"></circle>
                <line x1="1" y1="5" x2="23" y2="5"></line>
                <line x1="1" y1="12" x2="23" y2="12"></line>
                <line x1="1" y1="19" x2="23" y2="19"></line>
              </svg>
              Logout
            </button>
          </div>
        </div>
      </main>
    </main>
  );
}