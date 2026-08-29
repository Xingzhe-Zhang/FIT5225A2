import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { CognitoAuthError } from "../auth/authClient";
import { useAuth } from "../auth/AuthContext";

export function SignupPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [givenName, setGivenName] = useState("");
  const [familyName, setFamilyName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [existingAccount, setExistingAccount] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setExistingAccount(false);
    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    setSubmitting(true);
    try {
      const normalizedEmail = email.trim().toLowerCase();
      const result = await auth.signup({
        email: normalizedEmail,
        password,
        givenName: givenName.trim(),
        familyName: familyName.trim(),
      });
      if (result.userConfirmed) {
        navigate("/login", { replace: true });
        return;
      }
      navigate(`/verify?email=${encodeURIComponent(normalizedEmail)}`, {
        state: { destination: result.destination },
      });
    } catch (cause: unknown) {
      setExistingAccount(cause instanceof CognitoAuthError && cause.code === "UsernameExistsException");
      setError(cause instanceof Error ? cause.message : "Registration failed");
    } finally {
      setSubmitting(false);
    }
  }

  const verificationUrl = `/verify?email=${encodeURIComponent(email.trim().toLowerCase())}`;

  return (
    <main className="auth-page auth-page-single">
      <section className="auth-card auth-state-card registration-card" aria-labelledby="registration-title">
        <div className="auth-brand auth-brand-dark"><span className="brand-mark" aria-hidden="true">PB</span><strong>Pacific BioArchive</strong></div>
        <p className="panel-kicker">Researcher registration</p>
        <h1 id="registration-title">Create your account</h1>
        <p>Register with your project email and verify it before signing in.</p>
        <form className="auth-form" onSubmit={submit}>
          <label>Email address<input required type="email" autoComplete="email" maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <div className="auth-form-row">
            <label>Given name<input required autoComplete="given-name" maxLength={100} value={givenName} onChange={(event) => setGivenName(event.target.value)} /></label>
            <label>Family name<input required autoComplete="family-name" maxLength={100} value={familyName} onChange={(event) => setFamilyName(event.target.value)} /></label>
          </div>
          <label>Password<input required type="password" autoComplete="new-password" minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          <small>Use at least 12 characters with uppercase, lowercase, number and symbol.</small>
          <label>Confirm password<input required type="password" autoComplete="new-password" minLength={12} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} /></label>
          <button type="submit" disabled={submitting}>{submitting ? "Creating account…" : "Create account"}</button>
        </form>
        {error && <p role="alert">{error}</p>}
        {existingAccount && <Link className="button auth-link" to={verificationUrl}>Confirm existing account</Link>}
        <Link className="button-link" to="/login">Return to sign in</Link>
      </section>
    </main>
  );
}
