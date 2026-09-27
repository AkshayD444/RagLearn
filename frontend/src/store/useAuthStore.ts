import { create } from 'zustand';
import { api, User } from '@/lib/api';

export interface AuthState {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  error: string | null;
  initialize: () => Promise<void>;
  login: (payload: { email: string; password: string }) => Promise<void>;
  register: (payload: { email: string; full_name: string; password: string }) => Promise<void>;
  logout: () => void;
  clearError: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: typeof window !== 'undefined' ? localStorage.getItem('access_token') : null,
  isLoading: false,
  error: null,

  initialize: async () => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
    if (!token) {
      // Explicit, rather than relying on the initial value: initialize() is
      // also called after a sign-in attempt, and a caller that awaits it needs
      // "settled" to mean the same thing whether or not a token existed.
      set({ user: null, token: null, isLoading: false });
      return;
    }

    set({ isLoading: true, token });
    try {
      const user = await api.getMe();
      set({ user, isLoading: false });
    } catch (err: any) {
      // Only discard the token this call was made with. If the student signed in
      // while /me was in flight, the stored token has moved on and clearing it
      // would log them straight back out.
      const current =
        typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
      if (current === token) {
        if (typeof window !== 'undefined') localStorage.removeItem('access_token');
        set({ user: null, token: null, isLoading: false });
      }
    }
  },

  login: async (payload) => {
    set({ isLoading: true, error: null });
    try {
      const data = await api.login(payload);
      if (typeof window !== 'undefined') localStorage.setItem('access_token', data.access_token);
      set({ user: data.user, token: data.access_token, isLoading: false });
    } catch (err: any) {
      set({ error: err.message || 'Login failed', isLoading: false });
      throw err;
    }
  },

  register: async (payload) => {
    set({ isLoading: true, error: null });
    try {
      const data = await api.register(payload);
      if (typeof window !== 'undefined') localStorage.setItem('access_token', data.access_token);
      set({ user: data.user, token: data.access_token, isLoading: false });
    } catch (err: any) {
      set({ error: err.message || 'Registration failed', isLoading: false });
      throw err;
    }
  },

  logout: () => {
    if (typeof window !== 'undefined') localStorage.removeItem('access_token');
    set({ user: null, token: null, error: null });
  },

  clearError: () => set({ error: null }),
}));
