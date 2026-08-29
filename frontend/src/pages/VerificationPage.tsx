import { type FormEvent, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

export function VerificationPage() {
  const auth = useAuth();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [email, setEmail] = useState(searchParams.get("email") ?? "");
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resending, setResending] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [message, setMessage] = useState<string | null>(() => {
    const destination = (location.state as { destination?: string } | null)?.destination;
    return destination ? `A verification code was sent to ${destination}.` : null;
  });
  const [error, setError] = useState<string | null>(null);

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setMessage(null);
    setSubmitting(true);
    try {
      await auth.confirmRegistration(email, code);
      setConfirmed(true);
      setMessage("Email confirmed. You can now sign in.");
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Email confirmation failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function resend() {
    setError(null);
    setMessage(null);
    if (!email.trim()) {
      setError("Enter the email address used during registration");
      return;
    }
    setResending(true);
    try {
      const destination = await auth.resendRegistration(email);
      setMessage(destination ? `A new verification code was sent to ${destination}.` : "A new verification code was sent.");
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "A new verification code could not be sent");
    } finally {
      setResending(false);
    }
  }

  return (
    <main className="auth-page auth-page-single">
      <section className="auth-card auth-state-card registration-card" aria-labelledby="verification-title">
        <div className="auth-brand auth-brand-dark"><span className="brand-mark" aria-hidden="true">PB</span><strong>Pacific BioArchive</strong></div>
        <span className="state-icon" aria-hidden="true">✉</span>
        <p className="panel-kicker">Email verification</p>
        <h1 id="verification-title">Confirm your email</h1>
        <p>Enter the code from your verification email. You can return here later to confirm an unfinished registration.</p>
        {!confirmed && (
          <form className="auth-form" onSubmit={confirm}>
            <label>Email address<input required type="email" autoComplete="email" maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} /></label>
            <label>Verification code<input required inputMode="numeric" autoComplete="one-time-code" minLength={6} maxLength={6} pattern="[0-9]{6}" value={code} onChange={(event) => setCode(event.target.value)} /></label>
            <button type="submit" disabled={submitting}>{submitting ? "Confirming…" : "Confirm email"}</button>
            <button type="button" className="secondary" disabled={resending} onClick={() => void resend()}>{resending ? "Sending…" : "Resend code"}</button>
          </form>
        )}
        {message && <p role="status">{message}</p>}
        {error && <p role="alert">{error}</p>}
        <Link className={confirmed ? "button auth-link" : "button-link"} to="/login">Return to sign in</Link>
      </section>
    </main>
  );
}
