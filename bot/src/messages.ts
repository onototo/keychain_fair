import { CART_PAGE_SIZE, formatByn, pageSlice, shortId, units } from "./cart.js";
import type { CartLine, IndexedProduct, Order, PaymentMethod, Session } from "./types.js";

export const PAYMENT_LABELS: Record<PaymentMethod, string> = {
  cod: "наложенный платёж",
  transfer: "перевод на карту",
  online: "карта или Apple Pay",
};

export const STATUS_LABELS: Record<string, string> = {
  new: "новый",
  awaiting_transfer: "ждёт оплату",
  paid: "оплачен",
  shipped: "отправлен",
  cancelled: "отменён",
};

type IndexedCatalog = {
  maxUnits: number;
  byCode: Map<string, IndexedProduct>;
};

export function renderCart(cart: CartLine[], catalog: IndexedCatalog, page: number): { text: string; page: number; pages: number } {
  if (!cart.length) return { text: "Корзина пуста.", page: 0, pages: 1 };
  const sliced = pageSlice(cart, page, CART_PAGE_SIZE);
  const rows = sliced.items.map((line) => {
    const product = catalog.byCode.get(shortId(line.productId));
    const title = product?.title ?? "Товар снят с каталога";
    const lineKopecks = (product?.price_kopecks ?? 0) * line.quantity;
    return `${title} × ${line.quantity} — ${formatByn(lineKopecks)}`;
  });
  const total = cart.reduce((sum, line) => {
    const product = catalog.byCode.get(shortId(line.productId));
    return sum + (product?.price_kopecks ?? 0) * line.quantity;
  }, 0);
  const lines = ["Корзина:", ...rows, `Итого: ${formatByn(total)}`, `${units(cart)} из ${catalog.maxUnits} штук`];
  if (sliced.pages > 1) lines.push(`Страница ${sliced.page + 1} из ${sliced.pages}`);
  return { text: lines.join("\n"), page: sliced.page, pages: sliced.pages };
}

export function checkoutText(session: Session, catalog: IndexedCatalog, officeLabel: string): string {
  const cart = renderCart(session.cart, catalog, 0);
  return [
    "Проверьте заказ:",
    cart.text.replace(/^Корзина:\n/, ""),
    `Оплата: ${PAYMENT_LABELS[session.paymentMethod || "cod"]}`,
    `Отделение: ${officeLabel}`,
    `Имя: ${session.customerName || ""}`,
    `Телефон: ${session.phone || ""}`,
  ].join("\n");
}

export function customerOrderText(order: Order): string {
  const lines = [
    `Заказ №${order.order_number_label} принят.`,
    ...order.items.map((item) => `${item.title} × ${item.quantity} — ${item.line_byn}`),
    `Итого: ${order.total_byn}`,
    `Оплата: ${PAYMENT_LABELS[order.payment_method as PaymentMethod] ?? order.payment_method}`,
    `Европочта №${order.office_number}, ${order.office_city}, ${order.office_address}`,
  ];
  if (order.payment_method === "cod") {
    lines.push("Оплата при получении. Когда посылка уйдёт, здесь появится трек-номер.");
  } else if (order.status === "awaiting_transfer") {
    lines.push("Заказ ждёт подтверждения оплаты.");
  }
  return lines.join("\n");
}

export function adminOrderText(order: Order): string {
  const lines = [
    `Заказ №${order.order_number_label}`,
    `Статус: ${STATUS_LABELS[order.status] ?? order.status}`,
    `Оплата: ${PAYMENT_LABELS[order.payment_method as PaymentMethod] ?? order.payment_method}`,
    ...order.items.map((item) => `${item.title} × ${item.quantity} — ${item.line_byn}`),
    `Итого: ${order.total_byn}`,
    order.customer_name,
    order.phone,
    `Европочта №${order.office_number}, ${order.office_city}, ${order.office_address}`,
  ];
  if (order.telegram_username) lines.push(`Telegram: @${order.telegram_username}`);
  if (order.tracking_number) lines.push(`Трек: ${order.tracking_number}`);
  lines.push(order.shipment_status === "manual" ? "Заявка Европочты: вручную" : `Заявка: ${order.shipment_status}`);
  return lines.join("\n");
}
