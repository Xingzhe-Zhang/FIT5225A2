import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { CallbackPage } from "./pages/CallbackPage";
import { LibraryPage } from "./pages/LibraryPage";
import { LoginPage } from "./pages/LoginPage";
import { ProfilePage } from "./pages/ProfilePage";
import { SignupPage } from "./pages/SignupPage";
import { VerificationPage } from "./pages/VerificationPage";

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/auth/callback" element={<CallbackPage />} />
          <Route path="/verify" element={<VerificationPage />} />
          <Route element={<ProtectedRoute requireProfile={false} />}>
            <Route path="/profile" element={<ProfilePage />} />
          </Route>
          <Route element={<ProtectedRoute />}>
            <Route path="/library" element={<LibraryPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/library" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
