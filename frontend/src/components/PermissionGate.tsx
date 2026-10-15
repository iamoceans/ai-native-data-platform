import { t, useLocale } from "../i18n";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { me } from "../api/endpoints";

/**
 * PermissionGate (spec section 24): render the management UI only for the
 * capabilities it needs, and say plainly what is missing otherwise.
 *
 * The gate is a convenience, not the security boundary - every admin endpoint
 * checks the capability again on the server.
 */
export function PermissionGate({
  capability,
  children,
}: {
  capability: string;
  children: ReactNode;
}) {
  useLocale();
  const profile = useQuery({ queryKey: ["me"], queryFn: me });

  if (profile.isLoading) {
    return (
      <section className="card">
        <p className="muted">{t("Checking your permissions…")}</p>
      </section>
    );
  }
  const capabilities = profile.data?.capabilities ?? [];
  if (!capabilities.includes(capability)) {
    return (
      <section className="card" data-testid="permission-gate">
        <div className="card-head">
          <h2>{t("Administrator capability required")}</h2>
        </div>
        <p className="notice">
          {t("permission.required", {
            capability,
            username: profile.data?.username ?? t("unknown"),
            capabilities: capabilities.join(", ") || t("无"),
          })}
        </p>
      </section>
    );
  }
  return <>{children}</>;
}
