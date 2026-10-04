import assert from "node:assert/strict";
import test from "node:test";
import { shortId } from "./cart.js";
import { adminOrderText, checkoutText, customerOrderText, renderCart } from "./messages.js";
import type { IndexedProduct, Order, Session } from "./types.js";

function product(id: string, title: string, price: number): IndexedProduct {
  return {
    id,
    code: shortId(id),
    title,
    description: "",
    price_kopecks: price,
    price_byn: "",
    has_image: false,
  };
}

function catalog(items: IndexedProduct[], maxUnits = 99) {
  return { maxUnits, byCode: new Map(items.map((item) => [item.code, item])) };
}

function order(overrides: Partial<Order> = {}): Order {
  return {
    id: "order-1",
    order_number_label: "001",
    status: "new",
    payment_method: "cod",
    customer_name: "Иван Петров",
    phone: "+375291234567",
    office_number: "12",
    office_city: "Минск",
    office_address: "ул. Ленина, 1",
    total_byn: "19 BYN",
    shipment_status: "manual",
    items: [{ product_id: "toys/skull", title: "Череп", unit_price_kopecks: 1500, quantity: 1, line_byn: "15 BYN" }],
    ...overrides,
  };
}

test("cart text shows the shared total and a missing product", () => {
  const skull = product("toys/skull", "Череп", 1500);
  const indexed = catalog([skull]);
  assert.equal(renderCart([], indexed, 0).text, "Корзина пуста.");
  const text = renderCart(
    [
      { productId: "toys/skull", quantity: 2 },
      { productId: "toys/gone", quantity: 1 },
    ],
    indexed,
    0,
  ).text;
  assert.match(text, /Череп × 2 — 30 BYN/);
  assert.match(text, /Товар снят с каталога × 1 — 0 BYN/);
  assert.match(text, /Итого: 30 BYN/);
  assert.doesNotMatch(text, /99 штук/);
});

test("checkout and order cards name the payment and manual shipment", () => {
  const skull = product("toys/skull", "Череп", 1500);
  const session: Session = {
    step: "confirm",
    cart: [{ productId: "toys/skull", quantity: 1 }],
    paymentMethod: "transfer",
    customerName: "Иван Петров",
    phone: "+375291234567",
  };
  const checkout = checkoutText(session, catalog([skull]), "№12, Минск, ул. Ленина, 1");
  assert.match(checkout, /^Проверьте заказ:/);
  assert.match(checkout, /Оплата: перевод на карту/);
  assert.match(checkout, /Отделение: №12, Минск, ул. Ленина, 1/);

  const cod = customerOrderText(order());
  assert.match(cod, /Оплата при получении/);
  const waiting = customerOrderText(order({ payment_method: "transfer", status: "awaiting_transfer" }));
  assert.match(waiting, /ждёт подтверждения оплаты/);

  const admin = adminOrderText(order({ telegram_username: "ivan", tracking_number: "EP1" }));
  assert.match(admin, /Статус: новый/);
  assert.match(admin, /Telegram: @ivan/);
  assert.match(admin, /Трек: EP1/);
  assert.match(admin, /Заявка Европочты: вручную/);
});
