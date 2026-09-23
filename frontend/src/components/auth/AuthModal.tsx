"use client";

import React, { useState } from "react";
import { useAuthStore } from "@/store/useAuthStore";
import { LogIn, UserPlus, Sparkles, AlertCircle } from "lucide-react";

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
    <div className="w-full max-w-md p-8 rounded-2xl bg-slate-900/90 border border-slate-800 backdrop-blur-xl shadow-2xl">
      <div className="text-center mb-8">
        <div className="inline-flex p-3 rounded-2xl bg-sky-500/10 text-sky-400 mb-3 border border-sky-500/20">
          <Sparkles className="h-6 w-6" />
        </div>
        <h2 className="text-2xl font-bold tracking-tight text-white">
          {mode === "login" ? "Welcome Back" : "Create Student Account"}
        </h2>
        <p className="text-sm text-slate-400 mt-1">
          {mode === "login"
            ? "Sign in to access your personalized RAG tutor"
            : "Get started with AI-driven adaptive learning"}
        </p>
      </div>

      <div className="flex p-1 rounded-xl bg-slate-800/80 mb-6 border border-slate-700/50">
        <button
          type="button"
          onClick={() => { setMode("login"); clearError(); }}
          className={`flex-1 py-2 text-xs font-semibold rounded-lg transition-all flex items-center justify-center gap-2 ${
            mode === "login"
              ? "bg-sky-600 text-white shadow-md shadow-sky-600/30"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <LogIn className="h-3.5 w-3.5" /> Login
        </button>
        <button
          type="button"
          onClick={() => { setMode("register"); clearError(); }}
          className={`flex-1 py-2 text-xs font-semibold rounded-lg transition-all flex items-center justify-center gap-2 ${
            mode === "register"
              ? "bg-sky-600 text-white shadow-md shadow-sky-600/30"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <UserPlus className="h-3.5 w-3.5" /> Register
        </button>
      </div>

      {error && (
        <div className="p-3 mb-6 rounded-lg bg-rose-950/60 border border-rose-800/60 text-rose-300 text-xs flex items-center gap-2">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        {mode === "register" && (
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1">
              Full Name
            </label>
            <input
              type="text"
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="e.g. Alex Johnson"
              className="w-full px-4 py-2.5 rounded-xl bg-slate-800/60 border border-slate-700/60 text-white placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-transparent transition-all"
            />
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-slate-300 mb-1">
            Email Address
          </label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="student@university.edu"
            className="w-full px-4 py-2.5 rounded-xl bg-slate-800/60 border border-slate-700/60 text-white placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-transparent transition-all"
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-300 mb-1">
            Password
          </label>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            className="w-full px-4 py-2.5 rounded-xl bg-slate-800/60 border border-slate-700/60 text-white placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-transparent transition-all"
          />
        </div>

        <button
          type="submit"
          disabled={isLoading}
          className="w-full mt-2 py-3 px-4 rounded-xl bg-gradient-to-r from-sky-500 to-blue-600 hover:from-sky-400 hover:to-blue-500 text-white font-semibold text-sm shadow-lg shadow-sky-500/25 transition-all flex items-center justify-center gap-2 disabled:opacity-50"
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
    </div>
  );
}
