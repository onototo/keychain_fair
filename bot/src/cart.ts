import { createHash } from "node:crypto";
import type { CartLine } from "./types.js";

export const CART_PAGE_SIZE = 8;

export function shortId(value: string): string {
  return createHash("sha1").update(value).digest("hex").slice(0, 10);
}

export function units(cart: CartLine[]): number {
  return cart.reduce((sum, line) => sum + line.quantity, 0);
}

export function quantityOf(cart: CartLine[], productId: string): number {
  return cart.find((line) => line.productId === productId)?.quantity ?? 0;
}

export function formatByn(kopecks: number): string {
  const rubles = Math.trunc(kopecks / 100);
  const cents = Math.abs(kopecks % 100);
  if (cents === 0) return `${rubles} BYN`;
  return `${rubles}.${String(cents).padStart(2, "0")} BYN`;
}

export function changeUnits(
  cart: CartLine[],
  productId: string,
  delta: number,
  maxUnits: number,
): { cart: CartLine[]; error?: string } {
  const nextQuantity = quantityOf(cart, productId) + delta;
  if (delta > 0 && units(cart) + delta > maxUnits) {
    return { cart, error: `Можно заказать не больше ${maxUnits} штук` };
  }
  if (nextQuantity <= 0) {
    return { cart: cart.filter((line) => line.productId !== productId) };
  }
  const next = cart.map((line) => ({ ...line }));
  const existing = next.find((line) => line.productId === productId);
  if (existing) existing.quantity = nextQuantity;
  else next.push({ productId, quantity: nextQuantity });
  return { cart: next };
}

export function pageSlice<T>(items: T[], page: number, pageSize: number): { page: number; pages: number; items: T[] } {
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(Math.max(page, 0), pages - 1);
  return {
    page: safePage,
    pages,
    items: items.slice(safePage * pageSize, safePage * pageSize + pageSize),
  };
}

export function buttonLabel(text: string): string {
  return text.length > 60 ? `${text.slice(0, 57)}...` : text;
}
