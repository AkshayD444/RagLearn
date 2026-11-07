import { create } from 'zustand';
import { api, setUnauthorizedListener, User } from '@/lib/api';

export interface AuthState {
  user: User | null;
  /**
   * B2: whether the browser holds a session cookie.
   *
   * This replaces the old `token: string | null`, which every page used as a
   * proxy for "signed in". The token itself is no longer visible to this
   * process at all -- it lives in an HttpOnly cookie the browser attaches on its
   * own -- so there is nothing here to expose and nothing to persist.
   */
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  initialize: () => Promise<void>;
  login: (payload: { email: string; password: string }) => Promise<void>;
  register: (payload: { email: string; full_name: string; password: string }) => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
}

const clearSession = (set: (partial: Partial<AuthState>) => void) =>
  set({ user: null, isAuthenticated: false, isLoading: false, error: null });

// Counts successful sign-ins. `initialize()` reads it before it starts and
// checks it again before clearing anything, so a slow `/me` lookup cannot sign
// the student out of a session they completed in the meantime.
//
// This guard used to be "is the token I asked about still the stored one?", which
// no longer has an answer once the token lives in a cookie this module cannot
// see.
let sessionGeneration = 0;

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,
  isLoading: false,
  error: null,

  initialize: async () => {
    const generationAtStart = sessionGeneration;
    set({ isLoading: true, error: null });
    try {
      // No token to check first: asking whether a token exists was only ever
      // necessary because the token used to live in localStorage. The session
      // cookie is invisible from here, so /auth/me is the only honest question.
      const user = await api.getMe();
      set({ user, isAuthenticated: true, isLoading: false });
    } catch {
      // A failed lookup means there is no usable session -- unless the student
      // signed in while it was in flight, in which case this answer is stale and
      // clearing on it would log them straight back out.
      if (sessionGeneration !== generationAtStart) return;
      // Nothing to clean up locally either: there is nothing stored locally.
      clearSession(set);
    }
  },

  login: async (payload) => {
    set({ isLoading: true, error: null });
    try {
      // The response body still carries a token for API clients; this app does
      // not read it. The session is the cookie the browser just stored.
      const data = await api.login(payload);
      sessionGeneration += 1;
      set({ user: data.user, isAuthenticated: true, isLoading: false });
    } catch (err: any) {
      set({ error: err.message || 'Login failed', isLoading: false });
      throw err;
    }
  },

  register: async (payload) => {
    set({ isLoading: true, error: null });
    try {
      const data = await api.register(payload);
      sessionGeneration += 1;
      set({ user: data.user, isAuthenticated: true, isLoading: false });
    } catch (err: any) {
      set({ error: err.message || 'Registration failed', isLoading: false });
      throw err;
    }
  },

  logout: async () => {
    // Ask the server to withdraw the session first, so signing out actually
    // ends it rather than only forgetting it.
    //
    // The failure is swallowed deliberately. Signing out is teardown: a user who
    // clicks it while offline must still end up signed out on this device, and a
    // rejected promise here would leave them staring at a session they cannot
    // leave. The server-side session expires on its own regardless.
    try {
      await api.logout();
    } catch {
      // Intentionally ignored.
    } finally {
      sessionGeneration += 1;
      clearSession(set);
    }
  },

  clearError: () => set({ error: null }),
}));

// When the API reports that the session is unrecoverable -- the refresh token
// expired or was revoked -- drop the signed-in state so the UI falls back to the
// sign-in screen instead of continuing to render a session the server has
// forgotten.
if (typeof window !== 'undefined') {
  setUnauthorizedListener(() => {
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: false });
  });
}
