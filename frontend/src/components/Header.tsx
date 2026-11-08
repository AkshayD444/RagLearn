"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

export function Header({
  setActiveSection,
}: {
  setActiveSection?: (section: string) => void;
}) {
  const [theme, setTheme] = useState("light");

  useEffect(() => {
    const html = document.documentElement;
    const stored = localStorage.getItem("raglearn-theme");
    if (stored) {
      setTheme(stored);
      html.setAttribute("data-theme", stored);
    } else if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
      setTheme("dark");
      html.setAttribute("data-theme", "dark");
    }
  }, []);

  const toggleTheme = () => {
    setTheme((prev) => {
      const next = prev === "light" ? "dark" : "light";
      localStorage.setItem("raglearn-theme", next);
      document.documentElement.setAttribute("data-theme", next);
      return next;
    });
  };

  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsMobileMenuOpen(false);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  const handleMobileMenuClose = () => setIsMobileMenuOpen(false);

  return (
    <header
      className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur-sm"
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h1 className="font-bold text-lg text-foreground tracking-tight">
            RagLearn
          </h1>
        </div>
        <div className="flex items-center gap-4">
          <button
            onClick={toggleTheme}
            className="flex items-center gap-2 px-3 py-1.5 rounded-xl border border-border hover:bg-border/80 transition-colors"
            aria-label="Toggle theme"
          >
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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
          <button
            onClick={() => setIsMobileMenuOpen(true)}
            className="flex items-center gap-2 px-3 py-1.5 rounded-xl border border-border hover:bg-border/80 transition-colors hover:bg-emerald-500/20"
            aria-label="Open main navigation"
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
              <line x1="3" y1="6" x2="21" y2="6"></line>
              <line x1="3" y1="12" x2="21" y2="12"></line>
              <line x1="3" y1="18" x2="21" y2="18"></line>
            </svg>
            <span className="sr-only">Open navigation menu</span>
          </button>
          <div className="relative">
            <button onClick={(e) => e.stopPropagation()} className="flex items-center gap-2 px-3 py-1.5 rounded-xl border border-border text-sm font-medium hover:bg-border/80 transition-colors" aria-label="User menu">
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"></circle>
                <path d="M8 12l-4 4 4 4"></path>
                <line x1="12" y1="20" x2="12" y2="10"></line>
                <line x1="16" y1="12" x2="20" y2="12"></line>
              </svg>
              <span className="hidden sm:inline">User</span>
            </button>
            <div
              className="absolute right-0 mt-2 w-48 rounded-xl bg-border border-border shadow-lg z-40 hidden sm:block"
              style={{ top: "100%" }}
            >
              <div className="p-3 space-y-1">
                <button
                  onClick={() => setActiveSection?.("dashboard")}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-xl text-sm hover:bg-border/50 transition-colors"
                >
                  <svg className="h-4 w-4 text-slate-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="3" y1="6" x2="21" y2="6"></line>
                    <line x1="3" y1="12" x2="21" y2="12"></line>
                    <line x1="3" y1="18" x2="21" y2="18"></line>
                  </svg>
                  Dashboard
                </button>
                <button
                  onClick={() => setActiveSection?.("subjects")}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-xl text-sm hover:bg-border/50 transition-colors"
                >
                  <svg className="h-4 w-4 text-slate-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M22 11.08V12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h15"></path>
                    <polyline points="22 4 12 14 2 4"></polyline>
                  </svg>
                  My Subjects
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}