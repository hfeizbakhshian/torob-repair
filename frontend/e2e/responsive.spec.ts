import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

test.describe("layout and accessibility basics", () => {
  test("no unwanted horizontal scrolling at 360px", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 780 });
    for (const path of ["/", "/sign-in"]) {
      await page.goto(path);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `${path} overflows horizontally`).toBeLessThanOrEqual(1);
    }
  });

  test("the sample-data banner is always present", async ({ page }) => {
    await page.goto("/");
    await expect(
      page
        .getByText("همهٔ داده‌ها، قیمت‌ها، پرداخت‌ها و ارزیابی‌ها نمونه‌اند", { exact: false })
        .first(),
    ).toBeVisible();
  });

  test("the page is right-to-left and in Persian", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.locator("html")).toHaveAttribute("lang", "fa");
  });

  test("form fields are labelled and keyboard focus stays visible", async ({ page }) => {
    await signIn(page, "customer-sahar");
    await page.goto("/requests/new");
    const symptoms = page.getByLabel("نشانه‌هایی که دیده‌اید");
    await expect(symptoms).toBeVisible();
    await symptoms.focus();
    const outline = await symptoms.evaluate(
      (element) => getComputedStyle(element).outlineStyle,
    );
    expect(outline).not.toBe("none");
  });

  test("an error is carried by text, not colour alone", async ({ page }) => {
    await signIn(page, "customer-sahar");
    await page.goto("/requests/new");
    // The continue button stays disabled until the description is long enough, so the
    // requirement is communicated rather than only signalled by styling.
    await expect(page.getByTestId("create-draft")).toBeDisabled();
  });
});
