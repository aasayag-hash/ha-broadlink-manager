// Resolved from the document URL so the same build works both locally at "/"
// and behind ingress at "/api/hassio_ingress/<token>/".
const API_BASE = new URL(".", window.location.href).pathname;

const $ = (selector) => document.querySelector(selector);

let devices = [];
let selectedMac = null;
let scanning = false;

// Sequence guard: a slow scan must never paint over a newer list. Without it,
// the 5s refresh landing after a manual scan could restore the stale set and
// make a just-added device disappear.
let devicesRequestSeq = 0;

const POLL_MS = 5000;

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function initTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`#tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "aprender") renderLearn();
    });
  });
}

async function loadDevices() {
  const seq = ++devicesRequestSeq;
  try {
    const res = await fetch(`${API_BASE}api/devices`);
    if (!res.ok) return;
    const data = await res.json();
    if (seq !== devicesRequestSeq) return; // stale
    devices = data;
    renderDevices();
  } catch (err) {
    void err; // a failed poll is not worth interrupting the user over
  }
}

function capBadge(label, enabled) {
  return `<span class="cap-badge ${enabled ? "on" : "off"}">${enabled ? "✓" : "✗"} ${label}</span>`;
}

function renderDevices() {
  const list = $("#device-list");
  list.innerHTML = "";

  if (!devices.length) {
    const li = document.createElement("li");
    li.className = "list-empty";
    li.textContent =
      "Todavía no se encontró ningún Broadlink. Probá con Buscar, o agregá uno por IP si está en otra red.";
    list.appendChild(li);
    return;
  }

  devices.forEach((device) => {
    const caps = device.capabilities;
    const statusClass = device.last_error ? "error" : device.online ? "online" : "offline";
    const statusText = device.last_error ? "error" : device.online ? "online" : "offline";

    const capsHtml = caps.learn_ir
      ? [
          capBadge("Aprender IR", caps.learn_ir),
          capBadge("Enviar IR", caps.send_ir),
          capBadge("Aprender RF", caps.learn_rf),
          capBadge("Enviar RF", caps.send_rf),
        ].join("")
      : "";

    const stateHtml = device.state
      ? `<span class="device-state">${escapeHtml(
          Object.entries(device.state)
            .map(([k, v]) => `${k}: ${v}`)
            .join(" · ")
        )}</span>`
      : "";

    const li = document.createElement("li");
    li.className = "device-item";
    li.innerHTML = `
      <div class="device-card ${device.mac === selectedMac ? "selected" : ""}" data-mac="${escapeHtml(
      device.mac
    )}" role="button" tabindex="0">
        <span class="device-info">
          <span class="device-title">${escapeHtml(device.model)}</span>
          <span class="device-endpoint">${escapeHtml(device.host)} · ${escapeHtml(device.mac)}</span>
          ${capsHtml ? `<span class="device-caps">${capsHtml}</span>` : ""}
          ${
            caps.no_learn_reason
              ? `<span class="device-reason">${escapeHtml(caps.no_learn_reason)}</span>`
              : ""
          }
          ${stateHtml}
          ${
            device.last_error
              ? `<span class="device-error">${escapeHtml(device.last_error)}</span>`
              : ""
          }
        </span>
        <span class="device-side">
          <span class="status-pill ${statusClass}"${
      device.last_error ? ` title="${escapeHtml(device.last_error)}"` : ""
    }>${statusText}</span>
          ${device.manual ? '<span class="manual-tag">manual</span>' : ""}
        </span>
      </div>
    `;
    list.appendChild(li);
  });
}

// Event delegation: the list is re-rendered on every poll, so binding per card
// would re-attach listeners several times a minute.
function initDeviceList() {
  const list = $("#device-list");
  list.addEventListener("click", (event) => {
    const card = event.target.closest(".device-card");
    if (!card) return;
    selectedMac = card.dataset.mac;
    renderDevices();
    renderLearn();
  });
}

async function runScan() {
  if (scanning) return; // a second click would start a parallel broadcast sweep
  scanning = true;
  const button = $("#btn-scan");
  const status = $("#scan-status");
  button.disabled = true;
  button.textContent = "Buscando…";
  status.classList.remove("error");
  status.textContent = "Escaneando la red…";

  try {
    const res = await fetch(`${API_BASE}api/devices/scan`, { method: "POST" });
    if (!res.ok) {
      status.classList.add("error");
      status.textContent = `Error HTTP ${res.status}`;
      return;
    }
    devices = await res.json();
    devicesRequestSeq++; // invalidate any poll still in flight
    renderDevices();
    const found = devices.filter((d) => d.online).length;
    status.textContent = found
      ? `${found} dispositivo${found === 1 ? "" : "s"} en línea.`
      : "No se encontró ningún dispositivo. Si está en otra VLAN o red aislada, agregalo por IP.";
  } catch (err) {
    status.classList.add("error");
    status.textContent = `No se pudo escanear: ${err}`;
  } finally {
    scanning = false;
    button.disabled = false;
    button.textContent = "Buscar";
  }
}

async function submitAddDevice(event) {
  event.preventDefault();
  const errorEl = $("#add-device-error");
  errorEl.textContent = "";

  const res = await fetch(`${API_BASE}api/devices`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ip: $("#device-ip").value }),
  });

  if (!res.ok) {
    let detail = `Error HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && data.detail) detail = data.detail;
    } catch (err) {
      void err;
    }
    errorEl.textContent = detail;
    return;
  }

  $("#device-ip").value = "";
  await loadDevices();
}

function renderLearn() {
  const placeholder = $("#learn-placeholder");
  const device = devices.find((d) => d.mac === selectedMac);

  if (!device) {
    placeholder.textContent = "Elegí un dispositivo en la pestaña Dispositivos para empezar.";
    return;
  }
  if (!device.capabilities.learn_ir) {
    placeholder.textContent = `${device.model}: ${device.capabilities.no_learn_reason}`;
    return;
  }
  const modes = [
    device.capabilities.learn_ir ? "IR" : null,
    device.capabilities.learn_rf ? "RF" : null,
  ]
    .filter(Boolean)
    .join(" y ");
  placeholder.textContent = `${device.model} seleccionado. Puede aprender ${modes}. (Captura todavía no implementada.)`;
}

function init() {
  initTabs();
  initDeviceList();
  $("#btn-scan").addEventListener("click", runScan);
  $("#add-device-form").addEventListener("submit", submitAddDevice);

  loadDevices();
  setInterval(loadDevices, POLL_MS);
}

document.addEventListener("DOMContentLoaded", init);
