import assert from "node:assert/strict";
import test from "node:test";
import { changeUnits, formatByn, pageSlice, units } from "./cart.js";

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

test("money and pages stay simple", () => {
  assert.equal(formatByn(1500), "15 BYN");
  assert.equal(formatByn(1550), "15.50 BYN");
  const page = pageSlice([1, 2, 3], 5, 2);
  assert.equal(page.page, 1);
  assert.deepEqual(page.items, [3]);
});
