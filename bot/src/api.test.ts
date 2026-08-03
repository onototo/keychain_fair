import assert from "node:assert/strict";
import test from "node:test";
import { KeychainApi, apiErrorMessage } from "./api.js";

test("apiErrorMessage translates pydantic car number errors to Russian", () => {
  const message = apiErrorMessage(
    {
      detail: [
        {
          type: "value_error",
          loc: ["body", "car_number"],
          msg: "Value error, Car number must use BY format 1234 AB-7 with Latin letters",
        },
      ],
    },
    422,
  );

  assert.equal(message, "Введите номер авто в белорусском формате 1234 AB-7, буквы должны быть латиницей.");
});

test("apiErrorMessage translates service errors to Russian", () => {
  assert.equal(
    apiErrorMessage({ detail: "Custom text is too long. Maximum length is 26 characters." }, 400),
    "Текст слишком длинный. Максимум 26 символов.",
  );
  assert.equal(
    apiErrorMessage(
      {
        detail: {
          message: "You can have only 2 unpaid orders. Please pay for an existing order before creating another one.",
        },
      },
      429,
    ),
    "У вас уже есть 2 неоплаченных заказа. Оплатите один из них перед созданием нового.",
  );
});

test("apiErrorMessage uses Russian fallback", () => {
  assert.equal(
    apiErrorMessage({}, 500),
    "Сервер вернул ошибку 500. Попробуйте ещё раз или обратитесь к оператору.",
  );
});

test("renderOrderPreview posts draft payload and returns a PNG buffer", async (t) => {
  const originalFetch = globalThis.fetch;
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47]);
  let capturedInput: string | URL | Request = "";
  let capturedInit: RequestInit | undefined;
  globalThis.fetch = async (input, init) => {
    capturedInput = input;
    capturedInit = init;
    return new Response(png, { status: 200, headers: { "Content-Type": "image/png" } });
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const payload = {
    customer_name: "Anna",
    car_number: "",
    design_id: "classic_plate",
    size_id: "standard",
    elements: [],
    print_line_1: "Hello",
    print_line_2: "",
    idempotency_key: "tg:1:draft",
    source: "telegram" as const,
    telegram_chat_id: "1",
  };

  const buffer = await new KeychainApi("http://api.test", "secret").renderOrderPreview(payload);

  assert.equal(capturedInput, "http://api.test/api/internal/order-preview/render.png");
  assert.equal(capturedInit?.method, "POST");
  assert.equal((capturedInit?.headers as Record<string, string>)["X-Internal-Token"], "secret");
  assert.deepEqual(JSON.parse(String(capturedInit?.body)), payload);
  assert.deepEqual(buffer, png);
});
