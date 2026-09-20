import { expect, type Page } from "@playwright/test";

export const VEHICLE_LABEL = "پژو ۲۰۶ تیپ ۵";

/** The header is the only place the session state is shown, so scope to it. */
function header(page: Page) {
  return page.getByRole("banner");
}

export async function signIn(page: Page, loginKey: string): Promise<void> {
  await page.goto("/sign-in");
  await page.getByTestId(`sign-in-${loginKey}`).click();
  await expect(header(page).getByRole("button", { name: "خروج" })).toBeVisible();
}

export async function signOut(page: Page): Promise<void> {
  await header(page).getByRole("button", { name: "خروج" }).click();
  await expect(
    header(page).getByRole("link", { name: "ورود با حساب نمونه" }),
  ).toBeVisible();
}

/** Create a request, pay the sample fee, answer the questions and publish it. */
export async function publishRequest(page: Page): Promise<string> {
  await page.goto("/requests/new");
  await page
    .getByLabel("نشانه‌هایی که دیده‌اید")
    .fill("هنگام گاز دادن دور موتور بالا می‌رود ولی سرعت زیاد نمی‌شود.");
  await page.getByTestId("create-draft").click();

  await expect(
    page.getByRole("heading", { name: "۲. هزینهٔ بستهٔ ثبت درخواست" }),
  ).toBeVisible();
  await page.getByTestId("pay").click();

  await expect(page.getByRole("heading", { name: "۳. پرسش‌های لازم" })).toBeVisible();
  const selects = page.locator("select[id^='q-']");
  for (let index = 0; index < (await selects.count()); index += 1) {
    const option = selects.nth(index).locator("option").nth(1);
    await selects.nth(index).selectOption(await option.getAttribute("value") ?? "");
  }
  const numbers = page.locator("input[id^='q-'][type='number']");
  for (let index = 0; index < (await numbers.count()); index += 1) {
    await numbers.nth(index).fill("180000");
  }
  await page.getByTestId("build-summary").click();

  await expect(
    page.getByRole("heading", { name: "۴. تأیید خلاصه و انتشار" }),
  ).toBeVisible();
  await page.getByTestId("publish").click();

  await page.waitForURL(/\/requests\/[0-9a-f-]{36}$/);
  const match = /\/requests\/([0-9a-f-]{36})$/.exec(page.url());
  if (!match?.[1]) throw new Error(`could not read the request id from ${page.url()}`);
  return match[1];
}

/** Submit the default offer as the currently signed-in specialist. */
export async function submitOffer(page: Page, requestId: string): Promise<void> {
  await page.goto("/specialist");
  const details = page.locator("details", { hasText: "ثبت یا ویرایش پیشنهاد" }).first();
  await details.locator("summary").click();
  await page.getByTestId(`submit-offer-${requestId}`).click();
  await expect(page.getByRole("heading", { name: "پیشنهادهای من" })).toBeVisible();
}

export async function advanceDemoClock(page: Page, label: string): Promise<void> {
  await page.goto("/support");
  await page.getByRole("button", { name: label }).click();
}
