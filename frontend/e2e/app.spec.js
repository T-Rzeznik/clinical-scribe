import { test, expect } from "@playwright/test";

// End-to-end tests driving the real UI against the running backend. These
// exercise every frontend feature plus edge cases (dropdown click-out, text
// contrast, token expiry + auto-refresh). Some tests trigger a REAL Claude
// generation, so they allow a generous timeout.

const CREDS = { email: "schen@scribe.local", password: "password123" };

async function fresh(page) {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
}

async function login(page, creds = CREDS) {
  await fresh(page);
  await page.getByLabel("Email").fill(creds.email);
  await page.getByLabel("Password").fill(creds.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "New encounter" })).toBeVisible();
}

const findPatient = (page) => page.getByPlaceholder(/Type a name/);
const matches = (page) => page.locator(".patient-matches .match");

// ---------------------------------------------------------------- auth

test.describe("authentication", () => {
  test("invalid credentials show an error, stay on login", async ({ page }) => {
    await fresh(page);
    await page.getByLabel("Email").fill(CREDS.email);
    await page.getByLabel("Password").fill("wrongpassword");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.locator(".error")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });

  test("valid login reaches the workspace", async ({ page }) => {
    await login(page);
    await expect(page.getByRole("heading", { name: "New encounter" })).toBeVisible();
  });

  test("logout returns to the login screen", async ({ page }) => {
    await login(page);
    await page.getByRole("button", { name: "Log out" }).click();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });
});

// ------------------------------------------------------ patient picker

test.describe("patient picker", () => {
  test("typing shows matches and they are readable (not white-on-white)", async ({ page }) => {
    await login(page);
    await findPatient(page).fill("co");
    await expect(matches(page).first()).toBeVisible();
    // Contrast guard: the result text must not be white (the old bug).
    const color = await matches(page).first().evaluate(
      (el) => getComputedStyle(el).color
    );
    expect(color).not.toBe("rgb(255, 255, 255)");
  });

  test("EDGE CASE: dropdown dismisses when you click outside it", async ({ page }) => {
    await login(page);
    await findPatient(page).fill("co");
    await expect(matches(page).first()).toBeVisible();
    // Click a component elsewhere (the transcript box). The dropdown should close
    // so it can't cover the fields underneath.
    await page.getByPlaceholder(/Paste the visit transcript/).click();
    await expect(matches(page)).toHaveCount(0);
  });

  test("EDGE CASE: can interact with fields under where the dropdown was", async ({ page }) => {
    await login(page);
    await findPatient(page).fill("co");
    await expect(matches(page).first()).toBeVisible();
    // Without dismissing explicitly, the First-name field must still be usable.
    await page.getByLabel("First name").fill("Testable");
    await expect(page.getByLabel("First name")).toHaveValue("Testable");
  });

  test("choosing a patient fills the identity fields", async ({ page }) => {
    await login(page);
    await findPatient(page).fill("co");
    await matches(page).first().click();
    await expect(page.getByLabel("First name")).not.toHaveValue("");
    await expect(page.getByLabel("Date of birth")).not.toHaveValue("");
    await expect(page.getByText(/Using existing patient/)).toBeVisible();
  });

  test("provider scoping: another provider sees none of schen's patients", async ({ page }) => {
    await login(page, { email: "jpatel@scribe.local", password: "password123" });
    await findPatient(page).fill("co");
    // Give the debounced search time to run, then assert no dropdown.
    await page.waitForTimeout(700);
    await expect(matches(page)).toHaveCount(0);
  });
});

// ------------------------------------------------------- generate gate

test.describe("generate gating (empty/short transcript)", () => {
  test("Generate stays disabled until patient + >=15 char transcript", async ({ page }) => {
    await login(page);
    const gen = page.getByRole("button", { name: /Generate SOAP note/ });
    await expect(gen).toBeDisabled();

    await page.getByLabel("First name").fill("Gate");
    await page.getByLabel("Last name").fill("Keeper");
    await page.getByLabel("Date of birth").fill("1990-01-01");
    await page.getByPlaceholder(/Paste the visit transcript/).fill("too short");
    await expect(gen).toBeDisabled(); // <15 chars

    await page
      .getByPlaceholder(/Paste the visit transcript/)
      .fill("Patient here for follow-up of chest pain and hypertension today.");
    await expect(gen).toBeEnabled();
  });
});

// ------------------------------------------------ reset / next patient

test.describe("clear / next patient", () => {
  test("clears the form without logging out", async ({ page }) => {
    await login(page);
    await page.getByLabel("First name").fill("Backto");
    await page.getByLabel("Last name").fill("Back");
    await page.getByPlaceholder(/Paste the visit transcript/).fill("some notes here");
    await page.getByRole("button", { name: "Clear / next patient" }).click();
    // Fields cleared, still in the workspace (not bounced to login).
    await expect(page.getByLabel("First name")).toHaveValue("");
    await expect(page.getByLabel("Last name")).toHaveValue("");
    await expect(page.getByPlaceholder(/Paste the visit transcript/)).toHaveValue("");
    await expect(page.getByRole("heading", { name: "New encounter" })).toBeVisible();
  });
});

// ---------------------------------------------------- auto-refresh (A)

test.describe("token expiry + auto-refresh", () => {
  // Corrupt the stored access token (leaving the refresh token intact) to force a
  // 401 on the next authed call — same thing the removed dev button did.
  const expireAccessToken = (page) =>
    page.evaluate(() =>
      localStorage.setItem("access_token", "expired.invalid.token")
    );

  test("expired access token silently recovers (action still works)", async ({ page }) => {
    await login(page);
    await expireAccessToken(page);
    // Next authed call (patient search) should 401 -> refresh -> retry -> succeed.
    await findPatient(page).fill("co");
    await expect(matches(page).first()).toBeVisible();
    // Still in the workspace, not bounced to login.
    await expect(page.getByRole("heading", { name: "New encounter" })).toBeVisible();
  });

  test("give-up path: dead refresh token bounces to login", async ({ page }) => {
    await login(page);
    await page.evaluate(() => localStorage.setItem("refresh_token", "garbage.dead.token"));
    await expireAccessToken(page);
    await findPatient(page).fill("co"); // authed call: 401 -> refresh fails -> onAuthLost
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });
});

// ------------------------------- full pipeline: generate -> ICD -> save (D)

test.describe("generate -> review -> ICD -> save", () => {
  test("full happy path with a real generation", async ({ page }) => {
    test.setTimeout(120_000);
    await login(page);
    await page.getByLabel("First name").fill("E2E");
    await page.getByLabel("Last name").fill("Playwright");
    await page.getByLabel("Date of birth").fill("1975-08-08");
    await page
      .getByPlaceholder(/Paste the visit transcript/)
      .fill(
        "58yo male, follow-up for chest pain over the past week. Has type 2 diabetes and hypertension. BP 148/92. On metformin and lisinopril. Plan EKG and labs, continue meds, follow up in two weeks."
      );
    await page.getByRole("button", { name: /Generate SOAP note/ }).click();

    // Review section appears when streaming finishes.
    await expect(page.getByRole("heading", { name: /Review/ })).toBeVisible({
      timeout: 90_000,
    });
    // The four SOAP fields exist and the first is non-empty.
    const soapFields = page.locator(".soap-grid textarea");
    await expect(soapFields).toHaveCount(4);
    await expect(soapFields.first()).not.toHaveValue("");

    // ICD card is present; save the note.
    await expect(page.getByRole("heading", { name: "ICD-10 codes" })).toBeVisible();
    await page.getByRole("button", { name: "Save note version" }).click();
    await expect(page.locator(".saved")).toContainText(/Saved as version/);
  });
});

// ------------------------------------------------- admin RBAC + dashboard

test.describe("admin RBAC + dashboard", () => {
  test("provider does NOT see the Admin nav", async ({ page }) => {
    await login(page); // schen = provider
    await expect(page.getByRole("button", { name: "Admin" })).toHaveCount(0);
  });

  test("admin sees the Admin nav and dashboard (stats + audit)", async ({ page }) => {
    await login(page, { email: "admin", password: "password" });
    await page.getByRole("button", { name: "Admin" }).click();
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
    // Stat cards render with numbers.
    await expect(page.locator(".stat").first()).toBeVisible();
    // Audit log shows at least this admin's login.
    await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
    await expect(page.locator(".action-tag").first()).toBeVisible();
    // Can navigate back to the workspace.
    await page.getByRole("button", { name: "Workspace" }).click();
    await expect(page.getByRole("heading", { name: "New encounter" })).toBeVisible();
  });

  test("admin can assign a role, and can't demote themselves", async ({ page }) => {
    await login(page, { email: "admin", password: "password" });
    await page.getByRole("button", { name: "Admin" }).click();
    await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();

    // mgarcia's row: promote to admin, then back to provider (leaves state clean).
    const row = page.locator("tr", { hasText: "mgarcia@scribe.local" });
    const select = row.locator("select.role-select");
    await select.selectOption("admin");
    await expect(select).toHaveValue("admin");
    await select.selectOption("provider");
    await expect(select).toHaveValue("provider");

    // The admin's own row control is disabled (no self-demotion). "Ava Admin" is
    // unique to the admin's row (the word "admin" alone appears in every select).
    const ownRow = page.locator("tr", { hasText: "Ava Admin" });
    await expect(ownRow.locator("select.role-select")).toBeDisabled();
  });
});

// --------------------------------------------------------- history (E)

test.describe("patient history browsing", () => {
  test("picking a patient shows prior encounters and versions", async ({ page }) => {
    await login(page);
    // E2E Playwright was just saved above; search and open their history.
    await findPatient(page).fill("Playwright");
    const hit = matches(page).first();
    await expect(hit).toBeVisible();
    await hit.click();
    await expect(page.getByRole("heading", { name: /Prior encounters/ })).toBeVisible();
    // Expand the newest encounter and confirm a version renders.
    await page.locator(".history-head").first().click();
    await expect(page.locator(".history-version").first()).toBeVisible();
  });
});
