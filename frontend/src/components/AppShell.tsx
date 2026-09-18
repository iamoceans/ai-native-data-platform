import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { logout, me } from "../api/endpoints";

export function AppShell() {
  const navigate = useNavigate();
  const profile = useQuery({ queryKey: ["me"], queryFn: me });

  if (profile.isError) {
    return (
      <div className="auth-gate">
        <div className="card auth-card">
          <h1>Session required</h1>
          <p className="muted">Sign in to use the workspace.</p>
          <button className="primary" onClick={() => navigate("/login")}>
            Go to sign in
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>AI-Native Data Platform</span>
        </div>
        <nav className="nav">
          <NavLink to="/ask" className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            Ask data
          </NavLink>
          <NavLink to="/sql" className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            SQL workspace
          </NavLink>
          <NavLink to="/catalog" className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            Catalog
          </NavLink>
          <NavLink to="/history" className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            History
          </NavLink>
        </nav>
        <div className="topbar-right">
          {profile.data ? (
            <>
              <span className="muted small">
                {profile.data.username} · {profile.data.roles.join(", ")}
              </span>
              <button
                className="ghost"
                onClick={async () => {
                  await logout();
                  navigate("/login");
                }}
              >
                Sign out
              </button>
            </>
          ) : (
            <span className="muted small">loading…</span>
          )}
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </div>
  );
}
