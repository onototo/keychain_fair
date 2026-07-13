import assert from "node:assert/strict";
import test from "node:test";
import {
  buildOrderPayload,
  contentElementIds,
  defaultSizeId,
  requiresCarNumber,
  setLoopSide,
  toggleElement,
} from "./flow.js";
import type { Design, DraftData } from "./types.js";

const stackedDesign: Design = {
  id: "stacked_plate_classic",
  name: "Stacked",
  layout: "stacked_plate",
  default_size_id: "standard",
  default_elements: ["car", "loop_left"],
  preview_image: "/static/design-previews/stacked_plate_classic.png",
  sizes: [{ id: "standard", label: "Standard", price: 17 }],
  elements: [
    { id: "name", label: "Name", kind: "content" },
    { id: "car", label: "Car", kind: "content" },
    { id: "phone", label: "Phone", kind: "content" },
    { id: "loop_left", label: "Left", kind: "loop_side" },
    { id: "loop_right", label: "Right", kind: "loop_side" },
  ],
};

test("buildOrderPayload includes telegram metadata and idempotency key", () => {
  const data: DraftData = {
    designId: "stacked_plate_classic",
    sizeId: "standard",
    elements: ["car", "loop_left"],
    customerName: "Anna",
    phone: "375291234567",
    carNumber: "A123BC77",
  };

  const payload = buildOrderPayload(data, "draft-1", {
    chatId: "42",
    userId: "7",
    username: "anna",
  });

  assert.equal(payload.idempotency_key, "tg:42:draft-1");
  assert.equal(payload.source, "telegram");
  assert.equal(payload.telegram_chat_id, "42");
  assert.equal(payload.telegram_user_id, "7");
  assert.equal(payload.telegram_username, "anna");
});

test("stacked design requires car number only when car block is selected", () => {
  assert.equal(requiresCarNumber(stackedDesign, { elements: ["name", "loop_left"] }), false);
  assert.equal(requiresCarNumber(stackedDesign, { elements: ["car", "loop_left"] }), true);
});

test("element helpers keep loop side mutually exclusive", () => {
  const selected = setLoopSide(stackedDesign, { elements: ["car", "loop_left"] }, "loop_right");

  assert.deepEqual(selected.elements, ["car", "loop_right"]);
  assert.deepEqual(contentElementIds(stackedDesign, selected), ["car"]);
});

test("toggleElement adds and removes content choices", () => {
  const added = toggleElement({ elements: ["car"] }, "phone");
  const removed = toggleElement(added, "car");

  assert.deepEqual(added.elements, ["car", "phone"]);
  assert.deepEqual(removed.elements, ["phone"]);
  assert.equal(defaultSizeId(stackedDesign), "standard");
});
