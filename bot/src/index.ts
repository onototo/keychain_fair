import { randomUUID } from "node:crypto";
import { Bot, InlineKeyboard, InputFile, Keyboard, type Context } from "grammy";
import { KeychainApi } from "./api.js";
import { CART_PAGE_SIZE, buttonLabel, changeUnits, pageSlice, quantityOf, shortId } from "./cart.js";
import { loadConfig } from "./config.js";
import { adminOrderText, checkoutText, customerOrderText, renderCart } from "./messages.js";
import { emptySession, type Catalog, type IndexedCatalog, type IndexedProduct, type Office, type Order, type PaymentMethod, type Session } from "./types.js";

const config = loadConfig();
const api = new KeychainApi(config.apiBaseUrl, config.internalApiToken);
const bot = new Bot(config.telegramBotToken);
const OFFICE_PAGE_SIZE = 6;

function chatId(ctx: Context): string {
  const id = ctx.chat?.id ?? ctx.from?.id;
  if (id === undefined) throw new Error("Chat is not available");
  return String(id);
}

function isAdmin(ctx: Context): boolean {
  return config.adminUserIds.includes(String(ctx.from?.id ?? ""));
}

function indexCatalog(catalog: Catalog): IndexedCatalog {
  const productsByCode = new Map<string, IndexedProduct>();
  const categories = catalog.categories.map((category) => {
    const products = category.products.map((product) => {
      const code = shortId(product.id);
      if (productsByCode.has(code)) throw new Error(`Catalog code collision: ${product.id}`);
      const indexed = { ...product, code };
      productsByCode.set(code, indexed);
      return indexed;
    });
    return { id: category.id, code: shortId(`cat:${category.id}`), title: category.title, products };
  });
  return {
    maxUnits: catalog.max_units,
    byCode: productsByCode,
    byCategory: new Map(categories.map((category) => [category.code, category])),
    categories,
  };
}

async function loadCatalog(): Promise<IndexedCatalog> {
  return indexCatalog(await api.getCatalog());
}

async function loadSession(ctx: Context): Promise<Session> {
  return (await api.getSession(chatId(ctx))) ?? emptySession();
}

async function saveSession(ctx: Context, session: Session): Promise<void> {
  await api.saveSession(chatId(ctx), session);
}

function replyKeyboard(ctx: Context): Keyboard {
  const keyboard = new Keyboard().text("Каталог").text("Корзина");
  if (isAdmin(ctx)) keyboard.text("Заказы");
  return keyboard.resized().persistent();
}

function productKeyboard(code: string, quantity: number): InlineKeyboard {
  const keyboard = new InlineKeyboard();
  if (quantity > 0) keyboard.text("−", `c:d:${code}`);
  keyboard.text(quantity > 0 ? `${quantity} шт` : "В корзину", quantity > 0 ? `c:n:${code}` : `c:a:${code}`);
  if (quantity > 0) keyboard.text("+", `c:a:${code}`);
  return keyboard.row().text("Корзина", "cart").row().text("К категориям", "cats");
}

function cartKeyboard(session: Session, page: number, pages: number): InlineKeyboard {
  const keyboard = new InlineKeyboard();
  const slice = pageSlice(session.cart, page, CART_PAGE_SIZE);
  for (const line of slice.items) {
    const code = shortId(line.productId);
    keyboard.text("−", `c:d:${code}`).text(String(line.quantity), `c:n:${code}`).text("+", `c:a:${code}`).text("✕", `c:x:${code}`).row();
  }
  if (pages > 1) {
    if (slice.page > 0) keyboard.text("←", `cpg:${slice.page - 1}`);
    if (slice.page + 1 < pages) keyboard.text("→", `cpg:${slice.page + 1}`);
    keyboard.row();
  }
  if (session.cart.length) {
    keyboard.text("Оформить", "checkout").row();
    keyboard.text("Очистить", "clear").row();
  }
  return keyboard.text("В каталог", "cats");
}

function adminKeyboard(order: Order): InlineKeyboard {
  const keyboard = new InlineKeyboard();
  if (order.status === "awaiting_transfer") keyboard.text("Оплата пришла", `ap:${order.id}`).row();
  if (order.status === "new" || order.status === "paid") keyboard.text("Отправлено", `as:${order.id}`).row();
  if (order.status === "new" || order.status === "awaiting_transfer" || order.status === "paid") {
    keyboard.text("Отменить", `ax:${order.id}`).row();
  }
  return keyboard.text("К спискам", "al");
}

async function answer(ctx: Context, text?: string, alert = false): Promise<void> {
  if (!ctx.callbackQuery) return;
  await ctx.answerCallbackQuery(text ? { text, show_alert: alert } : undefined);
}

async function showCategories(ctx: Context): Promise<void> {
  const catalog = await loadCatalog();
  if (!catalog.categories.length) {
    await ctx.reply("Каталог пока пуст. Добавьте папку с товаром.");
    return;
  }
  const keyboard = new InlineKeyboard();
  for (const category of catalog.categories) keyboard.text(buttonLabel(category.title), `cat:${category.code}`).row();
  keyboard.text("Корзина", "cart");
  await ctx.reply("Категории:", { reply_markup: keyboard });
}

async function showCategory(ctx: Context, code: string, page: number): Promise<void> {
  const catalog = await loadCatalog();
  const category = catalog.byCategory.get(code);
  if (!category) {
    await answer(ctx, "Категория не найдена", true);
    return;
  }
  await answer(ctx);
  const slice = pageSlice(category.products, page, CART_PAGE_SIZE);
  const keyboard = new InlineKeyboard();
  for (const product of slice.items) keyboard.text(buttonLabel(`${product.title} · ${product.price_byn}`), `p:${product.code}`).row();
  if (slice.pages > 1) {
    if (slice.page > 0) keyboard.text("←", `pg:${code}:${slice.page - 1}`);
    if (slice.page + 1 < slice.pages) keyboard.text("→", `pg:${code}:${slice.page + 1}`);
    keyboard.row();
  }
  keyboard.text("К категориям", "cats");
  await ctx.reply(category.title, { reply_markup: keyboard });
}

async function showProduct(ctx: Context, session: Session, code: string): Promise<void> {
  const catalog = await loadCatalog();
  const product = catalog.byCode.get(code);
  if (!product) {
    await answer(ctx, "Товар не найден", true);
    return;
  }
  await answer(ctx);
  const quantity = quantityOf(session.cart, product.id);
  const caption = [product.title, product.price_byn, product.description].filter(Boolean).join("\n");
  const keyboard = productKeyboard(code, quantity);
  const slash = product.id.indexOf("/");
  if (product.has_image && slash > 0) {
    const image = await api.getCover(product.id.slice(0, slash), product.id.slice(slash + 1));
    if (image) {
      await ctx.replyWithPhoto(new InputFile(image, "product.jpg"), { caption, reply_markup: keyboard });
      return;
    }
  }
  await ctx.reply(caption, { reply_markup: keyboard });
}

async function showCart(ctx: Context, session: Session, preferEdit: boolean): Promise<void> {
  const catalog = await loadCatalog();
  const rendered = renderCart(session.cart, catalog, session.cartPage ?? 0);
  session.cartPage = rendered.page;
  const keyboard = cartKeyboard(session, rendered.page, rendered.pages);
  const message = ctx.callbackQuery?.message;
  if (preferEdit && message && "text" in message && message.text?.startsWith("Корзина")) {
    await answer(ctx);
    await ctx.editMessageText(rendered.text, { reply_markup: keyboard });
    return;
  }
  await answer(ctx);
  await ctx.reply(rendered.text, { reply_markup: keyboard });
}

async function changeCart(ctx: Context, session: Session, action: string, code: string): Promise<void> {
  const catalog = await loadCatalog();
  const product = catalog.byCode.get(code);
  if (!product) {
    await answer(ctx, "Товар больше не доступен", true);
    return;
  }
  if (action === "n") {
    await answer(ctx);
    return;
  }
  const delta = action === "d" ? -1 : action === "x" ? -10_000 : 1;
  const result = changeUnits(session.cart, product.id, delta, catalog.maxUnits);
  if (result.error) {
    await answer(ctx, result.error, true);
    return;
  }
  session.cart = result.cart;
  await saveSession(ctx, session);
  await answer(ctx, action === "a" ? "Добавлено" : undefined);
  const message = ctx.callbackQuery?.message;
  if (message && "text" in message && message.text?.startsWith("Корзина")) {
    const rendered = renderCart(session.cart, catalog, session.cartPage ?? 0);
    session.cartPage = rendered.page;
    await saveSession(ctx, session);
    await ctx.editMessageText(rendered.text, { reply_markup: cartKeyboard(session, rendered.page, rendered.pages) });
    return;
  }
  const quantity = quantityOf(session.cart, product.id);
  await ctx.editMessageReplyMarkup({ reply_markup: productKeyboard(code, quantity) });
}

async function askPayment(ctx: Context, session: Session): Promise<void> {
  if (!session.cart.length) {
    await answer(ctx, "Корзина пуста", true);
    return;
  }
  await answer(ctx);
  session.step = "browse";
  await saveSession(ctx, session);
  const keyboard = new InlineKeyboard().text("Наложенный платёж", "pay:cod").row().text("Перевод на карту", "pay:transfer");
  if (config.onlinePaymentUrl) keyboard.row().text("Картой или Apple Pay", "pay:online");
  keyboard.row().text("Назад в корзину", "cart");
  await ctx.reply("Как оплатить заказ?", { reply_markup: keyboard });
}

async function choosePayment(ctx: Context, session: Session, method: PaymentMethod): Promise<void> {
  if (method === "transfer" && !config.paymentCardNumber) {
    await answer(ctx, "Перевод на карту пока не настроен", true);
    return;
  }
  if (method === "online" && !config.onlinePaymentUrl) {
    await answer(ctx, "Оплата картой пока не настроена", true);
    return;
  }
  await answer(ctx);
  session.paymentMethod = method;
  session.step = "city";
  await saveSession(ctx, session);
  const catalog = await loadCatalog();
  const total = renderCart(session.cart, catalog, 0).text.split("\n").find((line) => line.startsWith("Итого:")) ?? "";
  if (method === "transfer") {
    const holder = config.paymentCardHolder ? `, ${config.paymentCardHolder}` : "";
    await ctx.reply(`Переведите ${total.replace("Итого: ", "")} на карту ${config.paymentCardNumber}${holder}.\nЗаказ будет ждать, пока оплата не подтвердится.`);
  }
  if (method === "online") {
    await ctx.reply(`Оплата картой или Apple Pay:\n${config.onlinePaymentUrl}`);
  }
  await ctx.reply("Напишите город, где заберёте посылку. Например: Минск");
}

async function showOffices(ctx: Context, session: Session, page: number): Promise<void> {
  const offices = session.offices ?? [];
  const slice = pageSlice(offices, page, OFFICE_PAGE_SIZE);
  session.officePage = slice.page;
  const keyboard = new InlineKeyboard();
  slice.items.forEach((office, index) => {
    const offset = slice.page * OFFICE_PAGE_SIZE + index;
    keyboard.text(buttonLabel(`№${office.number} · ${office.address}`), `of:${offset}`).row();
  });
  if (slice.page > 0) keyboard.text("←", `op:${slice.page - 1}`);
  if (slice.page + 1 < slice.pages) keyboard.text("→", `op:${slice.page + 1}`);
  keyboard.row().text("Другой город", "city");
  await ctx.reply(`Найдено отделений: ${offices.length}. Выберите:`, { reply_markup: keyboard });
}

async function askName(ctx: Context, session: Session, office: Office): Promise<void> {
  session.officeId = office.id;
  session.step = "name";
  await saveSession(ctx, session);
  await ctx.reply(`Отделение №${office.number}, ${office.city}, ${office.address}\nНапишите имя и фамилию получателя.`);
}

async function showConfirm(ctx: Context, session: Session): Promise<void> {
  const catalog = await loadCatalog();
  const office = (session.offices ?? []).find((item) => item.id === session.officeId);
  const officeLabel = office ? `№${office.number}, ${office.city}, ${office.address}` : "не выбрано";
  if (!session.draftId) session.draftId = randomUUID();
  session.step = "confirm";
  await saveSession(ctx, session);
  const keyboard = new InlineKeyboard().text("Подтвердить", "ok").row().text("Изменить оплату", "backpay");
  await ctx.reply(checkoutText(session, catalog, officeLabel), { reply_markup: keyboard });
}

async function confirmOrder(ctx: Context, session: Session): Promise<void> {
  if (!session.cart.length || !session.paymentMethod || !session.officeId || !session.customerName || !session.phone) {
    await answer(ctx, "Заказ заполнен не до конца", true);
    return;
  }
  await answer(ctx);
  if (!session.draftId) session.draftId = randomUUID();
  let result;
  try {
    result = await api.createOrder({
    customer_name: session.customerName,
    phone: session.phone,
    payment_method: session.paymentMethod,
    office_id: session.officeId,
    items: session.cart.map((line) => ({ product_id: line.productId, quantity: line.quantity })),
    idempotency_key: `tg:${chatId(ctx)}:${session.draftId}`,
    telegram_chat_id: chatId(ctx),
    telegram_user_id: ctx.from?.id ? String(ctx.from.id) : undefined,
    telegram_username: ctx.from?.username,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Не получилось оформить заказ.";
    if (message.includes("Имя")) session.step = "name";
    else if (message.toLowerCase().includes("телефон")) session.step = "phone";
    await saveSession(ctx, session);
    await ctx.reply(message);
    return;
  }
  const finished = emptySession();
  await saveSession(ctx, finished);
  await ctx.reply(customerOrderText(result.order));
  await notifyAdmins(result.order);
}

async function notifyAdmins(order: Order): Promise<void> {
  const keyboard = new InlineKeyboard().text("Открыть", `ao:${order.id}`);
  for (const adminId of config.adminUserIds) {
    try {
      await bot.api.sendMessage(adminId, adminOrderText(order), { reply_markup: keyboard });
    } catch (error) {
      console.error("admin notify failed", error);
    }
  }
}

async function notifyCustomer(order: Order, text: string): Promise<void> {
  if (!order.telegram_chat_id) return;
  try {
    await bot.api.sendMessage(order.telegram_chat_id, text);
  } catch (error) {
    console.error("customer notify failed", error);
  }
}

async function showAdminHome(ctx: Context): Promise<void> {
  if (!isAdmin(ctx)) {
    await answer(ctx, "Недоступно", true);
    return;
  }
  await answer(ctx);
  const keyboard = new InlineKeyboard()
    .text("Новые", "al:new:0").row()
    .text("Ждут оплату", "al:awaiting_transfer:0").row()
    .text("Оплачены", "al:paid:0").row()
    .text("Отправленные", "al:shipped:0");
  await ctx.reply("Заказы:", { reply_markup: keyboard });
}

async function showAdminList(ctx: Context, status: string, offset: number): Promise<void> {
  if (!isAdmin(ctx)) {
    await answer(ctx, "Недоступно", true);
    return;
  }
  await answer(ctx);
  const orders = await api.listOrders(status, 10, offset);
  const keyboard = new InlineKeyboard();
  if (!orders.length) {
    keyboard.text("К спискам", "al");
    await ctx.reply("В этом списке пусто.", { reply_markup: keyboard });
    return;
  }
  for (const order of orders) {
    keyboard.text(buttonLabel(`№${order.order_number_label} · ${order.total_byn} · ${order.office_city}`), `ao:${order.id}`).row();
  }
  if (offset > 0) keyboard.text("←", `al:${status}:${Math.max(offset - 10, 0)}`);
  if (orders.length === 10) keyboard.text("→", `al:${status}:${offset + 10}`);
  keyboard.row().text("К спискам", "al");
  await ctx.reply("Выберите заказ:", { reply_markup: keyboard });
}

async function showAdminOrder(ctx: Context, order: Order): Promise<void> {
  const text = adminOrderText(order);
  const keyboard = adminKeyboard(order);
  const message = ctx.callbackQuery?.message;
  if (message && "text" in message) {
    await answer(ctx);
    await ctx.editMessageText(text, { reply_markup: keyboard });
    return;
  }
  await answer(ctx);
  await ctx.reply(text, { reply_markup: keyboard });
}

async function onText(ctx: Context): Promise<void> {
  const text = ctx.message?.text?.trim() ?? "";
  if (text === "/start" || text.startsWith("/start@") || text.startsWith("/start ")) return;
  const session = await loadSession(ctx);
  if (text === "Каталог" || text === "/catalog") {
    session.step = "browse";
    await saveSession(ctx, session);
    await showCategories(ctx);
    return;
  }
  if (text === "Корзина") {
    session.step = "browse";
    await saveSession(ctx, session);
    await showCart(ctx, session, false);
    return;
  }
  if (text === "Заказы" || text === "/orders") {
    await showAdminHome(ctx);
    return;
  }
  if (session.step === "admin_tracking" && isAdmin(ctx) && session.adminOrderId) {
    const tracking = text === "-" ? "" : text;
    const order = await api.ship(session.adminOrderId, tracking);
    session.step = "browse";
    session.adminOrderId = undefined;
    await saveSession(ctx, session);
    const track = order.tracking_number ? ` Трек: ${order.tracking_number}.` : "";
    await ctx.reply(`Заказ №${order.order_number_label} отмечен отправленным.${track}`);
    await notifyCustomer(order, `Заказ №${order.order_number_label} отправлен.${track}`);
    return;
  }
  if (session.step === "city") {
    const offices = await api.searchOffices(text);
    if (!offices.length) {
      await ctx.reply("Отделения не нашлись. Напишите город ещё раз, например: Минск");
      return;
    }
    session.offices = offices;
    session.officePage = 0;
    await saveSession(ctx, session);
    await showOffices(ctx, session, 0);
    return;
  }
  if (session.step === "name") {
    session.customerName = text;
    session.step = "phone";
    await saveSession(ctx, session);
    await ctx.reply("Напишите телефон: +375291234567");
    return;
  }
  if (session.step === "phone") {
    session.phone = text;
    await showConfirm(ctx, session);
    return;
  }
  await ctx.reply("Откройте каталог кнопкой внизу.");
}

bot.command("start", async (ctx) => {
  const session = await loadSession(ctx);
  session.step = "browse";
  await saveSession(ctx, session);
  await ctx.reply("Можно выбрать несколько игрушек. Заказ заберёте в отделении Европочты.", {
    reply_markup: replyKeyboard(ctx),
  });
  await showCategories(ctx);
});

bot.on("message:text", async (ctx) => {
  try {
    await onText(ctx);
  } catch (error) {
    await ctx.reply(error instanceof Error ? error.message : "Не получилось продолжить заказ.");
  }
});

bot.on("callback_query:data", async (ctx) => {
  const data = ctx.callbackQuery.data;
  const session = await loadSession(ctx);
  try {
    if (data.startsWith("al") || data.startsWith("ao:") || data.startsWith("ap:") || data.startsWith("as:") || data.startsWith("ax:")) {
      if (!isAdmin(ctx)) {
        await answer(ctx, "Недоступно", true);
        return;
      }
    }
    if (data === "cats") {
      await answer(ctx);
      await showCategories(ctx);
      return;
    }
    if (data.startsWith("cat:")) return showCategory(ctx, data.slice(4), 0);
    if (data.startsWith("pg:")) {
      const [, code, page] = data.split(":");
      return showCategory(ctx, code, Number(page) || 0);
    }
    if (data.startsWith("p:")) return showProduct(ctx, session, data.slice(2));
    if (data.startsWith("c:")) {
      const [, action, code] = data.split(":");
      return changeCart(ctx, session, action, code);
    }
    if (data === "cart") return showCart(ctx, session, true);
    if (data.startsWith("cpg:")) {
      session.cartPage = Number(data.slice(4)) || 0;
      await saveSession(ctx, session);
      return showCart(ctx, session, true);
    }
    if (data === "clear") {
      session.cart = [];
      session.cartPage = 0;
      await saveSession(ctx, session);
      return showCart(ctx, session, true);
    }
    if (data === "checkout" || data === "backpay") return askPayment(ctx, session);
    if (data === "pay:cod" || data === "pay:transfer" || data === "pay:online") {
      return choosePayment(ctx, session, data.slice(4) as PaymentMethod);
    }
    if (data === "city") {
      await answer(ctx);
      session.step = "city";
      await saveSession(ctx, session);
      await ctx.reply("Напишите город, где заберёте посылку. Например: Минск");
      return;
    }
    if (data.startsWith("op:")) {
      await answer(ctx);
      await showOffices(ctx, session, Number(data.slice(3)) || 0);
      return;
    }
    if (data.startsWith("of:")) {
      const office = session.offices?.[Number(data.slice(3))];
      if (!office) {
        await answer(ctx, "Выберите отделение ещё раз", true);
        return;
      }
      await answer(ctx);
      return askName(ctx, session, office);
    }
    if (data === "ok") return confirmOrder(ctx, session);
    if (data === "al") return showAdminHome(ctx);
    if (data.startsWith("al:")) {
      const [, status, offset] = data.split(":");
      return showAdminList(ctx, status, Number(offset) || 0);
    }
    if (data.startsWith("ao:")) return showAdminOrder(ctx, await api.getOrder(data.slice(3)));
    if (data.startsWith("ap:")) {
      const order = await api.markPaid(data.slice(3));
      await showAdminOrder(ctx, order);
      await notifyCustomer(order, `Оплата заказа №${order.order_number_label} получена.`);
      return;
    }
    if (data.startsWith("as:")) {
      session.step = "admin_tracking";
      session.adminOrderId = data.slice(3);
      await saveSession(ctx, session);
      await answer(ctx);
      await ctx.reply("Напишите трек-номер. Если его ещё нет, отправьте «-».");
      return;
    }
    if (data.startsWith("ax:")) {
      const order = await api.cancel(data.slice(3));
      await showAdminOrder(ctx, order);
      await notifyCustomer(order, `Заказ №${order.order_number_label} отменён.`);
      return;
    }
    await answer(ctx);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Не получилось выполнить действие";
    const answered = ctx.callbackQuery
      ? await ctx.answerCallbackQuery({ text: message.slice(0, 180), show_alert: true }).then(() => true).catch(() => false)
      : false;
    if (!answered) await ctx.reply(message).catch(() => undefined);
  }
});

bot.catch((error) => {
  console.error(error);
});

bot.start();
