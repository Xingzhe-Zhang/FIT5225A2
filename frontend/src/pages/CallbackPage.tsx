import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

export function CallbackPage() {
  const auth = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (!auth.config || started.current) return;
    started.current = true;
    void auth
      .completeCallback(location.search)
      .then(() => navigate("/library", { replace: true }))
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : "Sign-in failed");
      });
  }, [auth.config, auth.completeCallback, location.search, navigate]);

  return (
    <main className="auth-page auth-page-single">
      <section className="auth-card auth-state-card">
        <div className="auth-brand auth-brand-dark"><span className="brand-mark" aria-hidden="true">PB</span><strong>Pacific BioArchive</strong></div>
        <span className="state-icon state-icon-loading" aria-hidden="true">↻</span>
        <p className="panel-kicker">Secure access</p>
        <h1>Completing sign-in</h1>
        {error ? <p role="alert">{error}</p> : <p role="status">Validating your secure session…</p>}
      </section>
    </main>
  );
}
