import { Buffer } from "node:buffer";
import { Bot, InlineKeyboard, InputFile, type Context } from "grammy";
import { KeychainApi } from "./api.js";
import { loadConfig } from "./config.js";
import {
  buildOrderPayload,
  cleanCustomText,
  contentElementIds,
  contentElements,
  createDraftSession,
  customTextLimit,
  defaultElementIds,
  defaultSizeId,
  findDesign,
  isCustomTextDesign,
  loopElements,
  orderSummary,
  requiresCarNumber,
  selectedLoopId,
  setLoopSide,
  toggleElement,
} from "./flow.js";
import { SessionStore } from "./session-store.js";
import type { Design, DraftSession } from "./types.js";

const config = loadConfig();
const api = new KeychainApi(config.apiBaseUrl, config.internalApiToken);
const sessions = new SessionStore(config.databaseUrl);
const bot = new Bot(config.telegramBotToken);

function chatId(ctx: Context): string {
  const id = ctx.chat?.id;
  if (id === undefined) throw new Error("Chat is not available");
  return String(id);
}

function telegramMeta(ctx: Context) {
  return {
    chatId: chatId(ctx),
    userId: ctx.from?.id ? String(ctx.from.id) : undefined,
    username: ctx.from?.username,
  };
}

function mainKeyboard(): InlineKeyboard {
  return new InlineKeyboard().text("Новый заказ", "start:new");
}

function cleanCarNumber(value: string): string {
  const compact = value.replace(/[\s-]+/g, "").toUpperCase();
  if (/^\d{4}[A-Z]{2}[1-7]$/.test(compact)) {
    return `${compact.slice(0, 4)} ${compact.slice(4, 6)}-${compact.slice(6)}`;
  }
  return compact;
}

async function saveAndReply(ctx: Context, session: DraftSession, text: string, keyboard?: InlineKeyboard): Promise<void> {
  await sessions.save(chatId(ctx), session);
  await ctx.reply(text, keyboard ? { reply_markup: keyboard } : undefined);
}

async function designsOrReply(ctx: Context): Promise<Design[] | null> {
  const designs = await api.getDesigns();
  if (!designs.length) {
    await ctx.reply("Пока нет доступных дизайнов. Сообщите оператору.");
    return null;
  }
  return designs;
}

async function beginNewOrder(ctx: Context): Promise<void> {
  const designs = await designsOrReply(ctx);
  if (!designs) return;

  const session = createDraftSession();
  await showDesignPicker(ctx, session, designs, "Выберите дизайн брелка.");
}

async function showDesignPicker(ctx: Context, session: DraftSession, designs: Design[], text: string): Promise<void> {
  session.step = "design";
  const keyboard = new InlineKeyboard();
  for (const design of designs) {
    keyboard.text(design.name, `design:${design.id}`).row();
  }
  if (session.data.designId) {
    keyboard.text("Назад к проверке", "edit:done").row();
  }
  keyboard.text("Отменить", "cancel");
  await saveAndReply(ctx, session, text, keyboard);
}

function applyDesignDefaults(session: DraftSession, design: Design): void {
  session.data.designId = design.id;
  session.data.sizeId = defaultSizeId(design);
  session.data.elements = defaultElementIds(design);
  if (isCustomTextDesign(design)) {
    session.data.carNumber = undefined;
  } else {
    session.data.printLine1 = undefined;
  }
}

async function showSizes(ctx: Context, session: DraftSession, design: Design, resetForDesign = true): Promise<void> {
  session.step = "size";
  if (resetForDesign) {
    applyDesignDefaults(session, design);
  }

  const keyboard = new InlineKeyboard();
  for (const size of design.sizes) {
    const price = size.price ? ` · ${size.price} BYN` : "";
    keyboard.text(`${size.label}${price}`, `size:${size.id}`).row();
  }
  if (session.editing) {
    keyboard.text("Назад к проверке", "edit:done").row();
  }
  keyboard.text("Отменить", "cancel");
  await saveAndReply(ctx, session, "Выберите размер.", keyboard);
}

async function showElements(ctx: Context, session: DraftSession, design: Design): Promise<void> {
  session.step = "elements";
  const keyboard = new InlineKeyboard();
  for (const element of contentElements(design)) {
    const selected = session.data.elements.includes(element.id) ? "[x]" : "[ ]";
    keyboard.text(`${selected} ${element.label}`, `el:${element.id}`).row();
  }
  keyboard.text("Далее", "el_done").row();
  if (session.editing) {
    keyboard.text("Назад к проверке", "edit:done").row();
  }
  keyboard.text("Отменить", "cancel");
  await saveAndReply(ctx, session, "Выберите элементы, которые попадут на брелок.", keyboard);
}

async function showLoopSide(ctx: Context, session: DraftSession, design: Design): Promise<void> {
  const loops = loopElements(design);
  if (!loops.length) {
    await askName(ctx, session);
    return;
  }

  session.step = "loop";
  const current = selectedLoopId(design, session.data);
  const keyboard = new InlineKeyboard();
  for (const loop of loops) {
    const selected = current === loop.id ? "[x]" : "[ ]";
    keyboard.text(`${selected} ${loop.label}`, `loop:${loop.id}`).row();
  }
  if (session.editing) {
    keyboard.text("Назад к проверке", "edit:done").row();
  }
  keyboard.text("Отменить", "cancel");
  await saveAndReply(ctx, session, "Выберите сторону ушка для кольца.", keyboard);
}

async function askPrintText(ctx: Context, session: DraftSession, design: Design): Promise<void> {
  session.step = "print_text";
  const limit = customTextLimit(design, session.data.sizeId);
  await saveAndReply(ctx, session, `Введите текст который вы хотите напечатать. Максимальная длина текста: ${limit} символов.`);
}

async function askName(ctx: Context, session: DraftSession): Promise<void> {
  session.step = "name";
  await saveAndReply(ctx, session, "Введите ваше имя.");
}

async function askCar(ctx: Context, session: DraftSession): Promise<void> {
  session.step = "car";
  await saveAndReply(ctx, session, "Введите номер авто РБ латиницей, например 1234 AB-7.");
}

async function showConfirmation(ctx: Context, session: DraftSession, design: Design): Promise<void> {
  session.step = "confirm";
  session.editing = false;
  await sessions.save(chatId(ctx), session);

  const keyboard = new InlineKeyboard()
    .text("Подтвердить", "confirm")
    .text("Изменить", "edit")
    .row()
    .text("Отменить", "cancel");
  const caption = orderSummary(design, session.data);
  try {
    const payload = buildOrderPayload(session.data, session.draftId, telegramMeta(ctx));
    const buffer = await api.renderOrderPreview(payload);
    await ctx.replyWithPhoto(new InputFile(buffer, `${session.draftId}.png`), {
      caption,
      reply_markup: keyboard,
    });
    return;
  } catch (error) {
    console.warn(error);
  }

  const previewUrl = api.previewUrl(design);

  if (previewUrl) {
    try {
      const response = await fetch(previewUrl);
      if (!response.ok) throw new Error(`Preview request failed: ${response.status}`);
      const buffer = Buffer.from(await response.arrayBuffer());
      await ctx.replyWithPhoto(new InputFile(buffer, `${design.id}.png`), {
        caption,
        reply_markup: keyboard,
      });
      return;
    } catch (error) {
      console.warn(error);
    }
  }

  await ctx.reply(caption, { reply_markup: keyboard });
}

async function showEditMenu(ctx: Context, session: DraftSession, design: Design): Promise<void> {
  session.step = "confirm";
  session.editing = false;

  const keyboard = new InlineKeyboard()
    .text("Дизайн", "edit:design")
    .text("Размер", "edit:size")
    .row();

  if (contentElements(design).length > 0) {
    keyboard.text("Элементы", "edit:elements").row();
  }
  if (loopElements(design).length > 0) {
    keyboard.text("Ушко", "edit:loop").row();
  }
  if (isCustomTextDesign(design)) {
    keyboard.text("Текст", "edit:print_text").row();
  }

  keyboard.text("Имя", "edit:name").row();

  if (requiresCarNumber(design, session.data)) {
    keyboard.text("Номер авто", "edit:car").row();
  }

  keyboard.text("Назад к проверке", "edit:done").row().text("Отменить", "cancel");
  await saveAndReply(ctx, session, "Что нужно исправить?", keyboard);
}

async function finishEditOrContinue(ctx: Context, session: DraftSession, design: Design, next: () => Promise<void>): Promise<void> {
  if (!session.editing) {
    await next();
    return;
  }

  if (isCustomTextDesign(design)) {
    const currentText = cleanCustomText(session.data.printLine1 || "");
    if (!currentText || currentText.length > customTextLimit(design, session.data.sizeId)) {
      await askPrintText(ctx, session, design);
      return;
    }
  }

  if (requiresCarNumber(design, session.data) && !session.data.carNumber) {
    await askCar(ctx, session);
    return;
  }

  await showConfirmation(ctx, session, design);
}

async function currentSessionAndDesign(ctx: Context): Promise<{ session: DraftSession; design: Design } | null> {
  const session = await sessions.get(chatId(ctx));
  if (!session) {
    await ctx.reply("Черновик не найден. Начните новый заказ.", { reply_markup: mainKeyboard() });
    return null;
  }
  const designs = await designsOrReply(ctx);
  if (!designs) return null;
  const design = findDesign(designs, session.data.designId);
  if (!design) {
    await ctx.reply("Выбранный дизайн больше недоступен. Начните заказ заново.", { reply_markup: mainKeyboard() });
    await sessions.clear(chatId(ctx));
    return null;
  }
  return { session, design };
}

bot.command("start", async (ctx) => {
  await ctx.reply("Привет! Здесь можно оформить заказ на брелок.", { reply_markup: mainKeyboard() });
});

bot.command("new", beginNewOrder);

bot.command("cancel", async (ctx) => {
  await sessions.clear(chatId(ctx));
  await ctx.reply("Заказ отменен.", { reply_markup: mainKeyboard() });
});

bot.callbackQuery("start:new", async (ctx) => {
  await ctx.answerCallbackQuery();
  await beginNewOrder(ctx);
});

bot.callbackQuery("cancel", async (ctx) => {
  await ctx.answerCallbackQuery();
  await sessions.clear(chatId(ctx));
  await ctx.reply("Заказ отменен.", { reply_markup: mainKeyboard() });
});

bot.callbackQuery("edit", async (ctx) => {
  await ctx.answerCallbackQuery();
  const current = await currentSessionAndDesign(ctx);
  if (!current) return;
  await showEditMenu(ctx, current.session, current.design);
});

bot.callbackQuery(/^edit:(.+)$/, async (ctx) => {
  await ctx.answerCallbackQuery();
  const current = await currentSessionAndDesign(ctx);
  if (!current) return;

  const field = ctx.match[1];
  if (field === "done") {
    await finishEditOrContinue(ctx, current.session, current.design, async () => {
      await showConfirmation(ctx, current.session, current.design);
    });
    return;
  }

  current.session.editing = true;
  if (field === "design") {
    const designs = await designsOrReply(ctx);
    if (!designs) return;
    await showDesignPicker(ctx, current.session, designs, "Выберите правильный дизайн.");
    return;
  }
  if (field === "size") {
    await showSizes(ctx, current.session, current.design, false);
    return;
  }
  if (field === "elements") {
    await showElements(ctx, current.session, current.design);
    return;
  }
  if (field === "loop") {
    await showLoopSide(ctx, current.session, current.design);
    return;
  }
  if (field === "print_text" && isCustomTextDesign(current.design)) {
    await askPrintText(ctx, current.session, current.design);
    return;
  }
  if (field === "name") {
    await askName(ctx, current.session);
    return;
  }
  if (field === "car" && requiresCarNumber(current.design, current.session.data)) {
    await askCar(ctx, current.session);
    return;
  }

  current.session.editing = false;
  await showEditMenu(ctx, current.session, current.design);
});

bot.callbackQuery(/^design:(.+)$/, async (ctx) => {
  await ctx.answerCallbackQuery();
  const session = await sessions.get(chatId(ctx));
  if (!session) return beginNewOrder(ctx);
  const designs = await designsOrReply(ctx);
  const design = designs?.find((item) => item.id === ctx.match[1]);
  if (!design) {
    await ctx.reply("Дизайн не найден. Выберите другой.");
    return beginNewOrder(ctx);
  }
  await showSizes(ctx, session, design);
});

bot.callbackQuery(/^size:(.+)$/, async (ctx) => {
  await ctx.answerCallbackQuery();
  const current = await currentSessionAndDesign(ctx);
  if (!current) return;
  current.session.data.sizeId = ctx.match[1];
  if (!current.session.editing) {
    current.session.data.elements = [];
  }
  if (current.session.editing) {
    await finishEditOrContinue(ctx, current.session, current.design, async () => {
      await showConfirmation(ctx, current.session, current.design);
    });
    return;
  }
  if (isCustomTextDesign(current.design)) {
    await askPrintText(ctx, current.session, current.design);
    return;
  }
  await askName(ctx, current.session);
});

bot.callbackQuery(/^el:(.+)$/, async (ctx) => {
  await ctx.answerCallbackQuery();
  const current = await currentSessionAndDesign(ctx);
  if (!current) return;
  current.session.data = toggleElement(current.session.data, ctx.match[1]);
  await showElements(ctx, current.session, current.design);
});

bot.callbackQuery("el_done", async (ctx) => {
  await ctx.answerCallbackQuery();
  const current = await currentSessionAndDesign(ctx);
  if (!current) return;
  if (current.design.layout === "stacked_plate" && contentElementIds(current.design, current.session.data).length === 0) {
    await ctx.reply("Выберите хотя бы один блок: имя или авто.");
    await showElements(ctx, current.session, current.design);
    return;
  }
  if (current.session.editing) {
    await showConfirmation(ctx, current.session, current.design);
    return;
  }
  await showLoopSide(ctx, current.session, current.design);
});

bot.callbackQuery(/^loop:(.+)$/, async (ctx) => {
  await ctx.answerCallbackQuery();
  const current = await currentSessionAndDesign(ctx);
  if (!current) return;
  current.session.data = setLoopSide(current.design, current.session.data, ctx.match[1]);
  if (current.session.editing) {
    await showConfirmation(ctx, current.session, current.design);
    return;
  }
  await askName(ctx, current.session);
});

bot.callbackQuery("confirm", async (ctx) => {
  await ctx.answerCallbackQuery();
  const current = await currentSessionAndDesign(ctx);
  if (!current) return;

  try {
    const payload = buildOrderPayload(current.session.data, current.session.draftId, telegramMeta(ctx));
    const result = await api.createOrder(payload);
    await sessions.clear(chatId(ctx));
    await ctx.reply(`Заказ ${result.order.id} создан и передан администратору. Можете пройти на оплату.`, { reply_markup: mainKeyboard() });
  } catch (error) {
    await ctx.reply(`Не получилось создать заказ: ${(error as Error).message}`);
  }
});

bot.on("message:text", async (ctx) => {
  const session = await sessions.get(chatId(ctx));
  if (!session) {
    await ctx.reply("Начните новый заказ.", { reply_markup: mainKeyboard() });
    return;
  }

  const text = ctx.message.text.trim();
  const current = await currentSessionAndDesign(ctx);
  if (!current) return;

  if (session.step === "print_text") {
    const cleaned = cleanCustomText(text);
    const limit = customTextLimit(current.design, session.data.sizeId);
    if (!cleaned) {
      await ctx.reply("Введите текст для печати.");
      return;
    }
    if (cleaned.length > limit) {
      await ctx.reply(`Слишком длинный текст. Максимальная длина: ${limit} символов.`);
      return;
    }
    session.data.printLine1 = cleaned;
    if (session.editing) {
      await showConfirmation(ctx, session, current.design);
      return;
    }
    await askName(ctx, session);
    return;
  }

  if (session.step === "name") {
    session.data.customerName = text;
    if (session.editing) {
      await showConfirmation(ctx, session, current.design);
      return;
    }
    if (requiresCarNumber(current.design, session.data)) {
      await askCar(ctx, session);
    } else {
      await showConfirmation(ctx, session, current.design);
    }
    return;
  }

  if (session.step === "car") {
    session.data.carNumber = cleanCarNumber(text);
    await showConfirmation(ctx, session, current.design);
    return;
  }

  await ctx.reply("Используйте кнопки под сообщением или /cancel.");
});

async function main(): Promise<void> {
  await sessions.init();
  await bot.start({ drop_pending_updates: true });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
