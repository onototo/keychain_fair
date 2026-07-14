import assert from "node:assert/strict";
import test from "node:test";
import {
  buildOrderPayload,
  cleanCustomText,
  customTextLimit,
  defaultElementIds,
  defaultSizeId,
  isCustomTextDesign,
  requiresCarNumber,
} from "./flow.js";
import type { Design, DraftData } from "./types.js";

const numberDesign: Design = {
  id: "stacked_plate_classic",
  name: "Number",
  layout: "stacked_plate",
  print_mode: "by_number_single",
  default_size_id: "standard",
  default_elements: [],
  preview_image: "/static/design-previews/stacked_plate_classic.png",
  sizes: [{ id: "standard", label: "Standard", price: 17 }],
  elements: [],
};

const customDesign: Design = {
  id: "classic_plate",
  name: "Custom",
  print_mode: "custom_text",
  default_size_id: "standard",
  sizes: [
    {
      id: "standard",
      label: "Standard",
      price: 15,
      custom_text_limits: { max_total_chars: 26, max_line_1_chars: 9, max_line_2_chars: 17 },
    },
  ],
  elements: [],
};

test("buildOrderPayload includes telegram metadata and custom text", () => {
  const data: DraftData = {
    designId: "classic_plate",
    sizeId: "standard",
    elements: ["legacy_ignored"],
    customerName: "Anna",
    phone: "375291234567",
    printLine1: "Hello",
  };

  const payload = buildOrderPayload(data, "draft-1", {
    chatId: "42",
    userId: "7",
    username: "anna",
  });

  assert.equal(payload.idempotency_key, "tg:42:draft-1");
  assert.equal(payload.source, "telegram");
  assert.equal(payload.telegram_chat_id, "42");
  assert.deepEqual(payload.elements, []);
  assert.equal(payload.print_line_1, "Hello");
  assert.equal(payload.print_line_2, "");
});

test("number designs require car number and custom text designs do not", () => {
  assert.equal(requiresCarNumber(numberDesign, { elements: [] }), true);
  assert.equal(requiresCarNumber(customDesign, { elements: [] }), false);
  assert.equal(isCustomTextDesign(customDesign), true);
});

test("default helpers keep designs element-free", () => {
  assert.deepEqual(defaultElementIds(numberDesign), []);
  assert.equal(defaultSizeId(numberDesign), "standard");
});

test("custom text helpers use catalog limits and collapse whitespace", () => {
  assert.equal(customTextLimit(customDesign, "standard"), 26);
  assert.equal(cleanCustomText("  Hello    World  "), "Hello World");
});
