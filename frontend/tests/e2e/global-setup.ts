import { request } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

/**
 * Sign in once per run and hand the session to every journey.
 *
 * The platform rate-limits logins per account and IP (5 attempts / 5 minutes,
 * spec 12/29). Five journeys each signing in would exceed that and turn a
 * healthy stack into "login failed" noise, so the session is established here
 * once and stored; journeys that need a *different* identity (the capability
 * gate checks a viewer) still log in explicitly.
 */
export const ADMIN_STATE = "tests/e2e/.auth/admin.json";

export default async function globalSetup(): Promise<void> {
  const baseURL = process.env.E2E_BASE_URL;
  if (!baseURL) return; // the suite is skipped without a stack

  const username = process.env.E2E_ADMIN_USER ?? "admin";
  const password = process.env.E2E_ADMIN_PASSWORD ?? "dev-admin-password-123";

  const context = await request.newContext({ baseURL });
  const response = await context.post("/api/v1/auth/login", {
    data: { username, password },
  });
  if (!response.ok()) {
    throw new Error(
      `global setup could not sign in as ${username}: HTTP ${response.status()} ${await response.text()}`,
    );
  }
  mkdirSync(dirname(ADMIN_STATE), { recursive: true });
  await context.storageState({ path: ADMIN_STATE });
  await context.dispose();
  writeFileSync(`${ADMIN_STATE}.user`, username, "utf-8");
}
