import { createContext, type PropsWithChildren, useContext, useEffect, useMemo, useState } from "react";

import {
  type AuthConfig,
  AUTHENTICATION_REQUIRED_EVENT,
  BrowserAuthClient,
  type CognitoSignupResult,
  type SignupDetails,
} from "./authClient";

export type AuthStatus = "loading" | "anonymous" | "authenticated";

export interface AuthContextValue {
  status: AuthStatus;
  config: AuthConfig | null;
  accessToken: string | null;
  /** Undefined is retained for lightweight component fixtures that do not use profile gating. */
  profileComplete?: boolean | null;
  profileError?: string | null;
  refreshProfile?: () => Promise<void>;
  login(provider?: string): Promise<void>;
  signup(details: SignupDetails): Promise<CognitoSignupResult>;
  confirmRegistration(email: string, code: string): Promise<void>;
  resendRegistration(email: string): Promise<string | undefined>;
  localLogin(): Promise<void>;
  completeCallback(search: string): Promise<void>;
  logout(): void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export function AuthProvider({ children }: PropsWithChildren) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [profileComplete, setProfileComplete] = useState<boolean | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);

  const client = useMemo(() => (config ? new BrowserAuthClient(config) : null), [config]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_BASE_URL}/auth/config`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Authentication configuration is unavailable");
        return (await response.json()) as AuthConfig;
      })
      .then(setConfig)
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setStatus("anonymous");
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!client) return;
    void client
      .restoreSession()
      .then((token) => {
        setAccessToken(token);
        setProfileComplete(null);
        setProfileError(null);
        setStatus(token ? "authenticated" : "anonymous");
      })
      .catch(() => {
        setAccessToken(null);
        setProfileComplete(null);
        setProfileError(null);
        setStatus("anonymous");
      });
  }, [client]);

  useEffect(() => {
    const requireAuthentication = () => {
      client?.logout(false);
      setAccessToken(null);
      setProfileComplete(null);
      setProfileError(null);
      setStatus("anonymous");
    };
    window.addEventListener(AUTHENTICATION_REQUIRED_EVENT, requireAuthentication);
    return () => window.removeEventListener(AUTHENTICATION_REQUIRED_EVENT, requireAuthentication);
  }, [client]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      config,
      accessToken,
      profileComplete,
      profileError,
      login: async (provider?: string) => {
        if (!client) throw new Error("Authentication is not configured");
        await client.beginLogin(provider);
      },
      signup: async (details: SignupDetails) => {
        if (!client) throw new Error("Authentication is not configured");
        return client.signUp(details);
      },
      confirmRegistration: async (email: string, code: string) => {
        if (!client) throw new Error("Authentication is not configured");
        await client.confirmSignUp(email, code);
      },
      resendRegistration: async (email: string) => {
        if (!client) throw new Error("Authentication is not configured");
        return client.resendConfirmationCode(email);
      },
      localLogin: async () => {
        if (!client || !config?.local_auth_enabled) throw new Error("Local authentication is disabled");
        const response = await fetch(`${API_BASE_URL}/auth/local-token`, { method: "POST" });
        if (!response.ok) throw new Error("Local authentication failed");
        const payload = (await response.json()) as { access_token?: string; expires_in?: number };
        if (!payload.access_token || !payload.expires_in) throw new Error("Local token response is invalid");
        client.storeLocalAccessToken(payload.access_token, payload.expires_in);
        setAccessToken(payload.access_token);
        setProfileComplete(null);
        setProfileError(null);
        setStatus("authenticated");
      },
      completeCallback: async (search: string) => {
        if (!client) throw new Error("Authentication is not configured");
        const token = await client.completeCallback(search);
        setAccessToken(token);
        setProfileComplete(null);
        setProfileError(null);
        setStatus("authenticated");
      },
      refreshProfile: async () => {
        if (!accessToken) throw new Error("Authentication is required");
        try {
          const response = await fetch(`${API_BASE_URL}/profile`, {
            headers: { Authorization: `Bearer ${accessToken}` },
          });
          if (!response.ok) throw new Error(`Profile request failed (${response.status})`);
          const payload = (await response.json()) as { complete?: unknown };
          if (typeof payload.complete !== "boolean") throw new Error("Profile response is invalid");
          setProfileComplete(payload.complete);
          setProfileError(null);
        } catch (error) {
          const message = error instanceof Error ? error.message : "Profile could not be loaded";
          setProfileError(message);
          throw error;
        }
      },
      logout: () => {
        client?.logout();
        setAccessToken(null);
        setProfileComplete(null);
        setProfileError(null);
        setStatus("anonymous");
      },
    }),
    [accessToken, client, config, profileComplete, profileError, status],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
