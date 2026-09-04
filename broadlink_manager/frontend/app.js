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
      if (btn.dataset.tab === "codigos") loadCodes();
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
          ${
            caps.rf_bands
              ? `<span class="device-bands">Bandas RF: ${escapeHtml(caps.rf_bands)}</span>`
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
    loadCodes();
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

// --- codes -----------------------------------------------------------------

let codeGroups = [];
let codesFilter = "";
// Collapsed state lives outside the render so a refresh does not re-open groups
// the user closed.
const collapsedGroups = new Set();

async function loadCodes() {
  const container = $("#codes-groups");
  if (!selectedMac) {
    codeGroups = [];
    container.innerHTML =
      '<p class="list-empty">Elegí un dispositivo en la pestaña Dispositivos para ver sus códigos.</p>';
    $("#codes-count").textContent = "";
    return;
  }

  try {
    const res = await fetch(`${API_BASE}api/codes/${encodeURIComponent(selectedMac)}`);
    if (!res.ok) {
      let detail = `Error HTTP ${res.status}`;
      try {
        const data = await res.json();
        if (data && data.detail) detail = data.detail;
      } catch (err) {
        void err;
      }
      container.innerHTML = `<p class="list-empty error">${escapeHtml(detail)}</p>`;
      return;
    }
    const data = await res.json();
    codeGroups = data.groups;
    renderCodes();
  } catch (err) {
    container.innerHTML = `<p class="list-empty error">No se pudieron leer los códigos: ${escapeHtml(
      err
    )}</p>`;
  }
}

function filteredGroups() {
  if (!codesFilter) return codeGroups;
  const needle = codesFilter.toLowerCase();
  return codeGroups
    .map((group) => {
      // A group whose name matches keeps all its commands; otherwise only the
      // matching ones, so searching "power" does not hide which equipment it
      // belongs to.
      if (group.subdevice.toLowerCase().includes(needle)) return group;
      const commands = group.commands.filter((c) => c.command.toLowerCase().includes(needle));
      return commands.length ? { ...group, commands } : null;
    })
    .filter(Boolean);
}

function renderCodes() {
  const container = $("#codes-groups");
  const groups = filteredGroups();
  const total = codeGroups.reduce((n, g) => n + g.commands.length, 0);
  const shown = groups.reduce((n, g) => n + g.commands.length, 0);

  $("#codes-count").textContent = codesFilter
    ? `${shown} de ${total}`
    : `${total} código${total === 1 ? "" : "s"}`;

  if (!groups.length) {
    container.innerHTML = `<p class="list-empty">${
      total
        ? "Sin coincidencias"
        : "Este dispositivo todavía no tiene códigos guardados. Aprendé uno desde la pestaña Aprender."
    }</p>`;
    return;
  }

  container.innerHTML = groups
    .map((group) => {
      const collapsed = collapsedGroups.has(group.subdevice);
      const rows = group.commands
        .map(
          (c) => `
        <tr>
          <td>${escapeHtml(c.command)}</td>
          <td class="code-preview">${escapeHtml(c.preview)}…</td>
          <td>${
            c.toggle
              ? '<span class="toggle-tag" title="Aprendido con la opción alternativa: Home Assistant alterna entre dos códigos en cada envío">toggle</span>'
              : ""
          }</td>
          <td>
            <button class="send-btn" data-group="${escapeHtml(group.subdevice)}" data-command="${escapeHtml(
            c.command
          )}">Probar</button>
            <button class="secondary edit-btn" data-group="${escapeHtml(
              group.subdevice
            )}" data-command="${escapeHtml(c.command)}">Editar</button>
            <button class="danger del-btn" data-group="${escapeHtml(
              group.subdevice
            )}" data-command="${escapeHtml(c.command)}">Borrar</button>
          </td>
        </tr>`
        )
        .join("");

      return `
      <div class="code-group ${collapsed ? "collapsed" : ""}">
        <div class="group-header">
          <button type="button" class="group-toggle" data-group="${escapeHtml(
            group.subdevice
          )}" title="${collapsed ? "Expandir" : "Contraer"}">${collapsed ? "›" : "⌄"}</button>
          <span class="group-name">${escapeHtml(group.subdevice)}</span>
          <span class="group-count">${group.commands.length} código${
        group.commands.length === 1 ? "" : "s"
      }</span>
          <span class="group-actions">
            <button class="secondary rename-group-btn" data-group="${escapeHtml(
              group.subdevice
            )}">Renombrar</button>
            <button class="danger del-group-btn" data-group="${escapeHtml(
              group.subdevice
            )}">Borrar</button>
          </span>
        </div>
        <table><tbody>${rows}</tbody></table>
      </div>`;
    })
    .join("");
}

async function codesAction(url, options, okMessage) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = `Error HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data && data.detail) detail = data.detail;
    } catch (err) {
      void err;
    }
    window.alert(detail);
    return false;
  }
  if (okMessage) window.alert(okMessage);
  return true;
}

function initCodes() {
  $("#codes-search").addEventListener("input", (event) => {
    codesFilter = event.target.value.trim();
    renderCodes();
  });

  // Event delegation: the table is rebuilt on every load, so per-row listeners
  // would pile up.
  $("#codes-groups").addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    const group = button.dataset.group;
    const command = button.dataset.command;
    const base = `${API_BASE}api/codes/${encodeURIComponent(selectedMac)}`;

    if (button.classList.contains("group-toggle")) {
      if (collapsedGroups.has(group)) collapsedGroups.delete(group);
      else collapsedGroups.add(group);
      renderCodes();
      return;
    }

    if (button.classList.contains("send-btn")) {
      const original = button.textContent;
      button.disabled = true;
      button.textContent = "Enviando…";
      try {
        await codesAction(`${base}/send`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ subdevice: group, command }),
        });
      } finally {
        button.disabled = false;
        button.textContent = original;
      }
      return;
    }

    if (button.classList.contains("edit-btn")) {
      openEditModal(group, command);
      return;
    }

    if (button.classList.contains("del-btn")) {
      if (
        !window.confirm(
          `¿Borrar el código "${command}" de ${group}? Las automatizaciones que lo usen dejarán de funcionar.`
        )
      )
        return;
      if (
        await codesAction(
          `${base}/${encodeURIComponent(group)}/${encodeURIComponent(command)}`,
          { method: "DELETE" }
        )
      )
        loadCodes();
      return;
    }

    if (button.classList.contains("rename-group-btn")) {
      const nuevo = window.prompt(
        `Nuevo nombre para "${group}".\n\nOjo: remote.send_command usa este nombre en el parámetro device, así que las automatizaciones que lo mencionen hay que actualizarlas a mano.`,
        group
      );
      if (!nuevo || nuevo === group) return;
      if (
        await codesAction(`${base}/rename-group`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ subdevice: group, new_subdevice: nuevo }),
        })
      )
        loadCodes();
      return;
    }

    if (button.classList.contains("del-group-btn")) {
      const count = codeGroups.find((g) => g.subdevice === group)?.commands.length ?? 0;
      if (
        !window.confirm(
          `¿Borrar el equipo "${group}" con sus ${count} código(s)? No se puede deshacer desde acá (queda un respaldo en /config/broadlink_manager/backups).`
        )
      )
        return;
      if (await codesAction(`${base}/${encodeURIComponent(group)}`, { method: "DELETE" }))
        loadCodes();
    }
  });

  $("#edit-cancel").addEventListener("click", closeEditModal);
  $("#edit-form").addEventListener("submit", submitEdit);
}

function openEditModal(group, command) {
  const form = $("#edit-form");
  form.dataset.group = group;
  form.dataset.command = command;
  $("#edit-modal-title").textContent = `Editar "${command}"`;
  $("#edit-name").value = command;
  $("#edit-group").value = group;
  $("#group-options").innerHTML = codeGroups
    .map((g) => `<option value="${escapeHtml(g.subdevice)}"></option>`)
    .join("");
  showEditError("");
  $("#edit-modal").classList.remove("hidden");
  $("#edit-name").focus();
}

function closeEditModal() {
  $("#edit-modal").classList.add("hidden");
}

function showEditError(message) {
  const el = $("#edit-error");
  el.textContent = message || "";
  el.classList.toggle("hidden", !message);
}

async function submitEdit(event) {
  event.preventDefault();
  const form = $("#edit-form");
  const { group, command } = form.dataset;
  const newName = $("#edit-name").value.trim();
  const newGroup = $("#edit-group").value.trim();
  const base = `${API_BASE}api/codes/${encodeURIComponent(selectedMac)}`;

  if (!newName || !newGroup) {
    showEditError("El nombre y el equipo no pueden estar vacíos.");
    return;
  }

  // Move first, then rename: doing it the other way round would look for the
  // new name in the old group.
  let currentGroup = group;
  if (newGroup !== group) {
    const res = await fetch(`${base}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subdevice: group, command, new_subdevice: newGroup }),
    });
    if (!res.ok) {
      // Validation errors keep the modal open so nothing typed is lost.
      showEditError(await extractDetail(res));
      return;
    }
    currentGroup = newGroup;
  }

  if (newName !== command) {
    const res = await fetch(`${base}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ subdevice: currentGroup, command, new_command: newName }),
    });
    if (!res.ok) {
      showEditError(await extractDetail(res));
      return;
    }
  }

  closeEditModal();
  loadCodes();
}

async function extractDetail(res) {
  let detail = `Error HTTP ${res.status}`;
  try {
    const data = await res.json();
    if (data && data.detail) detail = data.detail;
  } catch (err) {
    void err;
  }
  return detail;
}

function init() {
  initTabs();
  initDeviceList();
  initCodes();
  $("#btn-scan").addEventListener("click", runScan);
  $("#add-device-form").addEventListener("submit", submitAddDevice);

  loadDevices();
  setInterval(loadDevices, POLL_MS);
}

document.addEventListener("DOMContentLoaded", init);
