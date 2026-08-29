import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../src/auth/AuthContext";
import { LibraryPage } from "../src/pages/LibraryPage";

const authenticated: AuthContextValue = {
  status: "authenticated",
  config: null,
  accessToken: "access-token",
  login: vi.fn(),
  signup: vi.fn(),
  confirmRegistration: vi.fn(),
  resendRegistration: vi.fn(),
  localLogin: vi.fn(),
  completeCallback: vi.fn(),
  logout: vi.fn(),
};

describe("LibraryPage", () => {
  it("mounts accessible authenticated navigation and the existing shared-client panels", () => {
    render(
      <AuthContext.Provider value={authenticated}>
        <LibraryPage />
      </AuthContext.Provider>,
    );

    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByText("From field capture to searchable evidence")).toBeInTheDocument();
    expect(screen.getByText("Upload, review and organise wildlife observations in one secure workspace.")).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "Application sections" });
    expect(navigation).toHaveTextContent("Upload");
    expect(navigation).toHaveTextContent("Library");
    expect(navigation).toHaveTextContent("Search");
    expect(navigation).toHaveTextContent("Manage");
    expect(navigation).toHaveTextContent("Subscriptions");
    expect(screen.getByRole("heading", { name: "Upload wildlife media" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Before you upload" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your media library" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Search wildlife media" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Media management" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Tag subscriptions" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });
});
