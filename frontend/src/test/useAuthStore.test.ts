import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: {
    getMe: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
  },
  setUnauthorizedListener: vi.fn(),
}));

import { api, setUnauthorizedListener } from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";

// Captured at module scope on purpose. The store registers its listener when it
// is imported, which happens before any beforeEach reset -- reading it inside a
// test would find the mock already cleared.
const registeredListener = vi.mocked(setUnauthorizedListener).mock.calls[0]?.[0];

const getMe = vi.mocked(api.getMe);
const login = vi.mocked(api.login);
const register = vi.mocked(api.register);
const logout = vi.mocked(api.logout);

const user = {
  id: "22222222-2222-4222-8222-222222222222",
  email: "student@example.com",
  full_name: "Ada Lovelace",
  created_at: "2026-01-01T00:00:00Z",
};

const auth = {
  access_token: "jwt-token-value",
  refresh_token: "refresh-token-value",
  expires_in: 900,
  token_type: "bearer",
  user,
};

function resetStore() {
  useAuthStore.setState({
    user: null,
    isAuthenticated: false,
    isLoading: false,
    error: null,
  });
}

beforeEach(() => {
  // B2: the store must not depend on anything left in web storage, so these
  // tests clear it and then assert it stays clear.
  window.localStorage.clear();
  document.cookie.split(";").forEach((c) => {
    const name = c.split("=")[0].trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  });
  resetStore();
  getMe.mockReset();
  login.mockReset();
  register.mockReset();
  logout.mockReset();
  logout.mockResolvedValue(undefined);
});

afterEach(() => {
  resetStore();
});

describe("B2: nothing is persisted in web storage", () => {
  it("never writes a token to localStorage, even after signing in", async () => {
    login.mockResolvedValue(auth);

    await useAuthStore.getState().login({ email: user.email, password: "password123" });

    expect(window.localStorage.getItem("access_token")).toBeNull();
    expect(window.localStorage.length).toBe(0);
    expect(window.localStorage.getItem("refresh_token")).toBeNull();
  });

  it("keeps no token in its own state either", async () => {
    login.mockResolvedValue(auth);

    await useAuthStore.getState().login({ email: user.email, password: "password123" });

    const state = useAuthStore.getState();
    expect(JSON.stringify(state)).not.toContain(auth.access_token);
    expect(JSON.stringify(state)).not.toContain(auth.refresh_token);
    expect(state.isAuthenticated).toBe(true);
  });

  it("does not read a previously stored token on startup", async () => {
    window.localStorage.setItem("access_token", "leftover-from-an-old-build");
    getMe.mockRejectedValue(new Error("Not authenticated"));

    await useAuthStore.getState().initialize();

    // A stale value from a previous build must not be able to re-establish a
    // session; the server is the only authority on whether one exists.
    expect(getMe).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

describe("useAuthStore.initialize", () => {
  it("settles as signed out when there is no session", async () => {
    getMe.mockRejectedValue(new Error("Not authenticated"));

    await useAuthStore.getState().initialize();

    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(useAuthStore.getState().isLoading).toBe(false);
  });

  it("hydrates the user from the session cookie", async () => {
    getMe.mockResolvedValue(user);

    await useAuthStore.getState().initialize();

    expect(getMe).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().user).toEqual(user);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().isLoading).toBe(false);
  });

  it("settles out of the loading state even when the lookup fails", async () => {
    useAuthStore.setState({ isLoading: true });
    getMe.mockRejectedValue(new Error("Not authenticated"));

    await useAuthStore.getState().initialize();

    expect(useAuthStore.getState().isLoading).toBe(false);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("keeps a session that was established while the lookup was in flight", async () => {
    // The student signs in with a different account before /me settles, so the
    // stale request must not sign them back out.
    login.mockResolvedValue(auth);
    let rejectLookup: (err: Error) => void = () => {};
    getMe.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectLookup = reject;
        }),
    );

    const initializing = useAuthStore.getState().initialize();
    await useAuthStore.getState().login({ email: "other@example.com", password: "password123" });
    rejectLookup(new Error("Not authenticated"));
    await initializing;

    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().user).toEqual(user);
  });
});

describe("useAuthStore.login", () => {
  it("records the user and marks the session active", async () => {
    login.mockResolvedValue(auth);

    await useAuthStore.getState().login({ email: user.email, password: "password123" });

    expect(login).toHaveBeenCalledWith({ email: user.email, password: "password123" });
    expect(useAuthStore.getState().user).toEqual(user);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().error).toBeNull();
    expect(useAuthStore.getState().isLoading).toBe(false);
  });

  it("surfaces the server message and rethrows on failure", async () => {
    login.mockRejectedValue(new Error("Incorrect email or password"));

    await expect(
      useAuthStore.getState().login({ email: user.email, password: "nope" }),
    ).rejects.toThrow("Incorrect email or password");

    expect(useAuthStore.getState().error).toBe("Incorrect email or password");
    expect(useAuthStore.getState().isLoading).toBe(false);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

describe("useAuthStore.register", () => {
  it("records the user and marks the session active", async () => {
    register.mockResolvedValue(auth);

    await useAuthStore
      .getState()
      .register({ email: user.email, full_name: user.full_name, password: "password123" });

    expect(useAuthStore.getState().user).toEqual(user);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(window.localStorage.length).toBe(0);
  });

  it("surfaces a duplicate-email error", async () => {
    register.mockRejectedValue(new Error("Email already registered"));

    await expect(
      useAuthStore
        .getState()
        .register({ email: user.email, full_name: user.full_name, password: "password123" }),
    ).rejects.toThrow("Email already registered");

    expect(useAuthStore.getState().error).toBe("Email already registered");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

describe("useAuthStore.logout", () => {
  it("asks the server to end the session, then clears local state", async () => {
    useAuthStore.setState({ user, isAuthenticated: true, error: "stale" });

    await useAuthStore.getState().logout();

    // Revocation is the point: without the call, signing out would only forget
    // the session rather than ending it.
    expect(logout).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(useAuthStore.getState().error).toBeNull();
  });

  it("clears local state even when the server cannot be reached", async () => {
    logout.mockRejectedValue(new Error("Network down"));
    useAuthStore.setState({ user, isAuthenticated: true });

    await useAuthStore.getState().logout();

    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

describe("useAuthStore.clearError", () => {
  it("clears the error without touching the session", () => {
    useAuthStore.setState({ user, isAuthenticated: true, error: "boom" });

    useAuthStore.getState().clearError();

    expect(useAuthStore.getState().error).toBeNull();
    expect(useAuthStore.getState().user).toEqual(user);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });
});

describe("unrecoverable sessions", () => {
  it("subscribes the store to the api client's unauthorized signal", () => {
    expect(typeof registeredListener).toBe("function");
  });

  it("drops the signed-in state when the session is withdrawn server-side", () => {
    useAuthStore.setState({ user, isAuthenticated: true });

    registeredListener!();

    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
