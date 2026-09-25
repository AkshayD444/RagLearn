import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/store/useAuthStore", () => ({
  useAuthStore: vi.fn(),
}));

import { useAuthStore } from "@/store/useAuthStore";
import { AuthModal } from "@/components/auth/AuthModal";

const useAuthStoreMock = vi.mocked(useAuthStore);

type AuthState = ReturnType<typeof useAuthStore>;

function mockAuth(overrides: Partial<AuthState> = {}) {
  const state = {
    user: null,
    token: null,
    isLoading: false,
    error: null,
    initialize: vi.fn(),
    login: vi.fn().mockResolvedValue(undefined),
    register: vi.fn().mockResolvedValue(undefined),
    logout: vi.fn(),
    clearError: vi.fn(),
    ...overrides,
  } as AuthState;
  useAuthStoreMock.mockReturnValue(state);
  return state;
}

beforeEach(() => {
  useAuthStoreMock.mockReset();
});

function emailInput(): HTMLInputElement {
  return screen.getByPlaceholderText("student@university.edu");
}

function passwordInput(): HTMLInputElement {
  return screen.getByPlaceholderText("••••••••");
}

describe("AuthModal login mode", () => {
  it("starts in login mode", () => {
    mockAuth();
    render(<AuthModal />);

    expect(screen.getByText("Welcome Back")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign In" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("e.g. Alex Johnson")).not.toBeInTheDocument();
  });

  it("signs in with the email and password", async () => {
    const state = mockAuth();
    render(<AuthModal />);

    fireEvent.change(emailInput(), { target: { value: "ada@example.com" } });
    fireEvent.change(passwordInput(), { target: { value: "password123" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));

    await waitFor(() =>
      expect(state.login).toHaveBeenCalledWith({
        email: "ada@example.com",
        password: "password123",
      }),
    );
    expect(state.clearError).toHaveBeenCalled();
  });

  it("notifies the parent after a successful sign-in", async () => {
    mockAuth();
    const onSuccess = vi.fn();
    render(<AuthModal onSuccess={onSuccess} />);

    fireEvent.change(emailInput(), { target: { value: "ada@example.com" } });
    fireEvent.change(passwordInput(), { target: { value: "password123" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
  });

  it("does not notify the parent when sign-in fails", async () => {
    mockAuth({ login: vi.fn().mockRejectedValue(new Error("Incorrect email or password")) });
    const onSuccess = vi.fn();
    render(<AuthModal onSuccess={onSuccess} />);

    fireEvent.change(emailInput(), { target: { value: "ada@example.com" } });
    fireEvent.change(passwordInput(), { target: { value: "nope" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));

    await waitFor(() => expect(screen.getByText("Incorrect email or password")).toBeInTheDocument());
    expect(onSuccess).not.toHaveBeenCalled();
  });
});

describe("AuthModal register mode", () => {
  it("switches to register mode and shows the full-name field", () => {
    const state = mockAuth();
    render(<AuthModal />);

    fireEvent.click(screen.getByRole("button", { name: "Register" }));

    expect(screen.getByText("Create Student Account")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("e.g. Alex Johnson")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create Account" })).toBeInTheDocument();
    expect(state.clearError).toHaveBeenCalled();
  });

  it("registers with the full name, email, and password", async () => {
    const state = mockAuth();
    render(<AuthModal />);

    fireEvent.click(screen.getByRole("button", { name: "Register" }));
    fireEvent.change(screen.getByPlaceholderText("e.g. Alex Johnson"), {
      target: { value: "Alex Johnson" },
    });
    fireEvent.change(emailInput(), { target: { value: "alex@example.com" } });
    fireEvent.change(passwordInput(), { target: { value: "password123" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Account" }));

    await waitFor(() =>
      expect(state.register).toHaveBeenCalledWith({
        email: "alex@example.com",
        full_name: "Alex Johnson",
        password: "password123",
      }),
    );
  });

  it("switches back to login mode", () => {
    mockAuth();
    render(<AuthModal />);

    fireEvent.click(screen.getByRole("button", { name: "Register" }));
    fireEvent.click(screen.getByRole("button", { name: "Login" }));

    expect(screen.getByText("Welcome Back")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("e.g. Alex Johnson")).not.toBeInTheDocument();
  });

  it("surfaces a duplicate-email error", async () => {
    mockAuth({ register: vi.fn().mockRejectedValue(new Error("Email already registered")) });
    render(<AuthModal />);

    fireEvent.click(screen.getByRole("button", { name: "Register" }));
    fireEvent.change(screen.getByPlaceholderText("e.g. Alex Johnson"), { target: { value: "Alex" } });
    fireEvent.change(emailInput(), { target: { value: "alex@example.com" } });
    fireEvent.change(passwordInput(), { target: { value: "password123" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Account" }));

    expect(await screen.findByText("Email already registered")).toBeInTheDocument();
  });
});

describe("AuthModal form constraints", () => {
  it("marks every credential field as required", () => {
    mockAuth();
    render(<AuthModal />);

    expect(emailInput()).toBeRequired();
    expect(passwordInput()).toBeRequired();
    expect(emailInput()).toHaveAttribute("type", "email");
  });

  it("disables the submit button while the request is in flight", () => {
    mockAuth({ isLoading: true });
    const { container } = render(<AuthModal />);

    const submit = container.querySelector('button[type="submit"]') as HTMLButtonElement;
    expect(submit).toBeDisabled();
  });
});
