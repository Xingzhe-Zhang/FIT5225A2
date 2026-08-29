import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext, type AuthContextValue } from "../../src/auth/AuthContext";
import {
  SubscriptionPanel,
  type SubscriptionClient,
  type SubscriptionView,
} from "../../src/subscriptions/SubscriptionPanel";


const SUBSCRIPTION: SubscriptionView = {
  subscription_id: "11111111-1111-4111-8111-111111111111",
  email: "researcher@example.com",
  tags: ["dingo", "wombat"],
  status: "active",
  version: 1,
};


afterEach(cleanup);


function authValue(): AuthContextValue {
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
  };
}


function client(overrides: Partial<SubscriptionClient> = {}): SubscriptionClient {
  return {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue(SUBSCRIPTION),
    update: vi.fn().mockResolvedValue({ ...SUBSCRIPTION, version: 2 }),
    delete: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}


function renderPanel(api: SubscriptionClient) {
  render(
    <AuthContext.Provider value={authValue()}>
      <SubscriptionPanel client={api} />
    </AuthContext.Provider>,
  );
}


describe("SubscriptionPanel", () => {
  it("loads with the shared token and shows accessible empty state", async () => {
    const api = client();
    renderPanel(api);

    expect(screen.getByRole("status")).toHaveTextContent("Loading subscriptions");
    expect(await screen.findByText("No tag subscriptions yet.")).toBeInTheDocument();
    expect(api.list).toHaveBeenCalledWith("access-token");
  });

  it("shows a list failure as an alert", async () => {
    renderPanel(client({ list: vi.fn().mockRejectedValue(new Error("service unavailable")) }));

    expect(await screen.findByRole("alert")).toHaveTextContent("service unavailable");
  });

  it("creates a subscription with normalized unique tags", async () => {
    const api = client();
    renderPanel(api);
    await screen.findByText("No tag subscriptions yet.");

    fireEvent.change(screen.getByLabelText("Notification email"), {
      target: { value: "researcher@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Watched tags"), {
      target: { value: " Dingo, wombat, dingo " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create subscription" }));

    await waitFor(() =>
      expect(api.create).toHaveBeenCalledWith(
        "researcher@example.com",
        ["dingo", "wombat"],
        "access-token",
      ),
    );
    expect(await screen.findByText("dingo, wombat")).toBeInTheDocument();
  });

  it("updates using the displayed version", async () => {
    const api = client({ list: vi.fn().mockResolvedValue([SUBSCRIPTION]) });
    renderPanel(api);
    await screen.findByText("dingo, wombat");
    expect(screen.getByText("Active")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit researcher@example.com" }));
    expect(screen.getByText("Editing researcher@example.com")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Watched tags"), { target: { value: "cassowary" } });
    fireEvent.click(screen.getByRole("button", { name: "Update subscription" }));

    await waitFor(() =>
      expect(api.update).toHaveBeenCalledWith(
        SUBSCRIPTION.subscription_id,
        SUBSCRIPTION.email,
        ["cassowary"],
        1,
        "access-token",
      ),
    );
  });

  it("requires confirmation before deleting", async () => {
    const api = client({ list: vi.fn().mockResolvedValue([SUBSCRIPTION]) });
    renderPanel(api);
    await screen.findByText("dingo, wombat");
    fireEvent.click(screen.getByRole("button", { name: "Delete researcher@example.com" }));

    expect(screen.getByRole("dialog", { name: "Confirm subscription deletion" })).toBeInTheDocument();
    expect(api.delete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await waitFor(() =>
      expect(api.delete).toHaveBeenCalledWith(SUBSCRIPTION.subscription_id, "access-token"),
    );
    expect(await screen.findByText("No tag subscriptions yet.")).toBeInTheDocument();
  });
});
