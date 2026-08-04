const login = document.querySelector("#cashierLogin");
const app = document.querySelector("#cashierApp");
const loginForm = document.querySelector("#cashierLoginForm");
const pinInput = document.querySelector("#cashierPin");
const loginButton = loginForm.querySelector("button[type='submit']");
const loginMessage = document.querySelector("#cashierLoginMessage");
const ordersContainer = document.querySelector("#cashierOrders");
const ordersCount = document.querySelector("#cashierCount");
const message = document.querySelector("#cashierMessage");
const logout = document.querySelector("#cashierLogout");
const dialog = document.querySelector("#unpayDialog");
const dialogOrderName = document.querySelector("#unpayOrderName");
const confirmUnpay = document.querySelector("#confirmUnpay");

let lockTimer = null;
let pendingUnpayOrder = null;
let requestInProgress = false;

class ApiError extends Error {
  constructor(message, status, retryAfterSeconds = null) {
    super(message);
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function cashierPin() {
  return sessionStorage.getItem("cashierPin") || "";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Cashier-Pin": cashierPin(),
      ...(options.headers || {}),
    },
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
    throw new ApiError(
      typeof detail === "string" ? detail : detail?.message || fallback,
      response.status,
      detail?.retry_after_seconds || null,
    );
  }
  return result;
}

function formatTime(value) {
  return value ? new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}

function formatPrice(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return `${number.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} BYN`;
}

function orderNumberLabel(order) {
  return order?.order_number ? `#${order.order_number}` : "#---";
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value ?? "";
  return node.innerHTML;
}

function setLoggedIn(value) {
  login.hidden = value;
  app.hidden = !value;
}

function startLock(seconds) {
  let remaining = Math.max(1, Math.floor(Number(seconds) || 60));
  clearInterval(lockTimer);
  pinInput.disabled = true;
  loginButton.disabled = true;
  const render = () => {
    loginMessage.textContent = `Слишком много попыток. Повторите через ${remaining} сек.`;
    loginMessage.classList.add("error");
  };
  render();
  lockTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(lockTimer);
      pinInput.disabled = false;
      loginButton.disabled = false;
      loginMessage.textContent = "";
      loginMessage.classList.remove("error");
    } else render();
  }, 1000);
}

function orderCard(order) {
  const paid = Boolean(order.paid_at);
  const price = formatPrice(order.price);
  const card = document.createElement("article");
  card.className = `cashier-order ${paid ? "is-paid" : "is-unpaid"}`;
  card.innerHTML = `
    <div class="cashier-order-time">
      <span>${escapeHtml(orderNumberLabel(order))}</span>
      <small>${formatTime(order.created_at)}</small>
    </div>
    <div class="cashier-order-main">
      <h2>${escapeHtml(order.customer_name)}</h2>
      <div>${escapeHtml(order.car_number || "Без номера авто")}</div>
      <div class="muted">${escapeHtml(order.design_name)} · ${escapeHtml(order.size_label)}</div>
    </div>
    <div class="cashier-payment">
      <div class="cashier-price">${price ? escapeHtml(price) : "Цена не задана"}</div>
      <strong>${paid ? "Оплачен" : "Не оплачен"}</strong>
      <button class="${paid ? "danger" : ""}" data-payment="${paid ? "unpaid" : "paid"}">
        ${paid ? "Снять оплату" : "Отметить оплаченным"}
      </button>
    </div>
  `;
  card.querySelector("[data-payment]").addEventListener("click", () => {
    if (paid) {
      pendingUnpayOrder = order;
      dialogOrderName.textContent = order.customer_name;
      dialog.showModal();
    } else {
      updatePayment(order, "paid");
    }
  });
  return card;
}

async function refreshOrders() {
  if (requestInProgress) return;
  const result = await api("/api/cashier/orders");
  ordersCount.textContent = result.orders.length;
  ordersContainer.replaceChildren(...result.orders.map(orderCard));
  if (!result.orders.length) {
    ordersContainer.innerHTML = '<div class="cashier-empty panel">Активных заказов пока нет</div>';
  }
}

async function updatePayment(order, status) {
  if (requestInProgress) return;
  requestInProgress = true;
  message.textContent = "";
  document.querySelectorAll(".cashier-payment button").forEach((button) => { button.disabled = true; });
  confirmUnpay.disabled = true;
  try {
    await api(`/api/cashier/orders/${order.id}/payment`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    requestInProgress = false;
    await refreshOrders();
  } catch (error) {
    requestInProgress = false;
    message.textContent = error.message;
    message.classList.add("error");
    await refreshOrders().catch(() => {});
  } finally {
    confirmUnpay.disabled = false;
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  sessionStorage.setItem("cashierPin", pinInput.value);
  loginMessage.textContent = "";
  try {
    await refreshOrders();
    setLoggedIn(true);
  } catch (error) {
    sessionStorage.removeItem("cashierPin");
    if (error.status === 429) startLock(error.retryAfterSeconds);
    else {
      loginMessage.textContent = error.status === 401 ? "Неверный PIN" : error.message;
      loginMessage.classList.add("error");
    }
  }
});

dialog.addEventListener("close", () => {
  if (dialog.returnValue === "confirm" && pendingUnpayOrder) updatePayment(pendingUnpayOrder, "unpaid");
  pendingUnpayOrder = null;
});

logout.addEventListener("click", () => {
  sessionStorage.removeItem("cashierPin");
  pinInput.value = "";
  setLoggedIn(false);
});

if (cashierPin()) {
  refreshOrders().then(() => setLoggedIn(true)).catch(() => {
    sessionStorage.removeItem("cashierPin");
    setLoggedIn(false);
  });
} else setLoggedIn(false);

setInterval(() => {
  if (!app.hidden && !dialog.open) refreshOrders().catch(() => {});
}, 5000);
