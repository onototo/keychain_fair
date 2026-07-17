import * as THREE from "three";
import { STLLoader } from "/static/vendor/STLLoader.js";
import { OrbitControls } from "/static/vendor/OrbitControls.js";

const canvas = document.querySelector("#modelCanvas");
const viewerMessage = document.querySelector("#viewerMessage");
const dimensionOverlay = document.querySelector("#dimensionOverlay");
const orderTitle = document.querySelector("#orderTitle");
const orderMeta = document.querySelector("#orderMeta");

const summary = {
  customer: document.querySelector("#summaryCustomer"),
  car: document.querySelector("#summaryCar"),
  design: document.querySelector("#summaryDesign"),
  text: document.querySelector("#summaryText"),
  status: document.querySelector("#summaryStatus"),
};

let renderer;
let scene;
let camera;
let controls;
let mesh = null;
let grid = null;

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function pin() {
  return localStorage.getItem("adminPin") || "";
}

function authHeaders(json = true) {
  const headers = { "X-Admin-Pin": pin() };
  if (json) headers["Content-Type"] = "application/json";
  return headers;
}

function orderIdFromPath() {
  const match = window.location.pathname.match(/^\/admin\/orders\/([^/]+)\/3d$/);
  return match ? decodeURIComponent(match[1]) : "";
}

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return number.toFixed(3).replace(/\.?0+$/, "");
}

function apiMessage(error) {
  if (error.status === 401) return "Неверный PIN. Откройте админку и войдите снова.";
  if (error.status === 409) return "У заказа нет сохраненных параметров 3D модели.";
  return error.message || "Ошибка запроса";
}

async function apiJson(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: authHeaders(true),
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    const detail = data.detail;
    const message = typeof detail === "string" ? detail : detail?.message || response.statusText;
    throw new ApiError(message, response.status);
  }
  return data;
}

function setViewerMessage(message) {
  viewerMessage.textContent = message || "";
  viewerMessage.hidden = !message;
}

function setSummary(order) {
  orderTitle.textContent = `3D предпросмотр ${order.id}`;
  orderMeta.textContent = `${order.design_name} · ${order.size_label}`;
  summary.customer.textContent = order.customer_name || "--";
  summary.car.textContent = order.car_number || "--";
  summary.design.textContent = `${order.design_name || "--"} · ${order.size_label || "--"}`;
  summary.text.textContent = [order.print_line_1, order.print_line_2].filter(Boolean).join(" / ") || "--";
  summary.status.textContent = order.status_label || order.status || "--";
}

function initThree() {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0xf7f8f8);
  camera = new THREE.PerspectiveCamera(42, 1, 0.1, 2000);
  camera.position.set(70, 55, 90);

  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.target.set(0, 0, 0);

  const hemi = new THREE.HemisphereLight(0xffffff, 0xc7d2d9, 2.1);
  scene.add(hemi);
  const key = new THREE.DirectionalLight(0xffffff, 2.4);
  key.position.set(40, -60, 90);
  scene.add(key);

  resetGrid(120);
  const resizeObserver = new ResizeObserver(resizeRenderer);
  resizeObserver.observe(canvas.parentElement);
  resizeRenderer();

  const tick = () => {
    requestAnimationFrame(tick);
    controls.update();
    renderer.render(scene, camera);
  };
  tick();
}

function resetGrid(size) {
  if (grid) scene.remove(grid);
  const gridSize = Math.max(40, Math.ceil(size / 10) * 10);
  grid = new THREE.GridHelper(gridSize, Math.max(8, Math.floor(gridSize / 10)), 0x9aa7ad, 0xd4dde1);
  grid.position.y = 0;
  scene.add(grid);
}

function resizeRenderer() {
  if (!renderer || !canvas.parentElement) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  const width = Math.max(1, Math.floor(rect.width));
  const height = Math.max(1, Math.floor(rect.height));
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}

function colorGeometryByZ(geometry, filamentChangeHeightMm) {
  const position = geometry.getAttribute("position");
  const colors = [];
  const base = new THREE.Color(0x2f7f77);
  const relief = new THREE.Color(0xf0b429);
  const threshold = Number.isFinite(Number(filamentChangeHeightMm)) ? Number(filamentChangeHeightMm) : 0;
  for (let index = 0; index < position.count; index += 3) {
    const z0 = position.getZ(index);
    const z1 = position.getZ(index + 1);
    const z2 = position.getZ(index + 2);
    const color = (z0 + z1 + z2) / 3 <= threshold + 0.01 ? base : relief;
    colors.push(color.r, color.g, color.b, color.r, color.g, color.b, color.r, color.g, color.b);
  }
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
}

function showGeometry(geometry, bounds, filamentChangeHeightMm) {
  if (mesh) scene.remove(mesh);

  geometry.computeBoundingBox();
  const rawBox = geometry.boundingBox;
  const centerX = (rawBox.min.x + rawBox.max.x) / 2;
  const centerY = (rawBox.min.y + rawBox.max.y) / 2;
  geometry.translate(-centerX, -centerY, -rawBox.min.z);
  geometry.computeVertexNormals();
  geometry.computeBoundingBox();
  colorGeometryByZ(geometry, filamentChangeHeightMm);

  const material = new THREE.MeshStandardMaterial({
    roughness: 0.72,
    metalness: 0.02,
    vertexColors: true,
    side: THREE.DoubleSide,
  });
  mesh = new THREE.Mesh(geometry, material);
  mesh.rotation.x = -Math.PI / 2;
  scene.add(mesh);

  const box = new THREE.Box3().setFromObject(mesh);
  const size = new THREE.Vector3();
  box.getSize(size);
  const center = new THREE.Vector3();
  box.getCenter(center);
  const maxDim = Math.max(size.x, size.y, size.z, 20);
  resetGrid(maxDim * 1.6);
  controls.target.copy(center);
  camera.position.set(center.x + maxDim * 0.9, center.y + maxDim * 0.85, center.z + maxDim * 2.1);
  camera.near = 0.1;
  camera.far = maxDim * 20;
  camera.updateProjectionMatrix();
  camera.lookAt(center);
  controls.update();
  renderer.render(scene, camera);

  const width = bounds?.width ?? size.x;
  const height = bounds?.height ?? size.y;
  const z = bounds?.z ?? size.z;
  dimensionOverlay.textContent = `${formatNumber(width)} × ${formatNumber(height)} × ${formatNumber(z)} мм | пауза ${formatNumber(filamentChangeHeightMm)} мм`;
}

async function loadPreview() {
  const orderId = orderIdFromPath();
  if (!orderId) {
    throw new Error("Не найден ID заказа в адресе страницы");
  }

  setViewerMessage("Генерация STL...");
  const preview = await apiJson(`/api/admin/orders/${encodeURIComponent(orderId)}/preview-3d`, { method: "POST" });
  setSummary(preview.order);
  setViewerMessage("Загрузка STL...");

  const response = await fetch(preview.stl_url, { headers: authHeaders(false) });
  if (!response.ok) {
    throw new ApiError(response.statusText, response.status);
  }
  const buffer = await response.arrayBuffer();
  const loader = new STLLoader();
  const geometry = loader.parse(buffer);
  showGeometry(geometry, preview.bounds, preview.filament_change_height_mm);
  setViewerMessage("");
}

initThree();
loadPreview().catch((error) => {
  setViewerMessage(apiMessage(error));
});
