const state = {
  designs: [],
  selectedDesignId: null,
  selectedElementsByDesign: {},
  submitting: false,
  orderKey: null,
};

const form = document.querySelector("#orderForm");
const designsNode = document.querySelector("#designs");
const sizeSelect = document.querySelector("#sizeSelect");
const elementsNode = document.querySelector("#elements");
const messageNode = document.querySelector("#message");
const submitOrderButton = document.querySelector("#submitOrderButton");
const priceNode = document.querySelector("#price");
const preview = document.querySelector("#preview");
const previewCar = document.querySelector("#previewCar");
const previewPhone = document.querySelector("#previewPhone");
const previewElements = document.querySelector("#previewElements");
const previewStack = document.querySelector("#previewStack");
const designName = document.querySelector("#designName");
const customerNameInput = form.elements.customer_name;
const carNumberInput = form.elements.car_number;
const phoneInput = form.elements.phone;

const NAME_VALID_RE = /^[A-Za-zА-Яа-яЁёІіЇїЄєЎўҐґ ]{2,30}$/;
const NAME_BLOCKED_RE = /[^A-Za-zА-Яа-яЁёІіЇїЄєЎўҐґ ]/g;
const RU_CAR_RE = /^[ABEKMHOPCTYXАВЕКМНОРСТУХ][0-9]{3}[ABEKMHOPCTYXАВЕКМНОРСТУХ]{2}[0-9]{2,3}$/;
const BY_CAR_RE = /^[0-9]{4}[ABCEHIKMOPTXАВСЕНІКМОРТХ]{2}[1-7]$/;
const CAR_BLOCKED_RE = /[^0-9A-Za-zА-Яа-яЁёІі]/g;
const PHONE_RE = /^(79[0-9]{9}|89[0-9]{9}|375(25|29|33|44)[0-9]{7})$/;
const CONTENT_IDS = ["name", "car", "phone"];
const LOOP_SIDE_IDS = ["loop_left", "loop_right"];

function telegramWebApp() {
  return window.Telegram?.WebApp || null;
}

function initTelegramWebApp() {
  const app = telegramWebApp();
  if (!app) return;
  document.body.classList.add("telegram-webapp");
  app.ready();
  app.expand();
}

function telegramInitData() {
  return telegramWebApp()?.initData || "";
}

function responseErrorMessage(result, fallback) {
  if (typeof result?.detail === "string") return result.detail;
  if (Array.isArray(result?.detail)) {
    return result.detail.map((item) => item.msg || item.message || String(item)).join("; ");
  }
  if (result?.detail?.message) return result.detail.message;
  return fallback;
}

function selectedDesign() {
  return state.designs.find((item) => item.id === state.selectedDesignId) || state.designs[0];
}

function defaultSize(design) {
  return design?.sizes.find((item) => item.id === design.default_size_id) || design?.sizes[0];
}

function selectedSize(design) {
  return design?.sizes.find((item) => item.id === sizeSelect.value) || defaultSize(design);
}

function isStackedDesign(design) {
  return design?.layout === "stacked_plate";
}

function defaultElementIds(design) {
  if (!design) return [];
  if (design.default_elements?.length) return [...design.default_elements];
  return design.elements?.filter((item) => item.default).map((item) => item.id) || [];
}

function contentIdsFrom(ids) {
  return ids.filter((id) => CONTENT_IDS.includes(id));
}

function loopSideFrom(ids) {
  return ids.find((id) => LOOP_SIDE_IDS.includes(id)) || "loop_left";
}

function modelScale(size) {
  return Number(size?.model_scale || 1);
}

function formatMm(value) {
  return Number(value).toFixed(1).replace(".0", "");
}

function selectedElementIds(design = selectedDesign()) {
  if (!design) return [];
  if (!state.selectedElementsByDesign[design.id]) {
    state.selectedElementsByDesign[design.id] = defaultElementIds(design);
  }
  return state.selectedElementsByDesign[design.id];
}

function checkedElementIds() {
  const content = [...document.querySelectorAll("input[name='elements']:checked")].map((item) => item.value);
  const loopSide = document.querySelector("input[name='loop_side']:checked")?.value;
  return loopSide ? [...content, loopSide] : content;
}

function saveCheckedElements() {
  const design = selectedDesign();
  if (!design) return [];
  const checked = checkedElementIds();
  state.selectedElementsByDesign[design.id] = checked;
  return checked;
}

function activeElementIds(design = selectedDesign()) {
  if (elementsNode.querySelector("input[name='elements']")) {
    return checkedElementIds();
  }
  return selectedElementIds(design);
}

function iconForElement(id) {
  if (id === "name") return "Aa";
  if (id === "car") return "№";
  if (id === "phone") return "tel";
  if (id === "loop_left") return "←";
  if (id === "loop_right") return "→";
  if (id === "heart") return "♥";
  if (id === "smile") return "☺";
  if (id === "star") return "★";
  return "◆";
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
  return String(value).replace(/[\s-]+/g, "").replace(CAR_BLOCKED_RE, "").toUpperCase().slice(0, 9);
}

function sanitizePhone(value) {
  return String(value).replace(/\D/g, "").slice(0, 12);
}

function sanitizeFields() {
  customerNameInput.value = sanitizeName(customerNameInput.value);
  carNumberInput.value = sanitizeCarNumber(carNumberInput.value);
  phoneInput.value = sanitizePhone(phoneInput.value);
}

function validateFields() {
  const design = selectedDesign();
  const printedElements = activeElementIds(design);
  const carIsPrinted = !isStackedDesign(design) || printedElements.includes("car");
  const name = customerNameInput.value.trim();
  const carNumber = carNumberInput.value.trim();
  const phone = phoneInput.value.trim();

  carNumberInput.required = carIsPrinted;
  customerNameInput.setCustomValidity(
    !name || NAME_VALID_RE.test(name) ? "" : "Имя: только буквы латиницы/кириллицы, до 30 символов",
  );
  carNumberInput.setCustomValidity(
    (!carIsPrinted && !carNumber) || !carNumber || RU_CAR_RE.test(carNumber) || BY_CAR_RE.test(carNumber)
      ? ""
      : "Номер авто: РФ А123ВС77/А123ВС777 или РБ 1234АВ7",
  );
  phoneInput.setCustomValidity(!phone || PHONE_RE.test(phone) ? "" : "Телефон: только цифры, мобильный РБ или РФ");
}

function renderDesigns() {
  designsNode.innerHTML = "";
  state.designs.forEach((design) => {
    const node = document.createElement("button");
    node.type = "button";
    node.className = `design-option ${design.id === state.selectedDesignId ? "active" : ""}`;
    node.innerHTML = `
      <div class="design-title">
        <span>${design.name}</span>
        <span class="swatch" style="background: ${design.accent}"></span>
      </div>
      <div class="muted small">${design.description || ""}</div>
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
    const rowHeight = size.row_height_mm || size.height_mm;
    const scale = modelScale(size);
    const suffix = scale !== 1 ? ` · ${(scale * 100).toFixed(1)}%` : "";
    option.textContent = `${size.label} · ${formatMm(size.width_mm * scale)}×${formatMm(rowHeight * scale)} мм${suffix}`;
    sizeSelect.appendChild(option);
  });
  const preferredSizeId = design.default_size_id || design.sizes[0]?.id || "";
  if (preferredSizeId) sizeSelect.value = preferredSizeId;

  elementsNode.innerHTML = "";
  elementsNode.classList.toggle("content-choices", isStackedDesign(design));
  const checked = new Set(selectedElementIds(design));
  const contentElements = design.elements.filter((element) => element.kind !== "loop_side");
  const loopElements = design.elements.filter((element) => element.kind === "loop_side");

  const contentGroup = document.createElement("div");
  contentGroup.className = "choice-group";
  contentElements.forEach((element) => {
    const label = document.createElement("label");
    label.className = "choice";
    const isChecked = checked.has(element.id) ? "checked" : "";
    label.innerHTML = `<input type="checkbox" name="elements" value="${element.id}" ${isChecked} /> <span class="choice-icon">${iconForElement(element.id)}</span> <span>${element.label}</span>`;
    contentGroup.appendChild(label);
  });
  elementsNode.appendChild(contentGroup);

  if (loopElements.length) {
    const loopSide = loopSideFrom([...checked]);
    const loopGroup = document.createElement("div");
    loopGroup.className = "choice-group loop-choice-group";
    const title = document.createElement("div");
    title.className = "small muted choice-group-title";
    title.textContent = "Ушко";
    loopGroup.appendChild(title);
    loopElements.forEach((element) => {
      const label = document.createElement("label");
      label.className = "choice";
      const isChecked = loopSide === element.id ? "checked" : "";
      label.innerHTML = `<input type="radio" name="loop_side" value="${element.id}" ${isChecked} /> <span class="choice-icon">${iconForElement(element.id)}</span> <span>${element.label}</span>`;
      loopGroup.appendChild(label);
    });
    elementsNode.appendChild(loopGroup);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function displayValueFor(id, name, car, phone) {
  if (id === "name") return name || "Никита";
  if (id === "phone") {
    const value = phone || "37525555489";
    return value.startsWith("+") ? value : `+${value}`;
  }
  return car || "0114PO8";
}

function badgeFor(id) {
  if (id === "name") return "ID";
  if (id === "phone") return "TEL";
  return "BY";
}

function centerContentId(ids) {
  if (ids.length === 1) return ids[0];
  if (ids.includes("car")) return "car";
  if (ids.includes("name")) return "name";
  return ids[0] || "car";
}

function stackedSections(checked, name, car, phone) {
  const ids = contentIdsFrom(checked);
  if (ids.length >= 3) {
    return [
      { id: "name", slot: "top" },
      { id: "car", slot: "center" },
      { id: "phone", slot: "bottom" },
    ].map((section) => ({
      ...section,
      badge: badgeFor(section.id),
      value: displayValueFor(section.id, name, car, phone),
    }));
  }

  const centerId = centerContentId(ids);
  const sections = [{ id: centerId, slot: "center" }];
  if (ids.length === 2) {
    const bottomId = ids.find((id) => id !== centerId) || ids[1];
    sections.push({ id: bottomId, slot: "bottom" });
  }

  return sections.map((section) => ({
    ...section,
    badge: badgeFor(section.id),
    value: displayValueFor(section.id, name, car, phone),
  }));
}

function stackedHeight(size, sectionCount, design) {
  const count = Math.max(1, sectionCount);
  const rowHeight = Number(size.row_height_mm || size.height_mm || 0);
  const topRatio = Number(design.top_block_ratio || 0.56);
  const bottomRatio = Number(design.bottom_block_ratio || 0.56);
  const overlap = Number(size.section_overlap_mm || design.section_overlap_mm || 1.4);
  const scale = modelScale(size);
  if (count === 1) return rowHeight * scale;
  if (count === 2) return (rowHeight + rowHeight * bottomRatio - overlap) * scale;
  return (rowHeight + rowHeight * topRatio + rowHeight * bottomRatio - overlap * 2) * scale;
}

function steppedMetrics(slot, sectionCount) {
  const count = Math.max(1, sectionCount);
  const sideRatio = 0.56;
  const overlap = count === 1 ? 0 : 0.08;
  const centerHeight = 1;
  const sideHeight = sideRatio;
  const totalHeight = count === 1 ? centerHeight : count === 2 ? centerHeight + sideHeight - overlap : centerHeight + sideHeight * 2 - overlap * 2;
  const centerTop = count === 3 ? sideHeight - overlap : 0;
  const top = slot === "top" ? 0 : slot === "bottom" ? centerTop + centerHeight - overlap : centerTop;
  const height = slot === "center" ? centerHeight : sideHeight;
  const width = slot === "center" ? 100 : 78;
  const left = slot === "center" ? 0 : (100 - width) / 2;
  return {
    top: `${(top / totalHeight) * 100}%`,
    height: `${(height / totalHeight) * 100}%`,
    left: `${left}%`,
    width: `${width}%`,
    totalUnits: totalHeight,
  };
}

function steppedLoopY(sectionCount) {
  const count = Math.max(1, sectionCount);
  const sideHeight = 0.56;
  const overlap = count === 1 ? 0 : 0.08;
  const totalHeight = count === 1 ? 1 : count === 2 ? 1 + sideHeight - overlap : 1 + sideHeight * 2 - overlap * 2;
  const centerTop = count === 3 ? sideHeight - overlap : 0;
  return `${((centerTop + 0.5) / totalHeight) * 100}%`;
}

function sectionFontSize(section) {
  const length = section.value.length;
  if (section.slot === "center" && section.id === "car") return Math.max(30, Math.min(58, 68 - length * 1.25));
  if (section.slot === "center") return Math.max(26, Math.min(48, 58 - length * 1.25));
  if (section.id === "phone") return Math.max(22, Math.min(42, 54 - length * 1.35));
  return Math.max(20, Math.min(38, 50 - length * 1.15));
}

function updatePreview() {
  const design = selectedDesign();
  const size = selectedSize(design);
  if (!design || !size) return;

  const data = new FormData(form);
  const name = String(data.get("customer_name") || "Анна").trim();
  const car = String(data.get("car_number") || "A123BC77").trim().toUpperCase();
  const phone = String(data.get("phone") || "375291234567").trim();
  const checked = checkedElementIds();

  if (isStackedDesign(design)) {
    const active = contentIdsFrom(checked).length ? checked : defaultElementIds(design);
    const sections = stackedSections(active, name, car, phone);
    const loopSide = loopSideFrom(active);
    const firstMetrics = steppedMetrics("center", sections.length);
    preview.className = `keychain-preview stepped ${design.preview_style || "classic"} ${loopSide === "loop_right" ? "loop-right" : "loop-left"}`;
    preview.style.removeProperty("background");
    preview.style.setProperty("--preview-accent", design.accent || "#ffffff");
    preview.style.setProperty("--loop-y", steppedLoopY(sections.length));
    preview.style.setProperty("--model-preview-scale", modelScale(size));
    previewStack.hidden = false;
    previewCar.hidden = true;
    previewPhone.hidden = true;
    previewElements.hidden = true;
    previewStack.className = `preview-stack stepped-stack count-${sections.length}`;
    previewStack.style.aspectRatio = `4.95 / ${firstMetrics.totalUnits}`;
    previewStack.innerHTML = sections
      .map((section) => {
        const metrics = steppedMetrics(section.slot, sections.length);
        return `
          <div class="preview-section preview-section-${section.id} preview-slot-${section.slot}" style="--section-top: ${metrics.top}; --section-height: ${metrics.height}; --section-left: ${metrics.left}; --section-width: ${metrics.width};">
            <span class="preview-badge">${section.badge}</span>
            <span class="preview-section-text" style="font-size: ${sectionFontSize(section)}px">${escapeHtml(section.value)}</span>
          </div>
        `;
      })
      .join("");
    const height = stackedHeight(size, sections.length, design);
    designName.textContent = `${design.name} · ${size.label} · ${formatMm(size.width_mm * modelScale(size))}×${formatMm(height)} мм`;
    priceNode.textContent = `${size.price || 0} BYN`;
    return;
  }

  previewCar.textContent = car || "A123BC77";
  previewPhone.textContent = phone || "375291234567";
  previewElements.textContent = checked.map(iconForElement).join(" ");
  preview.style.background = design.accent || "#f4c542";
  preview.style.removeProperty("--model-preview-scale");
  preview.className = "keychain-preview";
  preview.classList.toggle("oval", design.id.includes("rounded"));
  previewStack.hidden = true;
  previewCar.hidden = false;
  previewPhone.hidden = false;
  previewElements.hidden = false;
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
  state.selectedDesignId = state.designs.find((item) => isStackedDesign(item))?.id || state.designs[0]?.id || null;
  renderAll();
}

form.addEventListener("input", (event) => {
  if (event.target === customerNameInput || event.target === carNumberInput || event.target === phoneInput) {
    sanitizeFields();
    validateFields();
  }
  updatePreview();
});
sizeSelect.addEventListener("change", updatePreview);
elementsNode.addEventListener("change", (event) => {
  const design = selectedDesign();
  let checked = saveCheckedElements();
  if (isStackedDesign(design) && contentIdsFrom(checked).length === 0 && event.target?.name === "elements") {
    event.target.checked = true;
    checked = saveCheckedElements();
  }
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
    elements: checkedElementIds(),
    idempotency_key: currentOrderKey(),
  };
  const initData = telegramInitData();
  if (initData) {
    payload.telegram_init_data = initData;
  }

  try {
    const response = await fetch("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(responseErrorMessage(result, "Заказ не создан"));
    }
    resetOrderKey();
    window.location.href = `/status/${result.order.id}`;
  } catch (error) {
    messageNode.textContent = error.message;
    messageNode.classList.add("error");
    setSubmitting(false);
  }
});

initTelegramWebApp();
window.addEventListener("load", initTelegramWebApp);

loadDesigns().catch((error) => {
  messageNode.textContent = error.message;
  messageNode.classList.add("error");
});
