import { expect, test } from "@playwright/test";
import { publishRequest, signIn, signOut } from "./helpers";

test.describe("permissions in the browser", () => {
  test("a specialist cannot open another customer's comparison", async ({ page }) => {
    await signIn(page, "customer-sahar");
    const requestId = await publishRequest(page);
    await signOut(page);

    await signIn(page, "specialist-arya");
    const response = await page.request.get(`/api/requests/${requestId}/offers`);
    expect(response.status()).toBe(403);
    expect((await response.json()).code).toBe("FORBIDDEN");
  });

  test("a customer cannot reach the specialist or support desks", async ({ page }) => {
    await signIn(page, "customer-sahar");
    expect((await page.request.get("/api/specialist/requests")).status()).toBe(403);
    expect((await page.request.get("/api/support/queue")).status()).toBe(403);
  });

  test("an irrelevant specialist does not see the request at all", async ({ page }) => {
    await signIn(page, "customer-sahar");
    const requestId = await publishRequest(page);
    await signOut(page);

    // Wrong city.
    await signIn(page, "specialist-rasht");
    await page.goto("/specialist");
    await expect(page.getByText("در حال حاضر درخواست مرتبطی وجود ندارد.")).toBeVisible();
    const list = await (await page.request.get("/api/specialist/requests")).json();
    expect(list.some((item: { id: string }) => item.id === requestId)).toBe(false);
  });

  test("signing out ends the session", async ({ page }) => {
    await signIn(page, "customer-sahar");
    await signOut(page);
    expect((await page.request.get("/api/requests")).status()).toBe(403);
  });
});
