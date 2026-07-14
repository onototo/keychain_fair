import { randomUUID } from "node:crypto";
import type { Design, DraftData, DraftSession, OrderPayload, TelegramUserMeta } from "./types.js";

const LOOP_KINDS = new Set(["loop_side"]);

export function createDraftSession(): DraftSession {
  return {
    draftId: randomUUID(),
    step: "design",
    data: { elements: [] },
  };
}

export function findDesign(designs: Design[], designId?: string): Design | null {
  return designs.find((design) => design.id === designId) || null;
}

export function defaultSizeId(design: Design): string {
  return design.default_size_id || design.sizes[0]?.id || "";
}

export function selectedSize(design: Design, sizeId?: string) {
  return design.sizes.find((size) => size.id === sizeId) || design.sizes.find((size) => size.id === defaultSizeId(design)) || design.sizes[0];
}

export function defaultElementIds(design: Design): string[] {
  return [];
}

export function isCustomTextDesign(design: Design): boolean {
  return design.print_mode === "custom_text";
}

export function customTextLimit(design: Design, sizeId?: string): number {
  const size = selectedSize(design, sizeId);
  return size?.custom_text_limits?.max_total_chars || 48;
}

export function cleanCustomText(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

export function contentElements(design: Design) {
  return design.elements.filter((element) => !LOOP_KINDS.has(element.kind || ""));
}

export function loopElements(design: Design) {
  return design.elements.filter((element) => LOOP_KINDS.has(element.kind || ""));
}

export function contentElementIds(design: Design, data: DraftData): string[] {
  const contentIds = new Set(contentElements(design).map((element) => element.id));
  return data.elements.filter((elementId) => contentIds.has(elementId));
}

export function selectedLoopId(design: Design, data: DraftData): string | null {
  const loopIds = new Set(loopElements(design).map((element) => element.id));
  return data.elements.find((elementId) => loopIds.has(elementId)) || loopElements(design)[0]?.id || null;
}

export function toggleElement(data: DraftData, elementId: string): DraftData {
  const exists = data.elements.includes(elementId);
  return {
    ...data,
    elements: exists ? data.elements.filter((item) => item !== elementId) : [...data.elements, elementId],
  };
}

export function setLoopSide(design: Design, data: DraftData, loopId: string): DraftData {
  const loopIds = new Set(loopElements(design).map((element) => element.id));
  return {
    ...data,
    elements: [...data.elements.filter((elementId) => !loopIds.has(elementId)), loopId],
  };
}

export function requiresCarNumber(design: Design, data: DraftData): boolean {
  return !isCustomTextDesign(design);
}

export function buildOrderPayload(data: DraftData, draftId: string, user: TelegramUserMeta): OrderPayload {
  if (!data.designId || !data.sizeId || !data.customerName || !data.phone) {
    throw new Error("Order draft is incomplete");
  }
  return {
    customer_name: data.customerName,
    car_number: data.carNumber || "",
    phone: data.phone,
    design_id: data.designId,
    size_id: data.sizeId,
    elements: [],
    print_line_1: data.printLine1 || "",
    print_line_2: "",
    idempotency_key: `tg:${user.chatId}:${draftId}`,
    source: "telegram",
    telegram_chat_id: user.chatId,
    telegram_user_id: user.userId,
    telegram_username: user.username,
  };
}

export function orderSummary(design: Design, data: DraftData): string {
  const size = design.sizes.find((item) => item.id === data.sizeId);
  const printText = data.printLine1 || "";
  const price = size?.price ? `\nЦена: ${size.price} BYN` : "";
  const car = data.carNumber ? `\nАвто: ${data.carNumber}` : "";
  const custom = printText ? `\nПечать: ${printText}` : "";
  return [
    "Проверьте заказ:",
    `Дизайн: ${design.name}`,
    `Размер: ${size?.label || data.sizeId}`,
    `Имя: ${data.customerName || ""}`,
    `Телефон: ${data.phone || ""}${car}${custom}${price}`,
  ].join("\n");
}
