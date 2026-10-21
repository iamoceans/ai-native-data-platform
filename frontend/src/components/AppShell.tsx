import { applyAccountPreferences, PLATFORM_NAME, t, useLocale } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { logout, me } from "../api/endpoints";

export function AppShell() {
  useLocale();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const profile = useQuery({ queryKey: ["me"], queryFn: me });
  useEffect(() => {
    if (profile.data?.id) applyAccountPreferences(profile.data);
  }, [profile.data]);
  useEffect(() => {
    const refresh = () => { queryClient.invalidateQueries({ queryKey: ["me"] }); };
    window.addEventListener("account-preferences-changed", refresh);
    return () => window.removeEventListener("account-preferences-changed", refresh);
  }, [queryClient]);
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (event.key === "ainative.auth-change") {
        queryClient.clear();
        window.location.reload();
      }
    };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, [queryClient]);

  if (profile.isError) {
    return (
      <div className="auth-gate">
        <div className="card auth-card">
          <h1>{t("Session required")}</h1>
          <p className="muted">{t("Sign in to use the workspace.")}</p>
          <button className="primary" onClick={() => navigate("/login")}>
            {t("Go to sign in")}
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
          <span>{PLATFORM_NAME}</span>
        </div>
        <nav className="nav">
          <NavLink
            to="/ask"
            className={({ isActive }) =>
              isActive ? "nav-link active" : "nav-link"
            }
          >
            {t("Ask data")}
          </NavLink>
          <NavLink
            to="/sql"
            className={({ isActive }) =>
              isActive ? "nav-link active" : "nav-link"
            }
          >
            {t("SQL workspace")}
          </NavLink>
          <NavLink
            to="/catalog"
            className={({ isActive }) =>
              isActive ? "nav-link active" : "nav-link"
            }
          >
            {t("Catalog")}
          </NavLink>
          <NavLink
            to="/history"
            className={({ isActive }) =>
              isActive ? "nav-link active" : "nav-link"
            }
          >
            {t("History")}
          </NavLink>
          <NavLink
            to="/skills"
            data-testid="nav-skills"
            className={({ isActive }) =>
              isActive ? "nav-link active" : "nav-link"
            }
          >
            {t("Skills")}
          </NavLink>
          {profile.data?.capabilities.includes("admin.manage") ? (
            <>
              <NavLink
                to="/admin/datasources"
                data-testid="nav-admin-datasources"
                className={({ isActive }) =>
                  isActive ? "nav-link active" : "nav-link"
                }
              >
                {t("Datasources")}
              </NavLink>
              <NavLink
                to="/admin/permissions"
                data-testid="nav-admin-permissions"
                className={({ isActive }) =>
                  isActive ? "nav-link active" : "nav-link"
                }
              >
                {t("Permissions")}
              </NavLink>
            </>
          ) : null}
        </nav>
        <div className="topbar-right">
          {profile.data ? (
            <>
              <NavLink to="/settings" data-testid="nav-settings">
                {profile.data.display_name || profile.data.username} · {t("个人设置")}
              </NavLink>
              <button
                className="ghost"
                onClick={async () => {
                  await logout();
                  // Cached data belongs to the session that just ended; keeping it
                  // would let the next account briefly see the previous profile
                  // (and its capabilities) until the cache went stale.
                  queryClient.clear();
                  navigate("/login");
                }}
              >
                {t("Sign out")}
              </button>
            </>
          ) : (
            <span className="muted small">{t("loading…")}</span>
          )}
        </div>
      </header>
      <main className="page">
        <Outlet />
      </main>
    </div>
  );
}
