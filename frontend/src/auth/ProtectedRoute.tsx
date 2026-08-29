import { useEffect } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "./AuthContext";

export function ProtectedRoute({ requireProfile = true }: { requireProfile?: boolean }) {
  const auth = useAuth();
  const location = useLocation();

  useEffect(() => {
    if (
      requireProfile &&
      auth.status === "authenticated" &&
      auth.profileComplete === null &&
      !auth.profileError &&
      auth.refreshProfile
    ) {
      void auth.refreshProfile().catch(() => undefined);
    }
  }, [auth.profileComplete, auth.profileError, auth.refreshProfile, auth.status, requireProfile]);

  if (auth.status === "loading") return <p role="status">Loading secure session…</p>;
  if (auth.status !== "authenticated") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (requireProfile && auth.profileComplete !== undefined) {
    if (auth.profileError) return <p role="alert">{auth.profileError}</p>;
    if (auth.profileComplete === null) return <p role="status">Loading your profile…</p>;
    if (!auth.profileComplete) return <Navigate to="/profile" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
