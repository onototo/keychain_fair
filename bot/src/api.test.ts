import assert from "node:assert/strict";
import test from "node:test";
import { apiErrorMessage } from "./api.js";

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
