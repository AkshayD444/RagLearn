"use client";

import React, { useState } from "react";
import { useAuthStore } from "@/store/useAuthStore";
import { LogIn, UserPlus, AlertCircle, CheckCircle2 } from "lucide-react";

export function AuthModal({ onSuccess }: { onSuccess?: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  
  const { login, register, isLoading, error, clearError } = useAuthStore();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();
    try {
      if (mode === "login") {
        await login({ email, password });
      } else {
        await register({ email, full_name: fullName, password });
      }
      if (onSuccess) onSuccess();
    } catch (err) {
      // Error is set in store
    }
  };

  return (
    <div className="max-w-md p-6 rounded-2xl border border-border bg-card">
      <div className="text-center mb-6">
        <div className="inline-flex p-2 rounded-lg bg-primary/10 border border-primary/20 mb-3">
          <svg className="h-5 w-5 text-primary" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
            <line x1="3" y1="15" x2="21" y2="15"></line>
            <line x1="9" y1="9" x2="15" y2="9"></line>
          </svg>
        </div>
        <h2 className="text-2xl font-bold tracking-tight text-foreground">
          {mode === "login" ? "Welcome Back" : "Create Student Account"}
        </h2>
        <p className="text-sm text-muted mt-1">
          {mode === "login"
            ? "Sign in to access your personalized RAG tutor"
            : "Get started with AI-driven adaptive learning"}
        </p>
      </div>

      <div className="flex p-1 rounded-xl bg-slate-800/80 border border-slate-700/50 mb-6">
        <button
          type="button"
          onClick={() => { setMode("login"); clearError(); }}
          className={`flex-1 py-2 text-xs font-semibold rounded-lg transition-all flex items-center justify-center gap-2 ${
            mode === "login"
              ? "bg-primary text-white shadow-md shadow-primary/30"
              : "text-slate-400 hover:text-foreground"
          }`}
        >
          <LogIn className="h-3.5 w-3.5" /> Login
        </button>
        <button
          type="button"
          onClick={() => { setMode("register"); clearError(); }}
          className={`flex-1 py-2 text-xs font-semibold rounded-lg transition-all flex items-center justify-center gap-2 ${
            mode === "register"
              ? "bg-primary text-white shadow-md shadow-primary/30"
              : "text-slate-400 hover:text-foreground"
          }`}
        >
          <UserPlus className="h-3.5 w-3.5" /> Register
        </button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {mode === "register" && (
          <div>
            <label className="block text-xs font-medium text-muted mb-1">
              Full Name
            </label>
            <input
              type="text"
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="e.g. Alex Johnson"
              className="w-full px-4 py-2.5 rounded-xl input bg-transparent border border-border text-foreground placeholder-muted-focus focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-all"
            />
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-muted mb-1">
            Email Address
          </label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="student@university.edu"
            className="w-full px-4 py-2.5 rounded-xl input bg-transparent border border-border text-foreground placeholder-muted-focus focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-all"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-muted mb-1">
            Password
          </label>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            className="w-full px-4 py-2.5 rounded-xl input bg-transparent border border-border text-foreground placeholder-muted-focus focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-all"
          />
        </div>

        <button
          type="submit"
          disabled={isLoading}
          className="w-full mt-3 px-4 py-2.5 rounded-xl btn-primary transition-all disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? (
            <span className="h-4 w-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          ) : mode === "login" ? (
            "Sign In"
          ) : (
            "Create Account"
          )}
        </button>
      </form>

      {error && (
        <div className="mt-3 p-3 rounded-xl border border-accent/30 bg-accent/5 text-accent-text text-xs flex items-center gap-2">
          <AlertCircle className="h-3.5 w-3.5" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
}