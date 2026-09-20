import { expect, test } from "@playwright/test";
import { publishRequest, signIn, signOut, submitOffer } from "./helpers";

test.describe("the customer journey", () => {
  test("shows cost and refund policy before any payment", async ({ page }) => {
    await signIn(page, "customer-sahar");
    await page.goto("/requests/new");
    await page
      .getByLabel("نشانه‌هایی که دیده‌اید")
      .fill("صدای غیرعادی هنگام گرفتن کلاچ.");
    await page.getByTestId("create-draft").click();

    // The fee, the per-stage turn cap and the refund policy are all visible, and the
    // amount is labelled as a test payment, before the pay button is used.
    await expect(
      page.getByRole("heading", { name: "۲. هزینهٔ بستهٔ ثبت درخواست" }),
    ).toBeVisible();
    await expect(page.getByText("۲٬۰۰۰ تومان").first()).toBeVisible();
    await expect(page.getByText("پرداخت آزمایشی — بدون انتقال وجه واقعی")).toBeVisible();
    await expect(page.getByText("bootstrap").first()).toBeVisible();
    await expect(page.getByTestId("pay")).toBeVisible();
  });

  test("refuses a service outside the demo coverage", async ({ page }) => {
    await signIn(page, "customer-sahar");
    await page.goto("/requests/new");
    // Everything in the picker is covered, so the guard is checked on the API contract
    // instead: an uncovered city produces the "not in this demo" message.
    const response = await page.request.post("/api/catalog/coverage", {
      data: { city: "اصفهان", vehicleCode: "peugeot_206_type_5", serviceCode: "clutch_kit_replacement" },
    });
    const body = await response.json();
    expect(body.supported).toBe(false);
    expect(body.message).toContain("در پوشش این دمو نیست");
  });

  test("runs the full path from request to completion", async ({ page }) => {
    await signIn(page, "customer-sahar");
    const requestId = await publishRequest(page);
    await expect(page.getByText("در انتظار پیشنهاد").first()).toBeVisible();
    await signOut(page);

    await signIn(page, "specialist-arya");
    await submitOffer(page, requestId);
    await signOut(page);

    await signIn(page, "customer-sahar");
    await page.goto(`/requests/${requestId}`);
    await expect(page.getByRole("heading", { name: "مقایسهٔ پیشنهادها" })).toBeVisible();
    await expect(page.getByText("تعمیرگاه نمونهٔ آریا")).toBeVisible();
    // No score is shown until there is enough history behind it.
    await page.getByText("ریز اقلام و شرایط").first().click();
    await expect(page.getByText("سابقهٔ کافی نیست").first()).toBeVisible();

    // Selecting is blocked until the arbitration clause is accepted.
    const selectButton = page.locator("[data-testid^='select-']").first();
    await expect(selectButton).toBeDisabled();
    await page.getByTestId("accept-arbitration").check();
    await expect(selectButton).toBeEnabled();
    await selectButton.click();

    await expect(page.getByRole("heading", { name: "همکاری انتخاب‌شده" })).toBeVisible();
  });
});
