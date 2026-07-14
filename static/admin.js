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

const PRINTER_WIDGET_CLASSES = [
  "printer-online",
  "printer-offline",
  "printer-waiting",
  "printer-heating",
  "printer-printing",
  "printer-done-printing",
];

let loginLockTimer = null;
let activeBatch = null;
let bedSizeMm = [220, 220];
let toolState = null;
let printerState = null;

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

function sourceLabel(order) {
  return order.source === "telegram" ? "Telegram" : "Web";
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
  return isBatchReadyForPrint(batch) && isPrinterReadyForPrint();
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

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...headers(), ...(options.headers || {}) },
  });
  const result = await response.json();
  if (!response.ok) {
    const detail = result.detail;
    const message = typeof detail === "string" ? detail : detail?.message || "Ошибка запроса";
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
      <td>${sourceLabel(order)}</td>
      <td>${formatDate(order.created_at)}</td>
      <td>${order.customer_name}</td>
      <td>${order.phone}</td>
      <td>${order.car_number}</td>
      <td>${order.design_name} · ${order.size_label}</td>
      <td><select data-order="${order.id}">${options}</select></td>
      <td>
        <div class="row-actions">
          <a class="small muted" href="/status/${order.id}" target="_blank">Статус</a>
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
  return selectableBatches.find(isBatchReadyForPrint) || selectableBatches.find((batch) => batch.status === "queued") || null;
}

function renderBatchPreview(payload) {
  const batches = payload.batches || [];
  bedSizeMm = payload.bed_size_mm || [220, 220];
  activeBatch = chooseActiveBatch(batches);

  bedPreview.innerHTML = "";
  batchItems.innerHTML = "";
  batchStatus.classList.remove("ok", "error");
  const [bedWidth, bedHeight] = bedSizeMm.map(Number);
  bedPreview.style.aspectRatio = `${bedWidth || 220} / ${bedHeight || 220}`;

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
  bedMeta.textContent = `${activeBatch.id} · ${formatMm(bedWidth)}×${formatMm(bedHeight)} мм · ${activeBatch.items.length} шт.`;
  printBatch.disabled = !printable;
  printBatch.title = printable
    ? "Проверить принтер и запустить печать выбранной партии"
    : hasBlocked
      ? "В партии есть удаленные заказы"
      : !isPrinterReadyForPrint()
        ? "Принтер не готов к запуску"
        : !canCreateGcode(activeBatch)
          ? "Slicing выключен, G-code еще не создан"
          : "Партия не готова к печати";

  activeBatch.items.filter((item) => item.status !== "unpaid").forEach((item) => {
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
  document.querySelector("#wifiQr").src = qr.wifi || result.wifi_qr || "";
  document.querySelector("#orderQr").src = qr.order || result.site_qr || "";
  document.querySelector("#adminQr").src = qr.admin || result.admin_qr || "";
  document.querySelector("#wifiSsid").textContent = result.wifi_ssid ? `SSID: ${result.wifi_ssid}` : "";
  document.querySelector("#siteUrl").textContent = result.site_url;
  document.querySelector("#adminUrl").textContent = result.admin_url;
  document.querySelector("#addresses").textContent = (result.fallback_urls || result.addresses || [])
    .filter((item) => item !== result.site_url)
    .join(" · ");
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

printBatch.addEventListener("click", async () => {
  if (!activeBatch || !canStartPrint(activeBatch)) return;
  if (!window.confirm(`Запустить печать партии ${activeBatch.id}?`)) return;
  adminMessage.textContent = "Проверяем принтер и запускаем печать";
  adminMessage.classList.remove("error");
  try {
    const result = await api(`/api/admin/batches/${activeBatch.id}/print`, { method: "POST" });
    adminMessage.textContent = result.message || "Печать запущена";
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
