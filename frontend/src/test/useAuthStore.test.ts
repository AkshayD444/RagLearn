import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: {
    getMe: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
  },
}));

import { api } from "@/lib/api";
import { useAuthStore } from "@/store/useAuthStore";

const getMe = vi.mocked(api.getMe);
const login = vi.mocked(api.login);
const register = vi.mocked(api.register);

const user = {
  id: "22222222-2222-4222-8222-222222222222",
  email: "student@example.com",
  full_name: "Ada Lovelace",
  created_at: "2026-01-01T00:00:00Z",
};

const auth = {
  access_token: "jwt-token-value",
  token_type: "bearer",
  user,
};

function resetStore() {
  useAuthStore.setState({ user: null, token: null, isLoading: false, error: null });
}

beforeEach(() => {
  window.localStorage.clear();
  resetStore();
  getMe.mockReset();
  login.mockReset();
  register.mockReset();
});

afterEach(() => {
  resetStore();
});

describe("useAuthStore.initialize", () => {
  it("does nothing when no token is stored", async () => {
    await useAuthStore.getState().initialize();

    expect(getMe).not.toHaveBeenCalled();
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().isLoading).toBe(false);
  });

  it("hydrates the user from a stored token", async () => {
    window.localStorage.setItem("access_token", "jwt-token-value");
    getMe.mockResolvedValue(user);

    await useAuthStore.getState().initialize();

    expect(getMe).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().user).toEqual(user);
    expect(useAuthStore.getState().token).toBe("jwt-token-value");
    expect(useAuthStore.getState().isLoading).toBe(false);
  });

  it("clears an expired token when the session lookup fails", async () => {
    window.localStorage.setItem("access_token", "stale-token");
    getMe.mockRejectedValue(new Error("Not authenticated"));

    await useAuthStore.getState().initialize();

    expect(window.localStorage.getItem("access_token")).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().isLoading).toBe(false);
  });
});

describe("useAuthStore.login", () => {
  it("persists the token and user on success", async () => {
    login.mockResolvedValue(auth);

    await useAuthStore.getState().login({ email: user.email, password: "password123" });

    expect(login).toHaveBeenCalledWith({ email: user.email, password: "password123" });
    expect(window.localStorage.getItem("access_token")).toBe("jwt-token-value");
    expect(useAuthStore.getState().user).toEqual(user);
    expect(useAuthStore.getState().token).toBe("jwt-token-value");
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
    expect(window.localStorage.getItem("access_token")).toBeNull();
  });
});

describe("useAuthStore.register", () => {
  it("persists the token and user on success", async () => {
    register.mockResolvedValue(auth);

    await useAuthStore
      .getState()
      .register({ email: user.email, full_name: user.full_name, password: "password123" });

    expect(window.localStorage.getItem("access_token")).toBe("jwt-token-value");
    expect(useAuthStore.getState().user).toEqual(user);
  });

  it("surfaces a duplicate-email error", async () => {
    register.mockRejectedValue(new Error("Email already registered"));

    await expect(
      useAuthStore
        .getState()
        .register({ email: user.email, full_name: user.full_name, password: "password123" }),
    ).rejects.toThrow("Email already registered");

    expect(useAuthStore.getState().error).toBe("Email already registered");
  });
});

describe("useAuthStore.logout", () => {
  it("removes the token and clears the session", () => {
    window.localStorage.setItem("access_token", "jwt-token-value");
    useAuthStore.setState({ user, token: "jwt-token-value", error: "stale" });

    useAuthStore.getState().logout();

    expect(window.localStorage.getItem("access_token")).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().error).toBeNull();
  });
});

describe("useAuthStore.clearError", () => {
  it("clears the error without touching the session", () => {
    useAuthStore.setState({ user, token: "jwt-token-value", error: "boom" });

    useAuthStore.getState().clearError();

    expect(useAuthStore.getState().error).toBeNull();
    expect(useAuthStore.getState().user).toEqual(user);
    expect(useAuthStore.getState().token).toBe("jwt-token-value");
  });
});
