import assert from "node:assert/strict";
import test from "node:test";
import { buttonLabel, changeUnits, formatByn, pageSlice, shortId, units } from "./cart.js";

test("cart counts every piece toward the shared limit", () => {
  let cart = changeUnits([], "skull", 98, 99).cart;
  cart = changeUnits(cart, "cube", 1, 99).cart;
  assert.equal(units(cart), 99);
  const blocked = changeUnits(cart, "skull", 1, 99);
  assert.equal(blocked.error, "Можно заказать не больше 99 штук");
  assert.equal(units(blocked.cart), 99);
});

test("zero quantity removes the line", () => {
  const cart = changeUnits([{ productId: "skull", quantity: 1 }], "skull", -1, 99).cart;
  assert.deepEqual(cart, []);
});

test("money pages labels and ids stay stable", () => {
  assert.equal(formatByn(1500), "15 BYN");
  assert.equal(formatByn(1550), "15.50 BYN");
  const page = pageSlice([1, 2, 3], 5, 2);
  assert.equal(page.page, 1);
  assert.deepEqual(page.items, [3]);
  assert.equal(pageSlice([], 0, 8).pages, 1);
  assert.equal(buttonLabel("я".repeat(61)).length, 60);
  assert.equal(shortId("toys/skull"), shortId("toys/skull"));
  assert.equal(shortId("toys/skull").length, 10);
});

test("removing a line does not depend on the current quantity", () => {
  const cart = changeUnits([{ productId: "skull", quantity: 4 }], "skull", -10_000, 99).cart;
  assert.deepEqual(cart, []);
});
