import { expect, test } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { z } from "zod";

const activationFile = process.env.EMBER_LAN_ACTIVATION_FILE;

test("authenticated HTTP LAN browser joins the room and can inspect deep recall", async ({
  page,
  context,
}) => {
  test.skip(
    !activationFile,
    "Supply the explicitly authorized private LAN activation file.",
  );
  const activation = z
    .object({ lan_url: z.string().url() })
    .parse(JSON.parse(await readFile(activationFile!, "utf8")));
  const url = new URL(activation.lan_url);
  const token = new URLSearchParams(url.hash.slice(1)).get("activate");
  expect(token !== null).toBe(true);
  const bootstrap = await context.request.post(url.origin + "/api/bootstrap", {
    data: { token },
    headers: { origin: url.origin },
  });
  expect(bootstrap.status()).toBe(200);
  await page.goto(url.origin);
  expect(await page.evaluate(() => window.isSecureContext)).toBe(false);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Disconnect", exact: true }),
  ).toBeVisible({ timeout: 30000 });
  expect(await page.evaluate(() => typeof crypto.randomUUID)).toBe("function");
  await expect(
    page.getByRole("button", { name: "Use microphone", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText(/Microphone input on another device requires HTTPS/),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Message Ember" })
    .fill("Synthetic unsent LAN transport check.");
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeEnabled();
  let memories: number | null = null;
  const queryFile = process.env.EMBER_LAN_TEST_QUERY_FILE;
  if (queryFile) {
    const queries = z
      .array(z.string().min(1).max(512))
      .min(1)
      .max(32)
      .parse(JSON.parse(await readFile(queryFile, "utf8")));
    await page
      .getByRole("textbox", { name: "Message Ember" })
      .fill(queries[0]!);
    await page
      .getByRole("button", { name: "Recall deeply", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Recall details", exact: true }),
    ).toBeVisible({ timeout: 10000 });
    const count = await page.locator(".inspector > p").first().innerText();
    memories = Number(count.split(" ")[0]);
    expect(memories > 0).toBe(true);
    await expect(
      page.getByRole("button", {
        name: "Answer using this recall",
        exact: true,
      }),
    ).toBeEnabled();
  }
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  const receipt = process.env.EMBER_LAN_RECEIPT;
  if (receipt)
    await writeFile(
      receipt,
      JSON.stringify({
        http_lan: true,
        secure_context: false,
        room_connected: true,
        send_enabled: true,
        microphone_requires_https: true,
        deep_memories: memories,
        no_prompt_generation: true,
      }) + "\n",
    );
});
