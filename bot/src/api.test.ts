import assert from "node:assert/strict";
import test from "node:test";
import { apiErrorMessage } from "./api.js";
import { loadConfig } from "./config.js";

test("api errors from the shop are shown as written", () => {
  assert.equal(apiErrorMessage({ detail: "Можно заказать не больше 99 штук" }, 400), "Можно заказать не больше 99 штук");
  assert.equal(apiErrorMessage({ detail: [{ msg: "invalid" }] }, 422), "Проверьте данные заказа.");
});

test("admin ids and optional payment settings come from env", () => {
  const config = loadConfig({
    TELEGRAM_BOT_TOKEN: "token",
    INTERNAL_API_TOKEN: "secret",
    ADMIN_TELEGRAM_USER_IDS: "10, 20",
    PAYMENT_CARD_NUMBER: "4111",
    ONLINE_PAYMENT_URL: "https://pay.example",
  });
  assert.deepEqual(config.adminUserIds, ["10", "20"]);
  assert.equal(config.paymentCardNumber, "4111");
  assert.equal(config.onlinePaymentUrl, "https://pay.example");
  assert.equal(config.apiBaseUrl, "http://127.0.0.1:8120");
});
