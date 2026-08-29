import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../src/auth/AuthContext";
import { ProtectedRoute } from "../src/auth/ProtectedRoute";
import { ProfilePage } from "../src/pages/ProfilePage";

afterEach(() => {
  vi.restoreAllMocks();
});

function auth(overrides: Partial<AuthContextValue> = {}): AuthContextValue {
  return {
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
    ...overrides,
  };
}

describe("profile completion", () => {
  it("redirects an authenticated user with no name to the profile form", async () => {
    const refreshProfile = vi.fn().mockResolvedValue(undefined);
    render(
      <AuthContext.Provider value={auth({ profileComplete: false, refreshProfile })}>
        <MemoryRouter initialEntries={["/library"]}>
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route path="/library" element={<p>Library</p>} />
            </Route>
            <Route path="/profile" element={<p>Profile form</p>} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>,
    );
    expect(await screen.findByText("Profile form")).toBeInTheDocument();
  });

  it("saves both names and returns to the library", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ given_name: "Kai", family_name: "Lee", complete: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const refreshProfile = vi.fn().mockResolvedValue(undefined);
    render(
      <AuthContext.Provider value={auth({ refreshProfile })}>
        <MemoryRouter initialEntries={["/profile"]}>
          <Routes>
            <Route element={<ProtectedRoute requireProfile={false} />}>
              <Route path="/profile" element={<ProfilePage />} />
            </Route>
            <Route path="/library" element={<p>Library</p>} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>,
    );
    fireEvent.change(screen.getByLabelText("Given name"), { target: { value: "Kai" } });
    fireEvent.change(screen.getByLabelText("Family name"), { target: { value: "Lee" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
    await waitFor(() => expect(screen.getByText("Library")).toBeInTheDocument());
    expect(refreshProfile).toHaveBeenCalledOnce();
  });
});
