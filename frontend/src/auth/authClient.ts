export interface AuthConfig {
  region: string;
  user_pool_id: string;
  app_client_id: string;
  oauth_domain: string;
  redirect_uri: string;
  external_providers: string[];
  local_auth_enabled?: boolean;
}

interface TokenResponse {
  access_token: string;
  id_token?: string;
  refresh_token?: string;
  expires_in: number;
  token_type: string;
}

export interface SignupDetails {
  email: string;
  password: string;
  givenName: string;
  familyName: string;
}

export interface CognitoSignupResult {
  userConfirmed: boolean;
  destination?: string;
}

interface CognitoDeliveryDetails {
  Destination?: string;
}

interface CognitoSignupResponse {
  UserConfirmed?: boolean;
  CodeDeliveryDetails?: CognitoDeliveryDetails;
}

interface CognitoErrorPayload {
  __type?: string;
  message?: string;
}

const COGNITO_ERROR_MESSAGES: Record<string, string> = {
  CodeMismatchException: "The verification code is incorrect",
  ExpiredCodeException: "The verification code has expired. Request a new code and try again",
  InvalidParameterException: "The registration details are invalid",
  InvalidPasswordException: "The password does not meet the security requirements",
  LimitExceededException: "Too many attempts. Wait a moment before trying again",
  NotAuthorizedException: "This account cannot be confirmed with that code",
  TooManyFailedAttemptsException: "Too many incorrect attempts. Request a new code later",
  TooManyRequestsException: "Too many requests. Wait a moment before trying again",
  UserNotFoundException: "No registration was found for this email address",
  UsernameExistsException: "An account with this email already exists. Confirm it or return to sign in",
};

export class CognitoAuthError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "CognitoAuthError";
  }
}

const STATE_KEY = "pba.oauth.state";
const VERIFIER_KEY = "pba.oauth.verifier";
const NONCE_KEY = "pba.oauth.nonce";
const TOKEN_KEY = "pba.auth.tokens";
export const AUTHENTICATION_REQUIRED_EVENT = "pba:authentication-required";

function base64Url(bytes: Uint8Array): string {
  let raw = "";
  bytes.forEach((byte) => {
    raw += String.fromCharCode(byte);
  });
  return btoa(raw).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function randomValue(size = 32): string {
  return base64Url(crypto.getRandomValues(new Uint8Array(size)));
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

export function buildAuthorizeUrl(
  config: AuthConfig,
  state: string,
  challenge: string,
  provider?: string,
  nonce?: string,
): string {
  const url = new URL("/oauth2/authorize", config.oauth_domain);
  url.searchParams.set("client_id", config.app_client_id);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("redirect_uri", config.redirect_uri);
  url.searchParams.set("state", state);
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  if (nonce) {
    url.searchParams.set("nonce", nonce);
  }
  if (provider) {
    url.searchParams.set("identity_provider", provider);
  }
  return url.toString();
}

export function buildSignupUrl(
  config: AuthConfig,
  state: string,
  challenge: string,
  nonce?: string,
): string {
  const url = new URL(buildAuthorizeUrl(config, state, challenge, undefined, nonce));
  url.pathname = "/signup";
  return url.toString();
}

export function buildLogoutUrl(config: AuthConfig): string {
  const url = new URL("/logout", config.oauth_domain);
  url.searchParams.set("client_id", config.app_client_id);
  // Cognito requires an exact match with the app client's configured logout URL.
  // Terraform registers the browser origin, while the callback includes /auth/callback.
  url.searchParams.set("logout_uri", new URL(config.redirect_uri).origin);
  return url.toString();
}

export function validateCallback(search: string, expectedState: string | null): { code: string } {
  const params = new URLSearchParams(search);
  const oauthError = params.get("error");
  if (oauthError) {
    throw new Error(params.get("error_description") ?? oauthError);
  }
  const state = params.get("state");
  const code = params.get("code");
  if (!expectedState || !state || state !== expectedState) {
    throw new Error("OAuth state validation failed");
  }
  if (!code) {
    throw new Error("Authorization code is missing");
  }
  return { code };
}

export class BrowserAuthClient {
  constructor(
    private readonly config: AuthConfig,
    private readonly storage: Storage = window.sessionStorage,
  ) {}

  getAccessToken(): string | null {
    const parsed = this.readTokens();
    return parsed?.access_token && parsed.expires_at > Date.now() ? parsed.access_token : null;
  }

  private async cognitoRequest<T>(operation: string, body: Record<string, unknown>): Promise<T> {
    const response = await fetch(`https://cognito-idp.${this.config.region}.amazonaws.com/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": `AWSCognitoIdentityProviderService.${operation}`,
      },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      let payload: CognitoErrorPayload = {};
      try {
        payload = (await response.json()) as CognitoErrorPayload;
      } catch {
        // Cognito normally returns JSON errors, but callers still receive a stable message if it does not.
      }
      const code = payload.__type?.split("#").at(-1) ?? "CognitoRequestFailed";
      throw new CognitoAuthError(
        code,
        COGNITO_ERROR_MESSAGES[code] ?? payload.message ?? "The authentication request failed",
      );
    }
    return (await response.json()) as T;
  }

  async signUp(details: SignupDetails): Promise<CognitoSignupResult> {
    const email = details.email.trim().toLowerCase();
    const response = await this.cognitoRequest<CognitoSignupResponse>("SignUp", {
      ClientId: this.config.app_client_id,
      Username: email,
      Password: details.password,
      UserAttributes: [
        { Name: "email", Value: email },
        { Name: "given_name", Value: details.givenName.trim() },
        { Name: "family_name", Value: details.familyName.trim() },
      ],
    });
    return {
      userConfirmed: response.UserConfirmed === true,
      destination: response.CodeDeliveryDetails?.Destination,
    };
  }

  async confirmSignUp(email: string, code: string): Promise<void> {
    await this.cognitoRequest<Record<string, never>>("ConfirmSignUp", {
      ClientId: this.config.app_client_id,
      Username: email.trim().toLowerCase(),
      ConfirmationCode: code.trim(),
    });
  }

  async resendConfirmationCode(email: string): Promise<string | undefined> {
    const response = await this.cognitoRequest<{ CodeDeliveryDetails?: CognitoDeliveryDetails }>(
      "ResendConfirmationCode",
      {
        ClientId: this.config.app_client_id,
        Username: email.trim().toLowerCase(),
      },
    );
    return response.CodeDeliveryDetails?.Destination;
  }

  storeLocalAccessToken(accessToken: string, expiresIn: number): void {
    this.storage.setItem(
      TOKEN_KEY,
      JSON.stringify({
        access_token: accessToken,
        expires_in: expiresIn,
        expires_at: Date.now() + expiresIn * 1000,
        token_type: "Bearer",
      }),
    );
  }

  private readTokens(): (TokenResponse & { expires_at: number }) | null {
    const raw = this.storage.getItem(TOKEN_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as TokenResponse & { expires_at: number };
    } catch {
      this.storage.removeItem(TOKEN_KEY);
      return null;
    }
  }

  private async beginAuthorization(provider?: string, signup = false): Promise<void> {
    if (provider && !this.config.external_providers.includes(provider)) {
      throw new Error("Identity provider is not enabled");
    }
    const state = randomValue();
    const verifier = randomValue(64);
    const nonce = randomValue();
    this.storage.setItem(STATE_KEY, state);
    this.storage.setItem(VERIFIER_KEY, verifier);
    this.storage.setItem(NONCE_KEY, nonce);
    const challenge = await challengeFor(verifier);
    const target = signup
      ? buildSignupUrl(this.config, state, challenge, nonce)
      : buildAuthorizeUrl(this.config, state, challenge, provider, nonce);
    window.location.assign(target);
  }

  async beginLogin(provider?: string): Promise<void> {
    await this.beginAuthorization(provider);
  }

  async beginSignup(): Promise<void> {
    await this.beginAuthorization(undefined, true);
  }

  async completeCallback(search: string): Promise<string> {
    const expectedState = this.storage.getItem(STATE_KEY);
    const expectedNonce = this.storage.getItem(NONCE_KEY);
    const verifier = this.storage.getItem(VERIFIER_KEY);
    const { code } = validateCallback(search, expectedState);
    if (!verifier) {
      throw new Error("PKCE verifier is missing");
    }

    const body = new URLSearchParams({
      grant_type: "authorization_code",
      client_id: this.config.app_client_id,
      code,
      redirect_uri: this.config.redirect_uri,
      code_verifier: verifier,
    });
    const response = await fetch(new URL("/oauth2/token", this.config.oauth_domain), {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!response.ok) {
      throw new Error("Token exchange failed");
    }
    const tokens = (await response.json()) as TokenResponse;
    if (!tokens.access_token || !Number.isFinite(tokens.expires_in)) {
      throw new Error("Token response is invalid");
    }
    if (expectedNonce) {
      if (!tokens.id_token) throw new Error("ID token is missing for nonce validation");
      try {
        const encodedPayload = tokens.id_token.split(".")[1];
        const normalizedPayload = encodedPayload.replaceAll("-", "+").replaceAll("_", "/");
        const paddedPayload = normalizedPayload.padEnd(Math.ceil(normalizedPayload.length / 4) * 4, "=");
        const payload = JSON.parse(atob(paddedPayload)) as {
          nonce?: string;
        };
        if (payload.nonce !== expectedNonce) throw new Error("OAuth nonce validation failed");
      } catch (cause) {
        if (cause instanceof Error && cause.message.includes("nonce")) throw cause;
        throw new Error("ID token could not be checked", { cause });
      }
    }
    this.storage.setItem(
      TOKEN_KEY,
      JSON.stringify({ ...tokens, expires_at: Date.now() + tokens.expires_in * 1000 }),
    );
    this.storage.removeItem(STATE_KEY);
    this.storage.removeItem(VERIFIER_KEY);
    this.storage.removeItem(NONCE_KEY);
    return tokens.access_token;
  }

  async restoreSession(): Promise<string | null> {
    const current = this.getAccessToken();
    if (current) return current;
    const previous = this.readTokens();
    if (!previous?.refresh_token) {
      this.storage.removeItem(TOKEN_KEY);
      return null;
    }

    const body = new URLSearchParams({
      grant_type: "refresh_token",
      client_id: this.config.app_client_id,
      refresh_token: previous.refresh_token,
    });
    const response = await fetch(new URL("/oauth2/token", this.config.oauth_domain), {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!response.ok) {
      this.storage.removeItem(TOKEN_KEY);
      return null;
    }
    const refreshed = (await response.json()) as TokenResponse;
    if (!refreshed.access_token || !Number.isFinite(refreshed.expires_in)) {
      this.storage.removeItem(TOKEN_KEY);
      return null;
    }
    const merged = {
      ...refreshed,
      refresh_token: refreshed.refresh_token ?? previous.refresh_token,
      expires_at: Date.now() + refreshed.expires_in * 1000,
    };
    this.storage.setItem(TOKEN_KEY, JSON.stringify(merged));
    return refreshed.access_token;
  }

  logout(redirectToHostedUi = true): void {
    this.storage.removeItem(TOKEN_KEY);
    this.storage.removeItem(STATE_KEY);
    this.storage.removeItem(VERIFIER_KEY);
    this.storage.removeItem(NONCE_KEY);
    if (redirectToHostedUi) {
      window.location.assign(buildLogoutUrl(this.config));
    }
  }
}
