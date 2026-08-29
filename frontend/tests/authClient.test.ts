import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BrowserAuthClient,
  buildAuthorizeUrl,
  buildLogoutUrl,
  buildSignupUrl,
  validateCallback,
} from "../src/auth/authClient";

const config = {
  region: "ap-southeast-2",
  user_pool_id: "ap-southeast-2_example",
  app_client_id: "client-id",
  oauth_domain: "https://example.auth.ap-southeast-2.amazoncognito.com",
  redirect_uri: "http://localhost:5173/auth/callback",
  external_providers: ["Google", "Microsoft"],
};

describe("Cognito hosted UI PKCE", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("builds an authorization-code URL with PKCE and optional provider", () => {
    const url = new URL(buildAuthorizeUrl(config, "state-123", "challenge-456", "Google"));

    expect(url.pathname).toBe("/oauth2/authorize");
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("identity_provider")).toBe("Google");
    expect(url.searchParams.get("state")).toBe("state-123");
  });

  it("accepts a matching callback and rejects state mismatch", () => {
    expect(validateCallback("?code=abc&state=expected", "expected")).toEqual({ code: "abc" });
    expect(() => validateCallback("?code=abc&state=wrong", "expected")).toThrow("state");
  });

  it("builds a Cognito registration entry point", () => {
    const url = new URL(buildSignupUrl(config, "state", "challenge"));
    expect(url.pathname).toBe("/signup");
    expect(url.searchParams.get("response_type")).toBe("code");
  });

  it("uses the configured browser origin for Cognito logout", () => {
    const url = new URL(buildLogoutUrl(config));

    expect(url.pathname).toBe("/logout");
    expect(url.searchParams.get("client_id")).toBe("client-id");
    expect(url.searchParams.get("logout_uri")).toBe("http://localhost:5173");
  });

  it("refreshes an expired access token without exposing the refresh token", async () => {
    sessionStorage.setItem("pba.oauth.state", "expected");
    sessionStorage.setItem("pba.oauth.verifier", "verifier");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          access_token: "expired-access",
          refresh_token: "refresh-only-in-storage",
          expires_in: -1,
          token_type: "Bearer",
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ access_token: "fresh-access", expires_in: 3600, token_type: "Bearer" }),
      });
    vi.stubGlobal("fetch", fetchMock);
    const client = new BrowserAuthClient(config, sessionStorage);

    await client.completeCallback("?code=abc&state=expected");
    expect(await client.restoreSession()).toBe("fresh-access");

    const refreshRequest = fetchMock.mock.calls[1][1] as RequestInit;
    expect(refreshRequest.body?.toString()).toContain("grant_type=refresh_token");
    expect(refreshRequest.body?.toString()).toContain("refresh_token=refresh-only-in-storage");
  });

  it("registers email and name attributes through Cognito's public SignUp API", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        UserConfirmed: false,
        CodeDeliveryDetails: { Destination: "z***@example.com" },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new BrowserAuthClient(config, sessionStorage);

    await expect(client.signUp({
      email: " Researcher@Example.com ",
      password: "SecurePassword!2",
      givenName: " Ada ",
      familyName: " Lovelace ",
    })).resolves.toEqual({ userConfirmed: false, destination: "z***@example.com" });

    const [url, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://cognito-idp.ap-southeast-2.amazonaws.com/");
    expect((request.headers as Record<string, string>)["X-Amz-Target"]).toContain(".SignUp");
    expect(JSON.parse(request.body as string)).toEqual({
      ClientId: "client-id",
      Username: "researcher@example.com",
      Password: "SecurePassword!2",
      UserAttributes: [
        { Name: "email", Value: "researcher@example.com" },
        { Name: "given_name", Value: "Ada" },
        { Name: "family_name", Value: "Lovelace" },
      ],
    });
  });

  it("confirms and resends codes for an unfinished registration", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ CodeDeliveryDetails: { Destination: "z***@example.com" } }),
      });
    vi.stubGlobal("fetch", fetchMock);
    const client = new BrowserAuthClient(config, sessionStorage);

    await client.confirmSignUp(" Researcher@Example.com ", " 123456 ");
    await expect(client.resendConfirmationCode("Researcher@Example.com")).resolves.toBe("z***@example.com");

    expect((fetchMock.mock.calls[0][1].headers as Record<string, string>)["X-Amz-Target"]).toContain(".ConfirmSignUp");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toMatchObject({
      Username: "researcher@example.com",
      ConfirmationCode: "123456",
    });
    expect((fetchMock.mock.calls[1][1].headers as Record<string, string>)["X-Amz-Target"]).toContain(".ResendConfirmationCode");
  });

  it("turns Cognito error types into actionable stable errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ __type: "com.amazon.cognito#UsernameExistsException", message: "raw message" }),
    }));
    const client = new BrowserAuthClient(config, sessionStorage);

    const request = client.signUp({
      email: "researcher@example.com",
      password: "SecurePassword!2",
      givenName: "Ada",
      familyName: "Lovelace",
    });
    await expect(request).rejects.toMatchObject({
      code: "UsernameExistsException",
      message: expect.stringContaining("Confirm it"),
    });
  });
});
