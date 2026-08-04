const STATUS_OPTIONS = [
  ["unpaid", "не оплачен"],
  ["paid", "оплачен"],
  ["printed", "напечатан"],
  ["ready_for_pickup", "готов к выдаче"],
  ["error", "ошибка"],
];

const login = document.querySelector("#login");
const admin = document.querySelector("#admin");
const loginForm = document.querySelector("#loginForm");
const pinInput = document.querySelector("#pinInput");
const loginButton = loginForm.querySelector("button[type='submit']");
const loginMessage = document.querySelector("#loginMessage");
const ordersBody = document.querySelector("#ordersBody");
const ordersCount = document.querySelector("#ordersCount");
const adminMessage = document.querySelector("#adminMessage");
const prepareQueue = document.querySelector("#prepareQueue");
const downloadStats = document.querySelector("#downloadStats");
const printBlanks = document.querySelector("#printBlanks");
const blankTargetCount = document.querySelector("#blankTargetCount");
const printBatch = document.querySelector("#printBatch");
const heatBed = document.querySelector("#heatBed");
const coolBed = document.querySelector("#coolBed");
const resumePrint = document.querySelector("#resumePrint");
const stopPrint = document.querySelector("#stopPrint");
const logout = document.querySelector("#logout");
const toolStatus = document.querySelector("#toolStatus");
const printerWidget = document.querySelector("#printerWidget");
const printerOnline = document.querySelector("#printerOnline");
const printerPhase = document.querySelector("#printerPhase");
const printerTime = document.querySelector("#printerTime");
const filamentNotice = document.querySelector("#filamentNotice");
const batchStatus = document.querySelector("#batchStatus");
const bedMeta = document.querySelector("#bedMeta");
const bedPreview = document.querySelector("#bedPreview");
const batchItems = document.querySelector("#batchItems");
const adminQrSpoilerWrap = document.querySelector("#adminQrSpoilerWrap");
const adminQrSpoiler = document.querySelector("#adminQrSpoiler");

const PRINTER_WIDGET_CLASSES = [
  "printer-online",
  "printer-offline",
  "printer-waiting",
  "printer-heating",
  "printer-printing",
  "printer-done-printing",
];

const BLANK_SLOT_STATES = [
  [1, "Пустое место"],
  [2, "Заготовка будет напечатана"],
  [3, "Заготовка печатается"],
  [4, "Заготовка напечатана"],
  [5, "Текст будет напечатан"],
  [6, "Текст печатается"],
  [7, "Текст напечатан, освободить"],
];

let loginLockTimer = null;
let activeBatch = null;
let bedSizeMm = [220, 220];
let toolState = null;
let printerState = null;
let hasPrintedBlankBatch = false;
let blankPreview = null;

class ApiError extends Error {
  constructor(message, status, retryAfterSeconds = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function pin() {
  return localStorage.getItem("adminPin") || "";
}

function headers() {
  return { "Content-Type": "application/json", "X-Admin-Pin": pin() };
}

function formatDate(value) {
  if (!value) return "";
  return new Date(value).toLocaleTimeString();
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value ?? "";
  return node.innerHTML;
}

function sourceLabel(order) {
  return order.source === "telegram" ? "Telegram" : "Web";
}

function orderNumberLabel(order) {
  return order?.order_number ? `#${order.order_number}` : "#---";
}

function orderTextLabel(order) {
  return [order?.print_line_1, order?.print_line_2].filter(Boolean).join(" ")
    || order?.car_number
    || "";
}

function formatDuration(value) {
  if (value === null || value === undefined) return "--";
  const total = Math.max(0, Math.floor(Number(value)));
  if (!Number.isFinite(total)) return "--";

  const seconds = String(total % 60).padStart(2, "0");
  const minutesTotal = Math.floor(total / 60);
  const minutes = String(minutesTotal % 60).padStart(2, "0");
  const hours = Math.floor(minutesTotal / 60);
  return hours > 0 ? `${hours}:${minutes}:${seconds}` : `${minutesTotal}:${seconds}`;
}

function formatMm(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return number.toFixed(1).replace(".0", "");
}

function batchHasDeletedOrders(batch) {
  return (batch?.items || []).some((item) => item.archived_at);
}

function batchHasBlockedOrders(batch) {
  return (batch?.items || []).some((item) => item.archived_at || item.status !== "queued");
}

function isBatchReadyForPrint(batch) {
  return Boolean(batch && batch.status === "queued" && batch.plate_stl_path && !batchHasBlockedOrders(batch));
}

function isPrinterReadyForPrint() {
  return Boolean(printerState?.online && !printerState?.busy);
}

function canCreateGcode(batch) {
  return Boolean(batch?.gcode_path || toolState?.slicer?.enabled);
}

function canStartPrint(batch) {
  const queuedOverlayReady = batch?.kind === "overlay" && isBatchReadyForPrint(batch) && canCreateGcode(batch);
  const canPrepareFromBlanks = Boolean((blankPreview?.can_accept_orders ?? hasPrintedBlankBatch) && toolState?.slicer?.enabled);
  return Boolean(isPrinterReadyForPrint() && (queuedOverlayReady || canPrepareFromBlanks));
}

function showAdmin() {
  login.hidden = true;
  admin.hidden = false;
}

function showLogin() {
  admin.hidden = true;
  login.hidden = false;
}

function setLoginEnabled(isEnabled) {
  pinInput.disabled = !isEnabled;
  loginButton.disabled = !isEnabled;
}

function startLoginLock(seconds) {
  let remaining = Math.max(1, Math.floor(Number(seconds) || 60));
  clearInterval(loginLockTimer);
  setLoginEnabled(false);

  const render = () => {
    loginMessage.textContent = `Слишком много попыток. Повторите через ${formatDuration(remaining)}`;
    loginMessage.classList.add("error");
  };
  render();

  loginLockTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(loginLockTimer);
      loginLockTimer = null;
      setLoginEnabled(true);
      loginMessage.textContent = "";
      loginMessage.classList.remove("error");
      return;
    }
    render();
  }, 1000);
}

function authErrorMessage(error) {
  if (error.status === 401) return "Неверный PIN";
  return error.message;
}

function setAdminQrSpoilerHidden(isHidden) {
  if (adminQrSpoilerWrap) adminQrSpoilerWrap.dataset.hidden = String(isHidden);
  if (!adminQrSpoiler) return;
  adminQrSpoiler.setAttribute("aria-pressed", String(!isHidden));
  adminQrSpoiler.setAttribute("aria-label", isHidden ? "Показать QR админ панели" : "Скрыть QR админ панели");
  const label = adminQrSpoiler.querySelector("span:last-child");
  if (label) label.textContent = isHidden ? "Показать" : "Скрыть";
}

function isAdminQrSpoilerHidden() {
  return adminQrSpoilerWrap?.dataset.hidden !== "false";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...headers(), ...(options.headers || {}) },
  });
  const contentType = response.headers.get("Content-Type") || "";
  const rawText = await response.text();
  let result = {};
  if (rawText && contentType.includes("application/json")) {
    try {
      result = JSON.parse(rawText);
    } catch (_error) {
      result = {};
    }
  }
  if (!response.ok) {
    const detail = result.detail;
    const fallback = rawText.trim() || `HTTP ${response.status}`;
    const message = typeof detail === "string" ? detail : detail?.message || fallback;
    throw new ApiError(message, response.status, detail?.retry_after_seconds || null);
  }
  return result;
}

async function downloadStatistics() {
  const response = await fetch("/api/admin/statistics.xlsx", {
    headers: { "X-Admin-Pin": pin() },
  });

  if (!response.ok) {
    let detail = null;
    try {
      detail = (await response.json()).detail;
    } catch (_error) {
      detail = null;
    }
    const message = typeof detail === "string" ? detail : detail?.message || "Ошибка запроса";
    throw new ApiError(message, response.status, detail?.retry_after_seconds || null);
  }

  const disposition = response.headers.get("Content-Disposition") || "";
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || "keychain-fair-statistics.xlsx";
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function updateBlankSlotState(blankBatchId, slotIndex, stateCode) {
  return api(`/api/admin/blanks/${encodeURIComponent(blankBatchId)}/slots/${encodeURIComponent(slotIndex)}`, {
    method: "PATCH",
    body: JSON.stringify({ state_code: Number(stateCode) }),
  });
}

function isStatusSelectActive() {
  const activeElement = document.activeElement;
  return Boolean(activeElement?.matches?.("select[data-order]") && ordersBody.contains(activeElement));
}

async function refreshOrders({ force = false } = {}) {
  const result = await api("/api/admin/orders");
  ordersCount.textContent = result.orders.length;
  if (!force && isStatusSelectActive()) return;

  ordersBody.innerHTML = "";

  result.orders.forEach((order) => {
    const row = document.createElement("tr");
    const statusOptions = [...STATUS_OPTIONS];
    if (!statusOptions.some(([value]) => value === order.status)) {
      statusOptions.unshift([order.status, order.status_label || order.status, true]);
    }
    const options = statusOptions.map(([value, label, disabled]) => {
      const selected = value === order.status ? "selected" : "";
      return `<option value="${value}" ${selected} ${disabled ? "disabled" : ""}>${label}</option>`;
    }).join("");
    row.innerHTML = `
      <td class="order-number">${escapeHtml(orderNumberLabel(order))}</td>
      <td>${escapeHtml(sourceLabel(order))}</td>
      <td>${escapeHtml(formatDate(order.created_at))}</td>
      <td>${escapeHtml(order.customer_name)}</td>
      <td class="order-text-cell">${escapeHtml(orderTextLabel(order))}</td>
      <td><select data-order="${order.id}">${options}</select></td>
      <td>
        <div class="row-actions">
          <a class="small muted" href="/status/${order.id}" target="_blank">Статус</a>
          <a class="small muted" href="/admin/orders/${order.id}/3d" target="_blank" rel="noopener">3D</a>
          <button class="danger small-button" data-delete="${order.id}" ${order.status === "printing" ? "disabled" : ""}>Удалить</button>
        </div>
      </td>
    `;
    row.querySelector("select").addEventListener("change", async (event) => {
      const select = event.currentTarget;
      select.disabled = true;
      adminMessage.classList.remove("error");
      try {
        await api(`/api/admin/orders/${order.id}/status`, {
          method: "POST",
          body: JSON.stringify({ status: select.value }),
        });
        await refreshOrders({ force: true });
      } catch (error) {
        select.value = order.status;
        select.disabled = false;
        adminMessage.textContent = error.message;
        adminMessage.classList.add("error");
      }
    });
    row.querySelector("[data-delete]").addEventListener("click", async () => {
      if (!window.confirm(`Удалить заказ ${order.customer_name}?`)) return;
      adminMessage.classList.remove("error");
      try {
        await api(`/api/admin/orders/${order.id}`, { method: "DELETE" });
        await refreshAll();
      } catch (error) {
        adminMessage.textContent = error.message;
        adminMessage.classList.add("error");
      }
    });
    ordersBody.appendChild(row);
  });
}

async function refreshTools() {
  const result = await api("/api/admin/tools");
  toolState = result;
  toolStatus.textContent = result.openscad.ok ? "OpenSCAD готов" : result.openscad.message;
}

function renderPrinterStatus(status) {
  printerState = status;
  const online = Boolean(status.online);
  const state = status.state || "offline";
  const stateClass = `printer-${state.replaceAll("_", "-")}`;
  const isPaused = Boolean(status.paused);
  const needsFilamentChange = Boolean(status.filament_change_required);

  printerWidget.classList.remove(...PRINTER_WIDGET_CLASSES);
  printerWidget.classList.add(online ? "printer-online" : "printer-offline", stateClass);
  printerWidget.classList.toggle("needs-filament", needsFilamentChange);
  printerOnline.textContent = status.online_label || (online ? "online" : "offline");
  printerPhase.textContent = status.label || state.replaceAll("_", " ");
  printerTime.textContent = `прошло ${formatDuration(status.elapsed_seconds)} / осталось ${formatDuration(status.remaining_seconds)}`;
  printerWidget.title = status.raw_state ? `OctoPrint: ${status.raw_state}` : status.message || "";
  filamentNotice.hidden = !needsFilamentChange;
  const controlEnabled = Boolean(status.online);
  heatBed.disabled = !controlEnabled;
  coolBed.disabled = !controlEnabled;
  resumePrint.disabled = !(controlEnabled && isPaused);
  resumePrint.title = needsFilamentChange
    ? "Замените пластик и продолжите печать"
    : isPaused
      ? "Продолжить печать, поставленную на паузу"
      : "Кнопка активна, когда OctoPrint поставил печать на паузу";
  stopPrint.disabled = !controlEnabled;
}

async function refreshPrinterStatus() {
  try {
    renderPrinterStatus(await api("/api/admin/printer/status"));
  } catch (error) {
    renderPrinterStatus({
      online: false,
      online_label: "offline",
      state: "offline",
      label: "offline",
      message: error.message,
    });
  }
}

function chooseActiveBatch(batches) {
  const selectableBatches = batches.filter((batch) => batch.status !== "error" && !batchHasBlockedOrders(batch));
  if (activeBatch) {
    const current = selectableBatches.find((batch) => batch.id === activeBatch.id);
    if (current) return current;
  }
  return selectableBatches.find(isBatchReadyForPrint)
    || selectableBatches.find((batch) => batch.status === "queued")
    || selectableBatches.find((batch) => batch.kind === "blank" && batch.status === "printed")
    || null;
}

function blankPreviewStateLabel(preview) {
  const labels = {
    planned: "План заготовок",
    printing_blanks: "Печать заготовок",
    active: "Заготовки готовы",
    full: "Стол заполнен",
  };
  return labels[preview?.state] || "Заготовки";
}

function assignmentTitle(assignment) {
  if (!assignment) return "";
  return [assignment.print_line_1, assignment.print_line_2].filter(Boolean).join(" ")
    || assignment.car_number
    || assignment.customer_name
    || "заказ";
}

function assignmentNumberLabel(assignment) {
  return assignment?.order_number ? `#${assignment.order_number}` : "";
}

function assignmentMeta(assignment) {
  if (!assignment) return "свободно";
  const status = assignment.batch_status_label || assignment.batch_status || assignment.order_status || "";
  return [assignment.customer_name, status].filter(Boolean).join(" · ");
}

function blankStateSummary(preview) {
  const counts = preview?.state_counts || {};
  return BLANK_SLOT_STATES
    .map(([code, label]) => {
      const count = Number(counts[String(code)] || 0);
      return count ? `${label}: ${count}` : "";
    })
    .filter(Boolean)
    .join(" · ");
}

function syncBlankTargetInput(preview = blankPreview) {
  if (!blankTargetCount) return;
  const max = Math.max(1, Number(preview?.slot_count || blankTargetCount.max || 18));
  blankTargetCount.max = String(max);
  let value = Number(blankTargetCount.value || max);
  if (!Number.isFinite(value)) value = max;
  value = Math.max(1, Math.min(max, Math.floor(value)));
  blankTargetCount.value = String(value);
  if (printBlanks) printBlanks.textContent = `Напечатать ${value} заготовок`;
}

function canEditBlankSlot(preview, slot) {
  if (!preview?.active_blank_batch?.id) return false;
  return ![3, 6].includes(Number(slot.state_code));
}

function blankSlotStateSelect(preview, slot) {
  const select = document.createElement("select");
  select.className = "blank-state-select";
  select.title = "Статус места";
  select.value = String(slot.state_code || 1);
  select.disabled = !canEditBlankSlot(preview, slot);
  select.addEventListener("click", (event) => event.stopPropagation());
  BLANK_SLOT_STATES.forEach(([code, label]) => {
    const option = document.createElement("option");
    option.value = String(code);
    option.textContent = `${code}. ${label}`;
    select.appendChild(option);
  });
  select.value = String(slot.state_code || 1);
  select.addEventListener("change", async (event) => {
    const target = event.currentTarget;
    target.disabled = true;
    adminMessage.textContent = "";
    adminMessage.classList.remove("error");
    try {
      await updateBlankSlotState(preview.active_blank_batch.id, slot.index, target.value);
      await refreshAll();
    } catch (error) {
      target.value = String(slot.state_code || 1);
      target.disabled = false;
      adminMessage.textContent = error.message;
      adminMessage.classList.add("error");
    }
  });

  return select;
}

function renderBlankTablePreview(preview, bedWidth, bedHeight) {
  const slots = preview?.slots || [];
  const printable = canStartPrint(activeBatch);
  const activeBlankId = preview?.active_blank_batch?.id;

  batchStatus.textContent = blankPreviewStateLabel(preview);
  batchStatus.classList.toggle("ok", preview?.state === "active" || preview?.state === "planned");
  batchStatus.classList.toggle("error", Boolean(preview?.needs_new_blanks));
  bedMeta.textContent = [
    preview?.message || `План стола: ${slots.length} заготовок.`,
    activeBlankId ? `стол ${activeBlankId}` : "",
    blankStateSummary(preview),
    `${formatMm(bedWidth)}×${formatMm(bedHeight)} мм`,
  ].filter(Boolean).join(" · ");

  printBatch.disabled = !printable;
  printBatch.title = printable
    ? "Проверить принтер и запустить очередь заказов поверх заготовок"
    : preview?.needs_new_blanks
      ? "Все заготовки уже назначены заказам — напечатайте новый стол заготовок"
      : !isPrinterReadyForPrint()
        ? "Принтер не готов к запуску"
        : !preview?.can_accept_orders
          ? "Сначала напечатайте заготовки"
          : "Нет подготовленной overlay-партии";

  if (!slots.length) {
    const empty = document.createElement("div");
    empty.className = "bed-empty";
    empty.textContent = "Заготовки не помещаются на текущий стол";
    bedPreview.appendChild(empty);
    return;
  }

  slots.forEach((slot) => {
    const assignment = slot.assignment || null;
    const stateCode = Number(slot.state_code || 1);
    const state = slot.state || "empty";
    const isPrintingNow = stateCode === 3 || stateCode === 6;
    const width = Number(slot.width_mm || 20);
    const height = Number(slot.height_mm || 20);
    const x = Number(slot.x_mm || 0);
    const y = Number(slot.y_mm || 0);
    const node = document.createElement("div");
    node.className = [
      "bed-item",
      "blank-slot",
      `state-${state}`,
      assignment ? "assigned" : "free",
      isPrintingNow ? "printing-now" : "",
      assignment?.batch_status || "",
    ].filter(Boolean).join(" ");
    node.style.left = `${(x / bedWidth) * 100}%`;
    node.style.bottom = `${(y / bedHeight) * 100}%`;
    node.style.width = `${(width / bedWidth) * 100}%`;
    node.style.height = `${(height / bedHeight) * 100}%`;
    node.title = assignment
      ? `${assignmentTitle(assignment)} · слот #${Number(slot.index) + 1}`
      : `${slot.state_label || "Пустое место"} · слот #${Number(slot.index) + 1}`;

    node.appendChild(blankSlotStateSelect(preview, slot));
    const number = document.createElement("em");
    number.className = "blank-order-number";
    number.textContent = assignmentNumberLabel(assignment);
    const title = document.createElement("strong");
    title.textContent = assignment ? assignmentTitle(assignment) : `#${Number(slot.index) + 1}`;
    const meta = document.createElement("span");
    meta.textContent = assignment ? assignmentMeta(assignment) : slot.state_label || "пустое место";
    node.append(number, title, meta);
    if (isPrintingNow) {
      const throbber = document.createElement("span");
      throbber.className = "slot-throbber";
      node.appendChild(throbber);
    }
    bedPreview.appendChild(node);

    const row = document.createElement("div");
    row.className = `batch-item-row ${assignment ? "is-assigned" : "is-free"} state-${state}`;
    [
      `#${Number(slot.index) + 1}`,
      assignment ? assignmentTitle(assignment) : "свободно",
      `${formatMm(width)}×${formatMm(height)} мм`,
      assignment ? assignmentMeta(assignment) : slot.state_label || "пустое место",
    ].forEach((value, cellIndex) => {
      const cell = document.createElement("span");
      if (cellIndex === 0) {
        cell.className = "batch-item-slot-cell";
        cell.append(blankSlotStateSelect(preview, slot), document.createTextNode(value));
      } else {
        cell.textContent = value;
      }
      row.appendChild(cell);
    });
    batchItems.appendChild(row);
  });
}

function renderBatchPreview(payload) {
  const batches = payload.batches || [];
  bedSizeMm = payload.bed_size_mm || [220, 220];
  blankPreview = payload.blank_preview || null;
  hasPrintedBlankBatch = Boolean(blankPreview?.has_active_blanks)
    || batches.some((batch) => batch.kind === "blank" && batch.status === "printed");
  activeBatch = chooseActiveBatch(batches);
  syncBlankTargetInput(blankPreview);

  bedPreview.innerHTML = "";
  batchItems.innerHTML = "";
  batchStatus.classList.remove("ok", "error");
  const [bedWidth, bedHeight] = bedSizeMm.map(Number);
  bedPreview.style.aspectRatio = `${bedWidth || 220} / ${bedHeight || 220}`;

  if (blankPreview?.slots?.length) {
    renderBlankTablePreview(blankPreview, bedWidth || 220, bedHeight || 220);
    return;
  }

  if (!activeBatch) {
    batchStatus.textContent = "Нет партии";
    bedMeta.textContent = "Подготовьте оплаченные заказы, чтобы увидеть раскладку.";
    bedPreview.innerHTML = `<div class="bed-empty">Стол пуст</div>`;
    printBatch.disabled = true;
    printBatch.title = "Нет подготовленной партии";
    return;
  }

  const hasBlocked = batchHasBlockedOrders(activeBatch);
  const batchReady = isBatchReadyForPrint(activeBatch);
  const printable = canStartPrint(activeBatch);
  batchStatus.textContent = activeBatch.status_label || activeBatch.status;
  batchStatus.classList.toggle("ok", batchReady);
  batchStatus.classList.toggle("error", hasBlocked || activeBatch.status === "error");
  const blankSlots = activeBatch.metadata?.slots || [];
  const visibleCount = activeBatch.items.length || blankSlots.length;
  bedMeta.textContent = `${activeBatch.id} · ${activeBatch.kind || "order"} · ${formatMm(bedWidth)}×${formatMm(bedHeight)} мм · ${visibleCount} шт.`;
  printBatch.disabled = !printable;
  printBatch.title = printable
    ? "Проверить принтер и запустить очередь заказов поверх заготовок"
    : hasBlocked
      ? "В партии есть удаленные заказы"
      : !isPrinterReadyForPrint()
        ? "Принтер не готов к запуску"
        : !canCreateGcode(activeBatch)
          ? "Slicing выключен, G-code еще не создан"
          : "Партия не готова к печати";

  const previewRows = activeBatch.items.length
    ? activeBatch.items.filter((item) => item.status !== "unpaid")
    : blankSlots.map((slot) => ({
        order_id: `slot-${slot.index}`,
        customer_name: "Заготовка",
        car_number: `#${Number(slot.index) + 1}`,
        status: activeBatch.status,
        width_mm: slot.width_mm,
        height_mm: slot.height_mm,
        position_x_mm: slot.x_mm,
        position_y_mm: slot.y_mm,
      }));

  previewRows.forEach((item) => {
    const width = Number(item.width_mm || 20);
    const height = Number(item.height_mm || 20);
    const x = Number(item.position_x_mm || 0);
    const y = Number(item.position_y_mm || 0);
    const node = document.createElement("div");
    node.className = `bed-item ${item.archived_at ? "archived" : ""}`;
    node.style.left = `${(x / bedWidth) * 100}%`;
    node.style.bottom = `${(y / bedHeight) * 100}%`;
    node.style.width = `${(width / bedWidth) * 100}%`;
    node.style.height = `${(height / bedHeight) * 100}%`;
    node.title = `${item.customer_name} · ${formatMm(width)}×${formatMm(height)} мм`;

    const title = document.createElement("strong");
    title.textContent = item.car_number || item.customer_name;
    const meta = document.createElement("span");
    meta.textContent = item.archived_at ? "удален" : item.customer_name;
    node.append(title, meta);
    bedPreview.appendChild(node);

    const row = document.createElement("div");
    row.className = "batch-item-row";
    row.innerHTML = `
      <span>${item.car_number || "без номера"}</span>
      <span>${item.customer_name}</span>
      <span>${formatMm(width)}×${formatMm(height)} мм</span>
      <span>${item.archived_at ? "удален" : item.status}</span>
    `;
    batchItems.appendChild(row);
  });
}

async function refreshBatches() {
  renderBatchPreview(await api("/api/admin/batches"));
}

async function refreshSystemInfo() {
  const response = await fetch("/api/system/info");
  const result = await response.json();
  const qr = result.qr || {};
  document.querySelector("#wifiQr").src = result.wifi_qr || qr.wifi || "";
  document.querySelector("#orderQr").src = result.site_qr || qr.order || "";
  document.querySelector("#adminQr").src = result.admin_qr || qr.admin || "";
  document.querySelector("#wifiSsid").textContent = result.wifi_ssid ? `SSID: ${result.wifi_ssid}` : "";
  document.querySelector("#siteUrl").textContent = result.site_url;
  document.querySelector("#adminUrl").textContent = result.admin_url;
}

async function refreshAll() {
  try {
    await refreshOrders();
    await refreshTools();
    await refreshPrinterStatus();
    await refreshBatches();
    adminMessage.textContent = "";
    adminMessage.classList.remove("error");
    return true;
  } catch (error) {
    if (error.status === 401 || error.status === 429 || String(error.message).includes("Invalid admin PIN")) {
      localStorage.removeItem("adminPin");
      showLogin();
      throw error;
    }
    adminMessage.textContent = error.message;
    adminMessage.classList.add("error");
    return false;
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (loginButton.disabled) return;
  loginMessage.textContent = "";
  loginMessage.classList.remove("error");
  localStorage.setItem("adminPin", pinInput.value);
  try {
    await refreshAll();
    showAdmin();
  } catch (error) {
    localStorage.removeItem("adminPin");
    showLogin();
    if (error.status === 429) {
      startLoginLock(error.retryAfterSeconds);
      return;
    }
    loginMessage.textContent = authErrorMessage(error);
    loginMessage.classList.add("error");
  }
});

prepareQueue.addEventListener("click", async () => {
  adminMessage.textContent = "Готовим печатный стол";
  try {
    const result = await api("/api/admin/print-queue/prepare", { method: "POST" });
    const diagnostics = result.batching.diagnostics || [];
    adminMessage.textContent = diagnostics.join(" | ") || "Печатный стол подготовлен";
    await refreshAll();
  } catch (error) {
    adminMessage.textContent = error.message;
    adminMessage.classList.add("error");
  }
});

downloadStats.addEventListener("click", async () => {
  downloadStats.disabled = true;
  adminMessage.textContent = "Готовим статистику";
  adminMessage.classList.remove("error");
  try {
    await downloadStatistics();
    adminMessage.textContent = "Статистика скачана";
  } catch (error) {
    if (error.status === 401 || error.status === 429 || String(error.message).includes("Invalid admin PIN")) {
      localStorage.removeItem("adminPin");
      showLogin();
      if (error.status === 429) startLoginLock(error.retryAfterSeconds);
    } else {
      adminMessage.textContent = error.message;
      adminMessage.classList.add("error");
    }
  } finally {
    downloadStats.disabled = false;
  }
});

blankTargetCount?.addEventListener("input", () => syncBlankTargetInput(blankPreview));
blankTargetCount?.addEventListener("change", () => syncBlankTargetInput(blankPreview));

printBlanks.addEventListener("click", async () => {
  syncBlankTargetInput(blankPreview);
  const targetCount = Number(blankTargetCount?.value || blankPreview?.slot_count || 18);
  if (!window.confirm(`Напечатать заготовки до ${targetCount} занятых мест без текста?`)) return;
  adminMessage.textContent = "Проверяем принтер и запускаем печать заготовок";
  adminMessage.classList.remove("error");
  try {
    const result = await api("/api/admin/blanks/print", {
      method: "POST",
      body: JSON.stringify({ target_count: targetCount }),
    });
    adminMessage.textContent = result.message || `Печать заготовок запущена: ${result.selected_count ?? result.slots?.length ?? 0} шт.`;
    await refreshAll();
  } catch (error) {
    adminMessage.textContent = error.message;
    adminMessage.classList.add("error");
    await refreshPrinterStatus();
  }
});

printBatch.addEventListener("click", async () => {
  if (!activeBatch || !canStartPrint(activeBatch)) return;
  if (!window.confirm("Запустить автоматическую печать заказов поверх заготовок?")) return;
  adminMessage.textContent = "Проверяем принтер и запускаем очередь";
  adminMessage.classList.remove("error");
  try {
    const result = await api("/api/admin/print-queue/run", { method: "POST" });
    adminMessage.textContent = result.started?.message || "Очередь запущена";
    await refreshAll();
  } catch (error) {
    adminMessage.textContent = error.message;
    adminMessage.classList.add("error");
    await refreshPrinterStatus();
  }
});

heatBed.addEventListener("click", async () => {
  adminMessage.textContent = "Разогреваем стол и хотэнд";
  adminMessage.classList.remove("error");
  try {
    const result = await api("/api/admin/printer/bed/heat", { method: "POST" });
    adminMessage.textContent = result.message || "Стол и хотэнд разогреваются";
    await refreshPrinterStatus();
  } catch (error) {
    adminMessage.textContent = error.message;
    adminMessage.classList.add("error");
    await refreshPrinterStatus();
  }
});

coolBed.addEventListener("click", async () => {
  adminMessage.textContent = "Отключаем подогрев стола и хотэнда";
  adminMessage.classList.remove("error");
  try {
    const result = await api("/api/admin/printer/bed/cool", { method: "POST" });
    adminMessage.textContent = result.message || "Подогрев стола и хотэнда отключен";
    await refreshPrinterStatus();
  } catch (error) {
    adminMessage.textContent = error.message;
    adminMessage.classList.add("error");
    await refreshPrinterStatus();
  }
});

resumePrint.addEventListener("click", async () => {
  if (resumePrint.disabled) return;
  if (!window.confirm("Пластик заменен и сопло готово продолжать печать?")) return;
  adminMessage.textContent = "Продолжаем печать";
  adminMessage.classList.remove("error");
  try {
    const result = await api("/api/admin/printer/resume", { method: "POST" });
    adminMessage.textContent = result.message || "Печать продолжена";
    await refreshAll();
  } catch (error) {
    adminMessage.textContent = error.message;
    adminMessage.classList.add("error");
    await refreshPrinterStatus();
  }
});

stopPrint.addEventListener("click", async () => {
  if (!window.confirm("Остановить печать и вернуть печатавшиеся заказы в очередь?")) return;
  adminMessage.textContent = "Останавливаем печать";
  adminMessage.classList.remove("error");
  try {
    const result = await api("/api/admin/printer/stop", { method: "POST" });
    adminMessage.textContent = result.message || "Печать остановлена, заказы возвращены в очередь";
    await refreshAll();
  } catch (error) {
    adminMessage.textContent = error.message;
    adminMessage.classList.add("error");
    await refreshPrinterStatus();
  }
});

logout.addEventListener("click", () => {
  localStorage.removeItem("adminPin");
  showLogin();
});

adminQrSpoiler?.addEventListener("click", () => {
  setAdminQrSpoilerHidden(!isAdminQrSpoilerHidden());
});

setAdminQrSpoilerHidden(true);

if (pin()) {
  showAdmin();
  refreshAll().catch((error) => {
    if (error.status === 429) startLoginLock(error.retryAfterSeconds);
  });
} else {
  showLogin();
}
refreshSystemInfo();
setInterval(() => {
  if (!admin.hidden) refreshAll().catch(() => {});
}, 5000);
