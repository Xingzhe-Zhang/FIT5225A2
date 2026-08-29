import { type FormEvent, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { PlatformClient } from "../api/platformClient";
import { useAuth } from "../auth/AuthContext";

const client = new PlatformClient();

export function ProfilePage() {
  const auth = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [givenName, setGivenName] = useState("");
  const [familyName, setFamilyName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!auth.accessToken) return;
    let active = true;
    void client.getProfile(auth.accessToken).then((profile) => {
      if (!active) return;
      setGivenName(profile.given_name ?? "");
      setFamilyName(profile.family_name ?? "");
    }).catch(() => {
      // The form remains usable and the save request provides the actionable error.
    });
    return () => { active = false; };
  }, [auth.accessToken]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!auth.accessToken) return;
    setSaving(true);
    setError(null);
    try {
      await client.updateProfile(givenName.trim(), familyName.trim(), auth.accessToken);
      await auth.refreshProfile?.();
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== "/profile" ? from : "/library", { replace: true });
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Profile could not be saved");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="auth-page auth-page-single">
      <section className="auth-card auth-state-card profile-card" aria-labelledby="profile-title">
        <div className="auth-brand auth-brand-dark"><span className="brand-mark" aria-hidden="true">PB</span><strong>Pacific BioArchive</strong></div>
        <p className="panel-kicker">Complete your researcher profile</p>
        <h1 id="profile-title">Tell us your name</h1>
        <p>Use your name for archive ownership and notification messages.</p>
        <form onSubmit={submit}>
          <label>Given name<input required maxLength={100} value={givenName} onChange={(event) => setGivenName(event.target.value)} autoComplete="given-name" /></label>
          <label>Family name<input required maxLength={100} value={familyName} onChange={(event) => setFamilyName(event.target.value)} autoComplete="family-name" /></label>
          <button type="submit" disabled={saving}>{saving ? "Saving…" : "Save and continue"}</button>
        </form>
        {error && <p role="alert">{error}</p>}
      </section>
    </main>
  );
}
