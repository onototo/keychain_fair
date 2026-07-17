import * as THREE from "three";
import { STLLoader } from "/static/vendor/STLLoader.js";
import { OrbitControls } from "/static/vendor/OrbitControls.js";

const TEXT_BLOCKS = [
  ["car", "Строка 1"],
  ["phone", "Телефон"],
];

const login = document.querySelector("#editorLogin");
const app = document.querySelector("#editorApp");
const loginForm = document.querySelector("#editorLoginForm");
const pinInput = document.querySelector("#editorPinInput");
const loginMessage = document.querySelector("#editorLoginMessage");
const editorStatus = document.querySelector("#editorStatus");
const editorMessage = document.querySelector("#editorMessage");
const viewerMessage = document.querySelector("#viewerMessage");
const dimensionOverlay = document.querySelector("#dimensionOverlay");
const designSelect = document.querySelector("#designSelect");
const sizeSelect = document.querySelector("#sizeSelect");
const updatePreview = document.querySelector("#updatePreview");
const savePreset = document.querySelector("#savePreset");
const resetPreset = document.querySelector("#resetPreset");
const logout = document.querySelector("#editorLogout");
const blockTabs = document.querySelector("#blockTabs");
const lineSpacingInput = document.querySelector("#lineSpacing");
const lineSpacingField = lineSpacingInput?.closest("label");
const canvas = document.querySelector("#modelCanvas");

const sampleInputs = {
  customer_name: document.querySelector("#sampleName"),
  car_number: document.querySelector("#sampleCar"),
};

const baseInputs = {
  base_width_mm: document.querySelector("#baseWidth"),
  base_height_mm: document.querySelector("#baseHeight"),
  thickness_mm: document.querySelector("#thickness"),
  relief_height_mm: document.querySelector("#reliefHeight"),
};

const holeInputs = {
  x_mm: document.querySelector("#holeX"),
  y_mm: document.querySelector("#holeY"),
  radius_mm: document.querySelector("#holeRadius"),
};

const blockInputs = {
  x_offset_mm: document.querySelector("#blockX"),
  y_offset_mm: document.querySelector("#blockY"),
  box_width_mm: document.querySelector("#blockBoxWidth"),
  box_height_mm: document.querySelector("#blockBoxHeight"),
  font_size_mm: document.querySelector("#blockFont"),
  relief_height_mm: document.querySelector("#blockRelief"),
};

const heightStepButtons = Array.from(document.querySelectorAll("[data-height-step]"));
const PRINT_FIRST_LAYER_HEIGHT_MM = 0.1;
const PRINT_LAYER_HEIGHT_MM = 0.2;
const BASE_HEIGHT_MIN_MM = 1.1;
const BASE_HEIGHT_MAX_MM = 5.9;
const RELIEF_HEIGHT_MIN_MM = 0.2;
const RELIEF_HEIGHT_MAX_MM = 2.0;

let designs = [];
let activeDesignId = "";
let activeSizeId = "";
let params = null;
let savedParams = null;
let activeBlock = "car";
let busy = false;

let renderer;
let scene;
let camera;
let controls;
let mesh = null;
let grid = null;
let textBoxOverlay = null;
let lastGeometryCenter = null;
let animationStarted = false;

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

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return number.toFixed(3).replace(/\.?0+$/, "");
}

function readNumber(input, fallback) {
  const value = Number(input.value);
  return Number.isFinite(value) ? value : fallback;
}

function clampNumber(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

function layerAlignedValue(value, fallback, minimum, maximum, offset = 0) {
  const number = Number(value);
  const source = Number.isFinite(number) ? number : fallback;
  const clamped = clampNumber(source, minimum, maximum);
  const steps = Math.round((clamped - offset) / PRINT_LAYER_HEIGHT_MM + 1e-9);
  const aligned = offset + steps * PRINT_LAYER_HEIGHT_MM;
  return Number(clampNumber(aligned, minimum, maximum).toFixed(3));
}

function baseHeight(value, fallback = BASE_HEIGHT_MIN_MM) {
  return layerAlignedValue(value, fallback, BASE_HEIGHT_MIN_MM, BASE_HEIGHT_MAX_MM, PRINT_FIRST_LAYER_HEIGHT_MM);
}

function reliefHeight(value, fallback = RELIEF_HEIGHT_MIN_MM) {
  return layerAlignedValue(value, fallback, RELIEF_HEIGHT_MIN_MM, RELIEF_HEIGHT_MAX_MM);
}

function normalizeLayerHeights(target) {
  if (!target) return target;
  target.thickness_mm = baseHeight(target.thickness_mm, target.thickness_mm ?? BASE_HEIGHT_MIN_MM);
  target.relief_height_mm = reliefHeight(target.relief_height_mm, target.relief_height_mm ?? RELIEF_HEIGHT_MIN_MM);
  Object.values(target.text_blocks || {}).forEach((block) => {
    if (block && typeof block === "object") {
      block.relief_height_mm = reliefHeight(block.relief_height_mm, target.relief_height_mm);
    }
  });
  return target;
}

function steppedLayerValue(value, direction, align, minimum, maximum) {
  const current = align(value, value);
  const next = clampNumber(current + direction * PRINT_LAYER_HEIGHT_MM, minimum, maximum);
  return align(next, current);
}

function apiMessage(error) {
  if (error.status === 401) return "Неверный PIN";
  if (error.status === 429) return "Слишком много попыток PIN. Подождите и попробуйте снова.";
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

function getActiveDesign() {
  return designs.find((design) => design.id === activeDesignId) || designs[0] || null;
}

function getActiveSize() {
  const design = getActiveDesign();
  if (!design) return null;
  return design.sizes.find((size) => size.id === activeSizeId) || design.sizes[0] || null;
}

function isCustomTextDesign(design = getActiveDesign()) {
  return design?.print_mode === "custom_text";
}

function editableTextBlocks() {
  return isCustomTextDesign() ? [["car", "Text"]] : TEXT_BLOCKS;
}

function setBusy(value) {
  busy = value;
  updatePreview.disabled = busy || !params;
  savePreset.disabled = busy || !params;
  resetPreset.disabled = busy || !params;
  heightStepButtons.forEach((button) => {
    button.disabled = busy || !params;
  });
}

function setMessage(message, isError = false) {
  editorMessage.textContent = message || "";
  editorMessage.classList.toggle("error-text", Boolean(isError));
}

function setViewerMessage(message) {
  viewerMessage.textContent = message || "";
  viewerMessage.hidden = !message;
}

function markDirty() {
  editorStatus.textContent = "Есть несохраненные изменения";
}

function fillSelects() {
  designSelect.innerHTML = designs
    .map((design) => `<option value="${design.id}">${design.name}</option>`)
    .join("");
  designSelect.value = activeDesignId;
  const design = getActiveDesign();
  sizeSelect.innerHTML = (design?.sizes || [])
    .map((size) => `<option value="${size.id}">${size.label}</option>`)
    .join("");
  sizeSelect.value = activeSizeId;
}

function fillBaseInputs() {
  if (!params) return;
  normalizeLayerHeights(params);
  Object.entries(baseInputs).forEach(([key, input]) => {
    input.value = formatNumber(params[key]);
  });
  Object.entries(holeInputs).forEach(([key, input]) => {
    input.value = formatNumber(params.hole?.[key]);
  });
  if (lineSpacingInput) {
    lineSpacingInput.value = formatNumber(params.line_spacing ?? 1.08);
    lineSpacingInput.disabled = !isCustomTextDesign();
    if (lineSpacingField) lineSpacingField.hidden = !isCustomTextDesign();
  }
}

function fillBlockTabs() {
  blockTabs.innerHTML = "";
  blockTabs.hidden = isCustomTextDesign();
  const blocks = editableTextBlocks();
  if (!blocks.some(([key]) => key === activeBlock)) {
    activeBlock = blocks[0]?.[0] || "car";
  }
  if (isCustomTextDesign()) return;
  blockTabs.style.gridTemplateColumns = `repeat(${Math.max(1, blocks.length)}, minmax(0, 1fr))`;
  blocks.forEach(([key, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = key === activeBlock ? "active" : "";
    button.textContent = label;
    button.addEventListener("click", () => {
      readBlockInputs();
      activeBlock = key;
      fillBlockTabs();
      fillBlockInputs();
    });
    blockTabs.append(button);
  });
}

function fillBlockInputs() {
  const block = params?.text_blocks?.[activeBlock];
  if (!block) return;
  blockInputs.font_size_mm.min = "1";
  Object.entries(blockInputs).forEach(([key, input]) => {
    input.value = formatNumber(block[key]);
  });
}

function readBaseInputs() {
  if (!params) return;
  params.base_width_mm = readNumber(baseInputs.base_width_mm, params.base_width_mm);
  params.base_height_mm = readNumber(baseInputs.base_height_mm, params.base_height_mm);
  params.thickness_mm = baseHeight(readNumber(baseInputs.thickness_mm, params.thickness_mm), params.thickness_mm);
  params.relief_height_mm = reliefHeight(readNumber(baseInputs.relief_height_mm, params.relief_height_mm), params.relief_height_mm);
  params.hole = params.hole || {};
  params.hole.x_mm = readNumber(holeInputs.x_mm, params.hole.x_mm || 0);
  params.hole.y_mm = readNumber(holeInputs.y_mm, params.hole.y_mm || 0);
  params.hole.radius_mm = readNumber(holeInputs.radius_mm, params.hole.radius_mm || 2.35);
  if (lineSpacingInput) {
    params.line_spacing = Number(clampNumber(readNumber(lineSpacingInput, params.line_spacing ?? 1.08), 0.7, 1.8).toFixed(3));
  }
}

function readBlockInputs() {
  if (!params) return;
  params.text_blocks = params.text_blocks || {};
  params.text_blocks[activeBlock] = params.text_blocks[activeBlock] || {};
  const block = params.text_blocks[activeBlock];
  Object.entries(blockInputs).forEach(([key, input]) => {
    const value = readNumber(input, block[key] || 0);
    if (key === "relief_height_mm") {
      block[key] = reliefHeight(value, params.relief_height_mm);
    } else {
      block[key] = value;
    }
  });
}

function readAllInputs() {
  readBaseInputs();
  readBlockInputs();
}

function applySavedPreset(editorParams) {
  const normalizedParams = normalizeLayerHeights(clone(editorParams));
  const sharedThickness = Number(normalizedParams?.thickness_mm);
  const sharedRelief = Number(normalizedParams?.relief_height_mm);
  designs.forEach((design) => {
    (design.sizes || []).forEach((size) => {
      size.editor_params = size.editor_params || {};
      if (Number.isFinite(sharedThickness)) {
        size.thickness_mm = sharedThickness;
        size.editor_params.thickness_mm = sharedThickness;
      }
      if (Number.isFinite(sharedRelief)) {
        size.relief_height_mm = sharedRelief;
        size.editor_params.relief_height_mm = sharedRelief;
      }
    });
  });

  const size = getActiveSize();
  if (size) {
    size.editor_params = clone(normalizedParams);
  }
}

function selectSize(sizeId) {
  const design = getActiveDesign();
  const size = design?.sizes.find((item) => item.id === sizeId) || design?.sizes[0];
  if (!design || !size) return;
  activeSizeId = size.id;
  params = normalizeLayerHeights(clone(size.editor_params));
  savedParams = normalizeLayerHeights(clone(size.editor_params));
  fillSelects();
  fillBaseInputs();
  fillBlockTabs();
  fillBlockInputs();
  setMessage("");
  editorStatus.textContent = "Пресет загружен";
  dimensionOverlay.textContent = "";
  lastGeometryCenter = null;
  clearTextBoxOverlay();
  setBusy(false);
}

function adjustHeightStep(target, delta) {
  if (!params) return;
  readBaseInputs();
  const direction = Number(delta) < 0 ? -1 : 1;
  if (target === "thickness_mm") {
    params.thickness_mm = steppedLayerValue(params.thickness_mm, direction, baseHeight, BASE_HEIGHT_MIN_MM, BASE_HEIGHT_MAX_MM);
  } else if (target === "relief_height_mm") {
    params.relief_height_mm = steppedLayerValue(params.relief_height_mm, direction, reliefHeight, RELIEF_HEIGHT_MIN_MM, RELIEF_HEIGHT_MAX_MM);
  }
  fillBaseInputs();
  markDirty();
}

function selectDesign(designId) {
  const design = designs.find((item) => item.id === designId) || designs[0];
  if (!design) return;
  activeDesignId = design.id;
  activeBlock = isCustomTextDesign(design) ? "car" : activeBlock;
  activeSizeId = design.default_size_id || design.sizes[0]?.id || "";
  selectSize(activeSizeId);
}

function previewElements() {
  const design = getActiveDesign();
  if (!design) return [];
  if (design.print_mode === "by_number_single") return ["car"];
  return [];
}

function sampleText() {
  return {
    customer_name: sampleInputs.customer_name.value || "Nikita",
    car_number: sampleInputs.car_number.value || "1234AB7",
    print_line_1: sampleInputs.car_number.value || "1234AB7",
    print_line_2: "",
  };
}

async function loadDesigns() {
  const payload = await apiJson("/api/admin/model-editor/designs");
  designs = payload.designs || [];
  if (!designs.length) {
    throw new Error("Нет доступных дизайнов");
  }
  activeDesignId = activeDesignId || designs[0].id;
  selectDesign(activeDesignId);
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
  startAnimation();
}

function resetGrid(size) {
  if (grid) scene.remove(grid);
  const gridSize = Math.max(40, Math.ceil(size / 10) * 10);
  grid = new THREE.GridHelper(gridSize, Math.max(8, Math.floor(gridSize / 10)), 0x9aa7ad, 0xd4dde1);
  grid.position.y = 0;
  scene.add(grid);
}

function clearTextBoxOverlay() {
  if (!textBoxOverlay) return;
  scene.remove(textBoxOverlay);
  textBoxOverlay.traverse((item) => {
    if (item.geometry) item.geometry.dispose();
    if (item.material) item.material.dispose();
  });
  textBoxOverlay = null;
}

function activeTextBox() {
  if (!params || !isCustomTextDesign() || !lastGeometryCenter) return null;
  const block = params.text_blocks?.car || {};
  const baseWidth = Number(params.base_width_mm) || 60;
  const baseHeight = Number(params.base_height_mm) || 24;
  const boxWidth = Number(block.box_width_mm) || Math.max(8, baseWidth - 22);
  const boxHeight = Number(block.box_height_mm) || Math.max(5, baseHeight * 0.48);
  const x = baseWidth / 2 + (Number(block.x_offset_mm) || 0) - lastGeometryCenter.x;
  const y = baseHeight / 2 + (Number(block.y_offset_mm) || 0) - lastGeometryCenter.y;
  const z =
    (Number(params.thickness_mm) || 0) +
    (Number(block.relief_height_mm) || Number(params.relief_height_mm) || 0) +
    0.08;
  return { x, y, z, width: boxWidth, height: boxHeight };
}

function updateTextBoxOverlay() {
  if (!scene || !renderer) return;
  clearTextBoxOverlay();
  const box = activeTextBox();
  if (!box) return;

  const halfWidth = box.width / 2;
  const halfHeight = box.height / 2;
  const points = [
    new THREE.Vector3(box.x - halfWidth, box.y - halfHeight, box.z),
    new THREE.Vector3(box.x + halfWidth, box.y - halfHeight, box.z),
    new THREE.Vector3(box.x + halfWidth, box.y + halfHeight, box.z),
    new THREE.Vector3(box.x - halfWidth, box.y + halfHeight, box.z),
    new THREE.Vector3(box.x - halfWidth, box.y - halfHeight, box.z),
  ];

  const group = new THREE.Group();
  const material = new THREE.LineBasicMaterial({ color: 0xd92d20, depthTest: false, transparent: true, opacity: 0.95 });
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const outline = new THREE.Line(geometry, material);
  outline.renderOrder = 20;
  group.add(outline);

  const handleMaterial = new THREE.MeshBasicMaterial({ color: 0xd92d20, depthTest: false });
  const handleSize = Math.max(0.55, Math.min(box.width, box.height) * 0.035);
  points.slice(0, 4).forEach((point) => {
    const handle = new THREE.Mesh(new THREE.BoxGeometry(handleSize, handleSize, handleSize), handleMaterial);
    handle.position.copy(point);
    handle.renderOrder = 21;
    group.add(handle);
  });

  group.rotation.x = -Math.PI / 2;
  textBoxOverlay = group;
  scene.add(textBoxOverlay);
  renderer.render(scene, camera);
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

function startAnimation() {
  if (animationStarted) return;
  animationStarted = true;
  const tick = () => {
    requestAnimationFrame(tick);
    controls.update();
    renderer.render(scene, camera);
  };
  tick();
}

function colorGeometryByZ(geometry, filamentChangeHeightMm) {
  const position = geometry.getAttribute("position");
  const colors = [];
  const base = new THREE.Color(0x2f7f77);
  const relief = new THREE.Color(0xf0b429);
  const rawThreshold = Number(filamentChangeHeightMm);
  const threshold = Number.isFinite(rawThreshold) ? rawThreshold : 0;
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
  clearTextBoxOverlay();

  geometry.computeBoundingBox();
  const rawBox = geometry.boundingBox;
  const centerX = (rawBox.min.x + rawBox.max.x) / 2;
  const centerY = (rawBox.min.y + rawBox.max.y) / 2;
  lastGeometryCenter = { x: centerX, y: centerY };
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
  updateTextBoxOverlay();
}

async function updatePreviewModel() {
  readAllInputs();
  setBusy(true);
  setMessage("");
  setViewerMessage("Генерация STL...");
  try {
    const payload = {
      design_id: activeDesignId,
      size_id: activeSizeId,
      editor_params: params,
      sample_text: sampleText(),
      elements: previewElements(),
    };
    const preview = await apiJson("/api/admin/model-editor/preview", {
      method: "POST",
      body: payload,
    });
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
    setMessage("3D обновлен");
    if (preview.text_layout && preview.text_layout.fits === false) {
      setMessage("\u0428\u0440\u0438\u0444\u0442 \u0443\u043c\u0435\u043d\u044c\u0448\u0435\u043d \u043d\u0438\u0436\u0435 6 \u043c\u043c, \u0447\u0442\u043e\u0431\u044b \u0442\u0435\u043a\u0441\u0442 \u043e\u0441\u0442\u0430\u043b\u0441\u044f \u0432\u043d\u0443\u0442\u0440\u0438 \u0437\u043e\u043d\u044b.", true);
    }
  } catch (error) {
    setViewerMessage("Не удалось построить STL");
    setMessage(apiMessage(error), true);
  } finally {
    setBusy(false);
  }
}

async function saveCurrentPreset() {
  readAllInputs();
  setBusy(true);
  setMessage("Сохранение...");
  try {
    const payload = await apiJson("/api/admin/model-editor/presets", {
      method: "POST",
      body: {
        design_id: activeDesignId,
        size_id: activeSizeId,
        editor_params: params,
      },
    });
    applySavedPreset(payload.editor_params);
    params = normalizeLayerHeights(clone(payload.editor_params));
    savedParams = clone(params);
    fillBaseInputs();
    fillBlockInputs();
    updateTextBoxOverlay();
    editorStatus.textContent = "Пресет сохранен";
    setMessage("Пресет сохранен для будущих заказов");
  } catch (error) {
    setMessage(apiMessage(error), true);
  } finally {
    setBusy(false);
  }
}

function resetCurrentPreset() {
  if (!savedParams) return;
  params = clone(savedParams);
  fillBaseInputs();
  fillBlockInputs();
  updateTextBoxOverlay();
  editorStatus.textContent = "Изменения сброшены";
  setMessage("");
}

async function enterEditor() {
  try {
    await loadDesigns();
    login.hidden = true;
    app.hidden = false;
    if (!renderer) initThree();
  } catch (error) {
    login.hidden = false;
    app.hidden = true;
    loginMessage.textContent = apiMessage(error);
    if (error.status === 401 || error.status === 429) {
      localStorage.removeItem("adminPin");
    }
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  localStorage.setItem("adminPin", pinInput.value);
  loginMessage.textContent = "";
  await enterEditor();
});

designSelect.addEventListener("change", () => {
  selectDesign(designSelect.value);
});

sizeSelect.addEventListener("change", () => {
  selectSize(sizeSelect.value);
});

Object.values(baseInputs).forEach((input) => {
  input.addEventListener("input", () => {
    readBaseInputs();
    updateTextBoxOverlay();
    markDirty();
  });
});

heightStepButtons.forEach((button) => {
  button.addEventListener("click", () => {
    adjustHeightStep(button.dataset.heightStep, button.dataset.stepDelta);
  });
});

Object.values(holeInputs).forEach((input) => {
  input.addEventListener("input", () => {
    readBaseInputs();
    markDirty();
  });
});

Object.values(blockInputs).forEach((input) => {
  input.addEventListener("input", () => {
    readBlockInputs();
    updateTextBoxOverlay();
    markDirty();
  });
});

if (lineSpacingInput) {
  lineSpacingInput.addEventListener("input", () => {
    readBaseInputs();
    markDirty();
  });
}

updatePreview.addEventListener("click", updatePreviewModel);
savePreset.addEventListener("click", saveCurrentPreset);
resetPreset.addEventListener("click", resetCurrentPreset);
logout.addEventListener("click", () => {
  localStorage.removeItem("adminPin");
  app.hidden = true;
  login.hidden = false;
});

setBusy(false);
if (pin()) {
  enterEditor();
}
