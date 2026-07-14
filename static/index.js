const state = {
  designs: [],
  selectedDesignId: null,
  submitting: false,
  orderKey: null,
};

const form = document.querySelector("#orderForm");
const designsNode = document.querySelector("#designs");
const sizeSelect = document.querySelector("#sizeSelect");
const messageNode = document.querySelector("#message");
const submitOrderButton = document.querySelector("#submitOrderButton");
const priceNode = document.querySelector("#price");
const previewImage = document.querySelector("#previewImage");
const designName = document.querySelector("#designName");
const customerNameInput = form.elements.customer_name;
const carNumberInput = form.elements.car_number;
const phoneInput = form.elements.phone;
const printLine1Input = form.elements.print_line_1;
const printLine2Input = form.elements.print_line_2;
const carNumberField = document.querySelector("#carNumberField");
const printLine1Field = document.querySelector("#printLine1Field");
const printLine2Field = document.querySelector("#printLine2Field");
const customTextHelp = document.querySelector("#customTextHelp");

const FALLBACK_PREVIEW = "/static/design-previews/fallback.png";
const NAME_VALID_RE = /^[A-Za-zА-Яа-яЁёІіЇїЄєЎўҐґ ]{2,30}$/;
const NAME_BLOCKED_RE = /[^A-Za-zА-Яа-яЁёІіЇїЄєЎўҐґ ]/g;
const BY_CAR_RE = /^[0-9]{4}[ABCEHIKMOPTX]{2}[1-7]$/;
const BY_CAR_DISPLAY_RE = /^[0-9]{4}\s[ABCEHIKMOPTX]{2}-[1-7]$/;
const CAR_BLOCKED_RE = /[^0-9A-Za-z]/g;
const PHONE_RE = /^\+?(79[0-9]{9}|89[0-9]{9}|375(25|29|33|44)[0-9]{7})$/;

function selectedDesign() {
  return state.designs.find((item) => item.id === state.selectedDesignId) || state.designs[0];
}

function defaultSize(design) {
  return design?.sizes.find((item) => item.id === design.default_size_id) || design?.sizes[0];
}

function selectedSize(design) {
  return design?.sizes.find((item) => item.id === sizeSelect.value) || defaultSize(design);
}

function isCustomDesign(design) {
  return design?.print_mode === "custom_text";
}

function modelScale(size) {
  return Number(size?.model_scale || 1);
}

function formatMm(value) {
  return Number(value).toFixed(1).replace(".0", "");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function newOrderKey() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID().replaceAll("-", "");
  }
  return `order_${Date.now()}_${Math.random().toString(36).slice(2)}`;
}

function currentOrderKey() {
  const storageKey = "keychain_fair_order_key";
  let value = window.sessionStorage.getItem(storageKey);
  if (!value) {
    value = newOrderKey();
    window.sessionStorage.setItem(storageKey, value);
  }
  state.orderKey = value;
  return value;
}

function resetOrderKey() {
  window.sessionStorage.removeItem("keychain_fair_order_key");
  state.orderKey = null;
}

function currentClientId() {
  const storageKey = "keychain_fair_client_id";
  let value = window.localStorage.getItem(storageKey);
  if (!value) {
    value = newOrderKey();
    window.localStorage.setItem(storageKey, value);
  }
  return value;
}

function customTextLimits(design = selectedDesign(), size = selectedSize(design)) {
  return size?.custom_text_limits || { max_total_chars: 48, max_line_1_chars: 24, max_line_2_chars: 24 };
}

function setSubmitting(isSubmitting) {
  state.submitting = isSubmitting;
  if (!submitOrderButton) return;
  submitOrderButton.disabled = isSubmitting;
  submitOrderButton.textContent = isSubmitting ? "Создаём заказ..." : "Подтвердить заказ";
  form.setAttribute("aria-busy", isSubmitting ? "true" : "false");
}

function sanitizeName(value) {
  return String(value).replace(NAME_BLOCKED_RE, "").replace(/\s+/g, " ").slice(0, 30);
}

function sanitizeCarNumber(value) {
  const compact = String(value).replace(/[\s-]+/g, "").replace(CAR_BLOCKED_RE, "").toUpperCase().slice(0, 7);
  if (/^[0-9]{4}/.test(compact) && compact.length > 4) {
    const digits = compact.slice(0, 4);
    const letters = compact.slice(4, 6);
    const region = compact.slice(6, 7);
    return `${digits}${letters ? ` ${letters}` : ""}${region ? `-${region}` : ""}`;
  }
  return compact;
}

function sanitizePrintLine(value) {
  return String(value)
    .replace(/\s+/g, " ")
    .slice(0, customTextLimits().max_total_chars || 48);
}

function sanitizePhone(value) {
  const cleaned = String(value).replace(/[^\d+]/g, "").replace(/(?!^)\+/g, "");
  const normalized = cleaned.startsWith("+") ? `+${cleaned.slice(1).replace(/\D/g, "")}` : cleaned.replace(/\D/g, "");
  return normalized.slice(0, 13);
}

function sanitizeFields() {
  customerNameInput.value = sanitizeName(customerNameInput.value);
  carNumberInput.value = sanitizeCarNumber(carNumberInput.value);
  phoneInput.value = sanitizePhone(phoneInput.value);
  printLine1Input.value = sanitizePrintLine(printLine1Input.value);
  printLine2Input.value = sanitizePrintLine(printLine2Input.value);
}

function validateFields() {
  const design = selectedDesign();
  const custom = isCustomDesign(design);
  const name = customerNameInput.value.trim();
  const carNumber = carNumberInput.value.trim();
  const phone = phoneInput.value.trim();
  const printLine1 = printLine1Input.value.trim();
  const printLine2 = printLine2Input.value.trim();
  const limits = customTextLimits(design, selectedSize(design));
  const maxCustomLength = Number(limits.max_total_chars || 48);
  const customLength = printLine1.length + printLine2.length;

  carNumberInput.required = !custom;
  carNumberInput.disabled = custom;
  printLine1Input.disabled = !custom;
  printLine2Input.disabled = !custom;
  printLine1Input.maxLength = maxCustomLength;
  printLine2Input.maxLength = maxCustomLength;

  carNumberField.hidden = custom;
  printLine1Field.hidden = !custom;
  printLine2Field.hidden = !custom;
  if (customTextHelp) {
    customTextHelp.textContent = custom ? `Максимальная длина текста: ${maxCustomLength} символов.` : "";
  }

  customerNameInput.setCustomValidity(
    !name || NAME_VALID_RE.test(name) ? "" : "Имя: только буквы латиницы/кириллицы, до 30 символов",
  );
  carNumberInput.setCustomValidity(
    custom || BY_CAR_DISPLAY_RE.test(carNumber) || BY_CAR_RE.test(carNumber.replace(/[\s-]+/g, ""))
      ? ""
      : "Номер авто: РБ 1234 AB-7",
  );
  phoneInput.setCustomValidity(!phone || PHONE_RE.test(phone) ? "" : "Телефон: цифры и необязательный +, мобильный РБ или РФ");
  printLine1Input.setCustomValidity(
    custom && !printLine1 && !printLine2
      ? "Введите текст для печати"
      : custom && customLength > maxCustomLength
        ? `Максимальная длина текста: ${maxCustomLength} символов`
        : "",
  );
  printLine2Input.setCustomValidity(custom && customLength > maxCustomLength ? `Максимальная длина текста: ${maxCustomLength} символов` : "");
}

function renderDesigns() {
  designsNode.innerHTML = "";
  state.designs.forEach((design) => {
    const node = document.createElement("button");
    node.type = "button";
    node.className = `design-option ${design.id === state.selectedDesignId ? "active" : ""}`;
    node.innerHTML = `
      <img class="design-thumb" src="${escapeHtml(design.preview_image || FALLBACK_PREVIEW)}" alt="" loading="lazy" />
      <div class="design-copy">
        <div class="design-title">${escapeHtml(design.name)}</div>
        <div class="muted small">${escapeHtml(design.description || "")}</div>
      </div>
    `;
    node.addEventListener("click", () => {
      state.selectedDesignId = design.id;
      renderAll();
    });
    designsNode.appendChild(node);
  });
}

function renderOptions() {
  const design = selectedDesign();
  if (!design) return;

  sizeSelect.innerHTML = "";
  design.sizes.forEach((size) => {
    const option = document.createElement("option");
    option.value = size.id;
    const scale = modelScale(size);
    const suffix = scale !== 1 ? ` · ${(scale * 100).toFixed(1)}%` : "";
    option.textContent = `${size.label} · ${formatMm(size.width_mm * scale)}×${formatMm(size.height_mm * scale)} мм${suffix}`;
    sizeSelect.appendChild(option);
  });
  const preferredSizeId = design.default_size_id || design.sizes[0]?.id || "";
  if (preferredSizeId) sizeSelect.value = preferredSizeId;
}

function updatePreview() {
  const design = selectedDesign();
  const size = selectedSize(design);
  if (!design || !size) return;

  previewImage.src = design.preview_image || FALLBACK_PREVIEW;
  previewImage.alt = design.name || "";
  designName.textContent = `${design.name} · ${size.label}`;
  priceNode.textContent = `${size.price || 0} BYN`;
}

function renderAll() {
  renderDesigns();
  renderOptions();
  validateFields();
  updatePreview();
}

async function loadDesigns() {
  const response = await fetch("/api/designs");
  const payload = await response.json();
  state.designs = payload.designs || [];
  state.selectedDesignId = state.designs[0]?.id || null;
  renderAll();
}

form.addEventListener("input", (event) => {
  if (
    event.target === customerNameInput ||
    event.target === carNumberInput ||
    event.target === phoneInput ||
    event.target === printLine1Input ||
    event.target === printLine2Input
  ) {
    sanitizeFields();
    validateFields();
  }
  updatePreview();
});

sizeSelect.addEventListener("change", () => {
  sanitizeFields();
  validateFields();
  updatePreview();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.submitting) return;

  messageNode.textContent = "";
  messageNode.classList.remove("error");
  sanitizeFields();
  validateFields();
  if (!form.reportValidity()) {
    return;
  }
  setSubmitting(true);

  const data = new FormData(form);
  const payload = {
    customer_name: String(data.get("customer_name") || ""),
    car_number: String(data.get("car_number") || ""),
    phone: String(data.get("phone") || ""),
    design_id: state.selectedDesignId,
    size_id: String(data.get("size_id") || ""),
    elements: [],
    print_line_1: String(data.get("print_line_1") || ""),
    print_line_2: String(data.get("print_line_2") || ""),
    idempotency_key: currentOrderKey(),
    client_id: currentClientId(),
  };

  try {
    const response = await fetch("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      const detail = result.detail;
      const message = typeof detail === "string" ? detail : detail?.message;
      throw new Error(message || "Заказ не создан");
    }
    resetOrderKey();
    window.location.href = `/status/${result.order.id}`;
  } catch (error) {
    messageNode.textContent = error.message;
    messageNode.classList.add("error");
    setSubmitting(false);
  }
});

loadDesigns().catch((error) => {
  messageNode.textContent = error.message;
  messageNode.classList.add("error");
});
