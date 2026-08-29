import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

export function LoginPage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (auth.status === "authenticated") {
      navigate("/library", { replace: true });
    }
  }, [auth.status, navigate]);

  const start = (provider?: string) => {
    setError(null);
    void auth.login(provider).catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : "Sign-in could not start");
    });
  };

  return (
    <main className="auth-page auth-page-split">
      <section className="auth-story" aria-labelledby="auth-story-title">
        <div className="auth-brand"><span className="brand-mark" aria-hidden="true">PB</span><strong>Pacific BioArchive</strong></div>
        <div className="auth-story-copy">
          <p className="eyebrow">Biodiversity field library</p>
          <h1 id="auth-story-title">Field research, organised.</h1>
          <p>A secure home for wildlife observations from capture to discovery.</p>
        </div>
        <ul className="auth-benefits" aria-label="Platform capabilities">
          <li><span>01</span><strong>Archive</strong><small>Images and field video</small></li>
          <li><span>02</span><strong>Discover</strong><small>Species and related media</small></li>
          <li><span>03</span><strong>Monitor</strong><small>Tag-based email alerts</small></li>
        </ul>
      </section>
      <section className="auth-card" aria-labelledby="sign-in-title">
        <div className="auth-card-heading">
          <p className="panel-kicker">Researcher access</p>
          <h2 id="sign-in-title">Sign in to your library</h2>
          <p>Continue with your project identity to manage private wildlife observations.</p>
        </div>
        <div className="auth-actions">
          <button type="button" onClick={() => start()}><span aria-hidden="true">✉</span>Continue with email</button>
          {auth.config?.external_providers.map((provider) => (
            <button type="button" className="secondary" key={provider} onClick={() => start(provider)}>
              <span className="provider-mark" aria-hidden="true">{provider.charAt(0)}</span>Continue with {provider}
            </button>
          ))}
        </div>
        <div className="auth-divider"><span>New to the archive?</span></div>
        <Link className="button secondary auth-link auth-link-secondary" to="/signup">Create an account</Link>
        {auth.config?.local_auth_enabled && (
          <button type="button" className="button-link local-login" onClick={() => void auth.localLogin().catch((cause: unknown) => {
            setError(cause instanceof Error ? cause.message : "Local authentication failed");
          })}>Use local demo account</button>
        )}
        {error && <p role="alert">{error}</p>}
        <small className="auth-footnote">Access is limited to authorised project participants.</small>
      </section>
    </main>
  );
}
