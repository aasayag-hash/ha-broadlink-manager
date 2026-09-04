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
      if (btn.dataset.tab === "entidades") {
        // Entities are built from stored codes, so the table has to be loaded
        // before the form can offer anything to pick.
        loadCodes().then(loadEntities);
        loadMqttSettings();
      }
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
            device.device_class === "Device"
              ? `<span class="device-unknown">La librería no reconoce este modelo (código ${device.devtype}). Probá elegir el modelo equivalente con el botón Modelo.</span>`
              : caps.no_learn_reason
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
          ${
            device.forced_devtype
              ? '<span class="manual-tag" title="El modelo lo elegiste vos, no fue detectado">modelo forzado</span>'
              : ""
          }
          <button type="button" class="secondary model-btn" data-mac="${escapeHtml(
            device.mac
          )}" title="Cambiar el modelo con el que se trata este equipo">Modelo</button>
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
    // The Model button sits inside the card, so it has to be handled first or
    // clicking it would also select the device.
    const modelButton = event.target.closest(".model-btn");
    if (modelButton) {
      event.stopPropagation();
      openModelModal(modelButton.dataset.mac);
      return;
    }

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

// --- learning ---------------------------------------------------------------

let learnTimer = null;
// Remembered per device so the second button of a remote can skip the sweep.
const knownFrequency = {};

const ACTIVE_STATES = ["waiting_ir", "sweeping", "waiting_packet"];

function renderLearn() {
  const info = $("#learn-device");
  const device = devices.find((d) => d.mac === selectedMac);

  if (!device) {
    info.textContent = "Elegí un dispositivo en la pestaña Dispositivos para empezar.";
    $("#learn-start").classList.add("hidden");
    $("#wizard-picker").classList.add("hidden");
    return;
  }
  if (!device.capabilities.learn_ir) {
    info.textContent = `${device.model}: ${device.capabilities.no_learn_reason}`;
    $("#learn-start").classList.add("hidden");
    $("#wizard-picker").classList.add("hidden");
    return;
  }

  info.textContent = `${device.model} · ${device.host}`;
  $("#learn-start").classList.remove("hidden");
  if (!wizard) {
    $("#wizard-picker").classList.remove("hidden");
    loadTemplates();
  }
  $("#btn-learn-rf").classList.toggle("hidden", !device.capabilities.learn_rf);

  const freq = knownFrequency[selectedMac];
  $("#reuse-freq-label").classList.toggle("hidden", !freq || !device.capabilities.learn_rf);
  if (freq) $("#known-freq").textContent = freq;

  pollLearn();
}

async function startLearn(mode) {
  const body = { mode };
  if (mode === "rf" && $("#reuse-freq").checked && knownFrequency[selectedMac]) {
    body.frequency = knownFrequency[selectedMac];
  }

  const res = await fetch(`${API_BASE}api/learn/${encodeURIComponent(selectedMac)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    window.alert(await extractDetail(res));
    return;
  }
  renderLearnState(await res.json());
  startLearnPolling();
}

function startLearnPolling() {
  stopLearnPolling();
  // Faster than the device list: a capture finishes in seconds and the
  // countdown has to move, not jump.
  learnTimer = setInterval(pollLearn, 700);
}

function stopLearnPolling() {
  if (learnTimer) clearInterval(learnTimer);
  learnTimer = null;
}

async function pollLearn() {
  if (!selectedMac) return;
  try {
    const res = await fetch(`${API_BASE}api/learn/${encodeURIComponent(selectedMac)}`);
    if (!res.ok) return;
    renderLearnState(await res.json());
  } catch (err) {
    void err;
  }
}

function renderLearnState(session) {
  const active = ACTIVE_STATES.includes(session.state);
  const captured = session.state === "captured";
  const finished = ["failed", "cancelled"].includes(session.state);

  $("#learn-start").classList.toggle("hidden", active || captured);
  $("#learn-progress").classList.toggle("hidden", !active);
  $("#learn-result").classList.toggle("hidden", !captured && !finished);
  // The template picker would be noise while a capture is running; the wizard
  // panel stays visible so the user keeps sight of what is left.
  if (!wizard) {
    $("#wizard-picker").classList.toggle("hidden", active || captured);
  }

  if (active) {
    $("#learn-state").textContent =
      session.state === "sweeping" ? "buscando frecuencia" : "esperando señal";
    $("#learn-countdown").textContent = session.remaining ? `${session.remaining}s` : "";
    $("#learn-message").textContent = session.message;
    return;
  }

  stopLearnPolling();

  if (captured) {
    if (session.frequency) knownFrequency[selectedMac] = session.frequency;
    // The kind comes from the captured bytes, not from the mode that was
    // requested: an RF remote pointed at an RM pro is picked up by the IR flow
    // too, and the user should see what was really captured.
    $("#learn-result-message").textContent = session.kind
      ? `${session.message} (tipo detectado: ${session.kind})`
      : session.message;
    $("#learn-error").textContent = "";
    $("#learn-code").textContent = session.code;
    $("#learn-code").classList.remove("hidden");
    $("#save-form").classList.remove("hidden");
    $("#retry-actions").classList.add("hidden");
    $("#save-group-options").innerHTML = codeGroups
      .map((g) => `<option value="${escapeHtml(g.subdevice)}"></option>`)
      .join("");

    // In the wizard both names are already decided, so they are filled in and
    // the user only has to test and save.
    if (wizard && wizard.pending) {
      $("#save-group").value = wizard.group;
      $("#save-command").value = wizard.pending.command;
    }
    return;
  }

  if (finished) {
    $("#learn-result-message").textContent = session.state === "cancelled" ? session.message : "";
    $("#learn-error").textContent = session.error || "";
    $("#learn-code").classList.add("hidden");
    $("#save-form").classList.add("hidden");
    $("#retry-actions").classList.remove("hidden");
  }
}

function resetLearn() {
  stopLearnPolling();
  $("#learn-result").classList.add("hidden");
  $("#learn-progress").classList.add("hidden");
  $("#learn-start").classList.remove("hidden");
  $("#save-error").classList.add("hidden");
  $("#save-group").value = "";
  $("#save-command").value = "";
}

async function submitSave(event) {
  event.preventDefault();
  const errorEl = $("#save-error");
  errorEl.classList.add("hidden");

  const res = await fetch(`${API_BASE}api/learn/${encodeURIComponent(selectedMac)}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      subdevice: $("#save-group").value,
      command: $("#save-command").value,
    }),
  });

  if (!res.ok) {
    // Keep the form open on a validation error so nothing typed is lost.
    errorEl.textContent = await extractDetail(res);
    errorEl.classList.remove("hidden");
    return;
  }

  const data = await res.json();
  if (data.frequency) knownFrequency[selectedMac] = data.frequency;
  resetLearn();

  // The codes table has to be reloaded before the wizard redraws: it is what
  // tells the wizard which buttons are done.
  await loadCodes();
  if (wizard) {
    wizard.pending = null;
    renderWizard();
  } else {
    renderLearn();
  }
}

function initLearn() {
  $("#btn-learn-ir").addEventListener("click", () => startLearn("ir"));
  $("#btn-learn-rf").addEventListener("click", () => startLearn("rf"));
  $("#btn-retry").addEventListener("click", resetLearn);
  $("#btn-discard").addEventListener("click", async () => {
    await fetch(`${API_BASE}api/learn/${encodeURIComponent(selectedMac)}/cancel`, {
      method: "POST",
    });
    resetLearn();
    // Discarding drops the pending button but stays in the wizard, so the user
    // can retry it or pick a different one.
    if (wizard) {
      wizard.pending = null;
      renderWizard();
    }
  });

  $("#btn-learn-cancel").addEventListener("click", async () => {
    await fetch(`${API_BASE}api/learn/${encodeURIComponent(selectedMac)}/cancel`, {
      method: "POST",
    });
    pollLearn();
  });

  $("#btn-test-code").addEventListener("click", async (event) => {
    const button = event.target;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Enviando…";
    try {
      const res = await fetch(`${API_BASE}api/learn/${encodeURIComponent(selectedMac)}/test`, {
        method: "POST",
      });
      if (!res.ok) window.alert(await extractDetail(res));
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  });

  $("#save-form").addEventListener("submit", submitSave);
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
          <td>${
            c.kind ? `<span class="kind-tag">${escapeHtml(c.kind)}</span>` : ""
          }</td>
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

// --- template wizard --------------------------------------------------------

let templates = null;
// The wizard in progress: which template, which equipment name, and which
// button is being captured right now.
let wizard = null;

async function loadTemplates() {
  if (templates === null) {
    const res = await fetch(`${API_BASE}api/templates`);
    templates = res.ok ? await res.json() : [];
  }
  $("#template-list").innerHTML = templates
    .map((t) => {
      const required = t.buttons.filter((b) => !b.optional).length;
      return `
      <button type="button" class="template-card" data-id="${escapeHtml(t.id)}">
        <span class="template-name">${t.icon} ${escapeHtml(t.name)}</span>
        <span class="template-count">${required} básicos · ${t.buttons.length} en total</span>
      </button>`;
    })
    .join("");
}

async function startWizard(templateId) {
  const name = window.prompt(
    "¿Cómo querés llamar a este equipo?\n\nVa a agrupar todos sus botones con ese nombre.",
    ""
  );
  if (!name || !name.trim()) return;

  const res = await fetch(
    `${API_BASE}api/templates/${encodeURIComponent(templateId)}/${encodeURIComponent(selectedMac)}`
  );
  if (!res.ok) {
    window.alert(await extractDetail(res));
    return;
  }
  const data = await res.json();
  wizard = { template: data.template, group: name.trim(), pending: null };
  renderWizard();
}

function exitWizard() {
  wizard = null;
  $("#wizard-panel").classList.add("hidden");
  $("#wizard-picker").classList.remove("hidden");
  renderLearn();
}

function learnedCommands() {
  // Read from the codes table rather than tracked separately, so a code
  // captured outside the wizard also counts as done.
  const group = codeGroups.find((g) => g.subdevice === wizard.group);
  return new Set(group ? group.commands.map((c) => c.command) : []);
}

function renderWizard() {
  if (!wizard) return;
  const { template, group } = wizard;
  const done = learnedCommands();

  $("#wizard-picker").classList.add("hidden");
  $("#wizard-panel").classList.remove("hidden");
  $("#wizard-title").textContent = `${template.icon} ${template.name} · ${group}`;

  const note = $("#wizard-note");
  note.textContent = template.note || "";
  note.classList.toggle("hidden", !template.note);

  const required = template.buttons.filter((b) => !b.optional);
  const doneRequired = required.filter((b) => done.has(b.command)).length;
  $("#wizard-progress").textContent =
    doneRequired === required.length
      ? `Listo: los ${required.length} botones básicos están aprendidos. Podés seguir con los opcionales o salir.`
      : `${doneRequired} de ${required.length} botones básicos. Tocá el que quieras aprender.`;

  // The next unlearned required button is highlighted, so there is always an
  // obvious next step without forcing a fixed order.
  const next = template.buttons.find((b) => !b.optional && !done.has(b.command));

  $("#wizard-buttons").innerHTML = template.buttons
    .map((b) => {
      const isDone = done.has(b.command);
      const isNext = next && b.command === next.command;
      const cls = isDone ? "done" : isNext ? "next" : "";
      return `
      <button type="button" class="wizard-btn ${cls}" data-command="${escapeHtml(
        b.command
      )}" data-label="${escapeHtml(b.label)}"${b.hint ? ` title="${escapeHtml(b.hint)}"` : ""}>
        ${isDone ? "✓" : isNext ? "▸" : ""} ${escapeHtml(b.label)}
        ${b.optional ? '<span class="wizard-optional">opcional</span>' : ""}
      </button>`;
    })
    .join("");
}

async function wizardCapture(command, label) {
  const device = devices.find((d) => d.mac === selectedMac);
  if (!device) return;

  // RF when the device can do it and the frequency is already known, so the
  // second button of a remote does not sweep again; IR otherwise.
  const mode = device.capabilities.learn_rf && knownFrequency[selectedMac] ? "rf" : null;
  if (!mode) {
    const chosen = device.capabilities.learn_rf
      ? window.confirm(
          `Aprender "${label}".\n\nOK = infrarrojo (control de TV, aire...)\nCancelar = radiofrecuencia (portón, luces 433...)`
        )
        ? "ir"
        : "rf"
      : "ir";
    wizard.pending = { command, label };
    await startLearn(chosen);
    return;
  }

  wizard.pending = { command, label };
  await startLearn("rf");
}

function initWizard() {
  $("#template-list").addEventListener("click", (event) => {
    const card = event.target.closest(".template-card");
    if (card) startWizard(card.dataset.id);
  });

  $("#wizard-exit").addEventListener("click", exitWizard);

  $("#wizard-buttons").addEventListener("click", (event) => {
    const button = event.target.closest(".wizard-btn");
    if (button) wizardCapture(button.dataset.command, button.dataset.label);
  });
}

// --- model override ---------------------------------------------------------

let knownModels = null;
let modelTargetMac = null;

async function openModelModal(mac) {
  const device = devices.find((d) => d.mac === mac);
  if (!device) return;
  modelTargetMac = mac;

  // Fetched once: the list is 137 entries and never changes at runtime.
  if (knownModels === null) {
    const res = await fetch(`${API_BASE}api/models`);
    knownModels = res.ok ? await res.json() : [];
  }

  $("#model-current").textContent = device.forced_devtype
    ? `Ahora está forzado a: ${device.model} (código ${device.forced_devtype}).`
    : `Detectado como: ${device.model} (código ${device.devtype}).`;

  $("#model-select").innerHTML = knownModels
    .map((m) => {
      const caps = [m.learn_ir ? "IR" : null, m.learn_rf ? "RF" : null]
        .filter(Boolean)
        .join("+");
      const selected = m.devtype === (device.forced_devtype || device.devtype);
      return `<option value="${m.devtype}"${selected ? " selected" : ""}>${escapeHtml(
        m.manufacturer
      )} ${escapeHtml(m.model)}${caps ? ` · ${caps}` : ""}</option>`;
    })
    .join("");

  $("#model-clear").classList.toggle("hidden", !device.forced_devtype);
  $("#model-error").classList.add("hidden");
  $("#model-modal").classList.remove("hidden");
}

async function applyModel() {
  const devtype = Number($("#model-select").value);
  const res = await fetch(`${API_BASE}api/devices/${encodeURIComponent(modelTargetMac)}/model`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ devtype }),
  });
  if (!res.ok) {
    const el = $("#model-error");
    el.textContent = await extractDetail(res);
    el.classList.remove("hidden");
    return;
  }
  const data = await res.json();
  $("#model-modal").classList.add("hidden");
  // A warning still means the override was stored: the device may just be busy.
  if (data.warning) window.alert(data.warning);
  loadDevices();
}

async function clearModel() {
  const res = await fetch(`${API_BASE}api/devices/${encodeURIComponent(modelTargetMac)}/model`, {
    method: "DELETE",
  });
  if (!res.ok) {
    window.alert(await extractDetail(res));
    return;
  }
  $("#model-modal").classList.add("hidden");
  window.alert("Listo. Buscá de nuevo para que se detecte el modelo real.");
  loadDevices();
}

function initModelOverride() {
  $("#model-apply").addEventListener("click", applyModel);
  $("#model-clear").addEventListener("click", clearModel);
  $("#model-cancel").addEventListener("click", () =>
    $("#model-modal").classList.add("hidden")
  );
}

// --- export / import --------------------------------------------------------

let pendingImport = null;

async function exportCodes() {
  if (!selectedMac) return;
  const res = await fetch(`${API_BASE}api/export/${encodeURIComponent(selectedMac)}`);
  if (!res.ok) {
    window.alert(await extractDetail(res));
    return;
  }
  const data = await res.json();
  if (!Object.keys(data.devices).length) {
    window.alert("Este dispositivo no tiene códigos para exportar.");
    return;
  }

  // Built client-side into a blob: the file never has to come back through the
  // ingress proxy as a download, which is where content-disposition gets lost.
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  const stamp = new Date().toISOString().slice(0, 10);
  link.download = `broadlink-codigos-${stamp}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function onImportFileChosen(event) {
  const file = event.target.files[0];
  event.target.value = ""; // so choosing the same file again still fires
  if (!file || !selectedMac) return;

  let payload;
  try {
    payload = JSON.parse(await file.text());
  } catch (err) {
    window.alert(`El archivo no es un JSON válido: ${err.message}`);
    return;
  }

  const res = await fetch(
    `${API_BASE}api/import/${encodeURIComponent(selectedMac)}/preview`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload }),
    }
  );
  if (!res.ok) {
    window.alert(await extractDetail(res));
    return;
  }

  pendingImport = payload;
  renderImportPreview(await res.json());
  $("#import-modal").classList.remove("hidden");
}

function renderImportPreview(preview) {
  $("#import-error").classList.add("hidden");
  $("#import-summary").innerHTML =
    `<p>El archivo trae <strong>${preview.total}</strong> código${
      preview.total === 1 ? "" : "s"
    }.</p>` +
    preview.groups
      .map(
        (g) => `
      <div class="import-group">
        <span class="import-group-name">${escapeHtml(g.subdevice)}</span>
        ${g.new.length ? `<span class="import-new"> · ${g.new.length} nuevo(s)</span>` : ""}
        ${
          g.conflicting.length
            ? `<span class="import-conflict"> · ${g.conflicting.length} ya existe(n): ${escapeHtml(
                g.conflicting.join(", ")
              )}</span>`
            : ""
        }
      </div>`
      )
      .join("");

  // The mode only matters when something would be overwritten.
  $("#import-conflict-options").classList.toggle("hidden", !preview.conflicts);
}

async function confirmImport() {
  if (!pendingImport) return;
  const button = $("#import-confirm");
  const mode =
    document.querySelector("input[name=import-mode]:checked")?.value || "skip";

  button.disabled = true;
  button.textContent = "Importando…";
  try {
    const res = await fetch(`${API_BASE}api/import/${encodeURIComponent(selectedMac)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload: pendingImport, mode }),
    });
    if (!res.ok) {
      const el = $("#import-error");
      el.textContent = await extractDetail(res);
      el.classList.remove("hidden");
      return;
    }
    const result = await res.json();
    $("#import-modal").classList.add("hidden");
    pendingImport = null;

    const parts = [
      result.added.length ? `${result.added.length} agregado(s)` : null,
      result.overwritten.length ? `${result.overwritten.length} reemplazado(s)` : null,
      result.renamed.length ? `${result.renamed.length} guardado(s) con otro nombre` : null,
      result.skipped.length ? `${result.skipped.length} salteado(s)` : null,
    ].filter(Boolean);
    window.alert(`Importación terminada: ${parts.join(", ") || "sin cambios"}.`);
    loadCodes();
  } finally {
    button.disabled = false;
    button.textContent = "Importar";
  }
}

function initTransfer() {
  $("#btn-export").addEventListener("click", exportCodes);
  $("#btn-import").addEventListener("click", () => $("#import-file").click());
  $("#import-file").addEventListener("change", onImportFileChosen);
  $("#import-confirm").addEventListener("click", confirmImport);
  $("#import-cancel").addEventListener("click", () => {
    $("#import-modal").classList.add("hidden");
    pendingImport = null;
  });
}

// --- entities ---------------------------------------------------------------

let mqttStatus = { status: "unknown", error: null };

async function loadEntities() {
  const list = $("#entity-list");
  if (!selectedMac) {
    list.innerHTML = '<li class="list-empty">Elegí un dispositivo primero.</li>';
    return;
  }

  try {
    const res = await fetch(`${API_BASE}api/entities/${encodeURIComponent(selectedMac)}`);
    if (!res.ok) return;
    const data = await res.json();
    mqttStatus = data.mqtt;
    renderMqttStatus();
    renderEntityList(data.entities);
    fillEntityForm();
  } catch (err) {
    void err;
  }
}

function renderMqttStatus() {
  const el = $("#mqtt-status");
  const ok = mqttStatus.status === "connected";
  el.classList.toggle("error", !ok);
  el.textContent = ok
    ? "MQTT conectado. Las entidades aparecen al instante en Home Assistant."
    : mqttStatus.error ||
      "Sin conexión MQTT. Instalá el add-on Mosquitto para poder crear entidades.";
  // Nothing can be created without a broker, so make that obvious rather than
  // letting the user fill the form and hit an error on submit.
  $("#entity-form").querySelector("button[type=submit]").disabled = !ok;
}

function renderEntityList(list) {
  const el = $("#entity-list");
  if (!list.length) {
    el.innerHTML =
      '<li class="list-empty">Todavía no creaste ninguna entidad para este dispositivo.</li>';
    return;
  }
  el.innerHTML = list
    .map((e) => {
      const detail =
        e.kind === "button"
          ? `${e.subdevice} / ${e.commands.press}`
          : `${e.subdevice} / ${e.commands.on} · ${e.commands.off}`;
      return `
      <li class="entity-row">
        <span class="entity-name">${escapeHtml(e.name)}</span>
        <span class="kind-tag">${e.kind === "button" ? "botón" : "interruptor"}</span>
        <span class="entity-detail">${escapeHtml(detail)}</span>
        <button class="danger del-entity-btn" data-slug="${escapeHtml(e.slug)}">Borrar</button>
      </li>`;
    })
    .join("");
}

function fillEntityForm() {
  const groupSelect = $("#entity-group");
  const previous = groupSelect.value;
  groupSelect.innerHTML = codeGroups
    .map((g) => `<option value="${escapeHtml(g.subdevice)}">${escapeHtml(g.subdevice)}</option>`)
    .join("");
  if (previous) groupSelect.value = previous;
  fillCommandSelects();
}

function fillCommandSelects() {
  const group = codeGroups.find((g) => g.subdevice === $("#entity-group").value);
  const options = (group ? group.commands : [])
    .map((c) => `<option value="${escapeHtml(c.command)}">${escapeHtml(c.command)}</option>`)
    .join("");
  ["#entity-command", "#entity-command-on", "#entity-command-off"].forEach((sel) => {
    $(sel).innerHTML = options;
  });
}

function updateEntityKindFields() {
  const isSwitch = $("#entity-kind").value === "switch";
  $("#entity-cmd-label").classList.toggle("hidden", isSwitch);
  $("#entity-on-label").classList.toggle("hidden", !isSwitch);
  $("#entity-off-label").classList.toggle("hidden", !isSwitch);
}

async function submitEntity(event) {
  event.preventDefault();
  const errorEl = $("#entity-error");
  errorEl.classList.add("hidden");

  const kind = $("#entity-kind").value;
  const body = {
    kind,
    name: $("#entity-name").value,
    subdevice: $("#entity-group").value,
  };
  if (kind === "button") {
    body.command = $("#entity-command").value;
  } else {
    body.command_on = $("#entity-command-on").value;
    body.command_off = $("#entity-command-off").value;
  }

  const res = await fetch(`${API_BASE}api/entities/${encodeURIComponent(selectedMac)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    errorEl.textContent = await extractDetail(res);
    errorEl.classList.remove("hidden");
    return;
  }

  $("#entity-name").value = "";
  loadEntities();
}

async function loadMqttSettings() {
  try {
    const res = await fetch(`${API_BASE}api/mqtt`);
    if (!res.ok) return;
    const data = await res.json();
    const s = data.settings;

    $("#mqtt-host").value = data.source === "manual" ? s.host || "" : "";
    $("#mqtt-host").placeholder =
      data.source === "auto" ? `${s.host} (detectado)` : "(automático)";
    $("#mqtt-port").value = s.port || 1883;
    $("#mqtt-user").value = s.username || "";
    $("#mqtt-ssl").checked = !!s.ssl;
    $("#mqtt-pass").value = "";
    $("#mqtt-pass-hint").textContent = s.has_password
      ? "Ya hay una contraseña guardada. Dejalo vacío para conservarla."
      : "";

    $("#mqtt-source").textContent =
      data.source === "manual"
        ? `Usando la configuración cargada a mano (${s.host}:${s.port}). Borrá el servidor para volver a la automática.`
        : data.source === "auto"
        ? `Detectado automáticamente desde Home Assistant: ${s.host}:${s.port}.`
        : data.detect_error || "No se detectó ningún broker.";
  } catch (err) {
    void err;
  }
}

async function submitMqttSettings(event) {
  event.preventDefault();
  const errorEl = $("#mqtt-error");
  const button = $("#mqtt-save");
  errorEl.classList.add("hidden");
  button.disabled = true;
  button.textContent = "Conectando…";

  try {
    const res = await fetch(`${API_BASE}api/mqtt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host: $("#mqtt-host").value,
        port: Number($("#mqtt-port").value) || 1883,
        username: $("#mqtt-user").value || null,
        password: $("#mqtt-pass").value || null,
        ssl: $("#mqtt-ssl").checked,
      }),
    });
    if (!res.ok) {
      // Keep the form open on failure so the typed settings are not lost.
      errorEl.textContent = await extractDetail(res);
      errorEl.classList.remove("hidden");
      return;
    }
    await loadMqttSettings();
    loadEntities();
  } finally {
    button.disabled = false;
    button.textContent = "Guardar y conectar";
  }
}

function initEntities() {
  $("#mqtt-form").addEventListener("submit", submitMqttSettings);
  $("#entity-kind").addEventListener("change", updateEntityKindFields);
  $("#entity-group").addEventListener("change", fillCommandSelects);
  $("#entity-form").addEventListener("submit", submitEntity);

  $("#entity-list").addEventListener("click", async (event) => {
    const button = event.target.closest(".del-entity-btn");
    if (!button) return;
    if (!window.confirm("¿Borrar esta entidad de Home Assistant?")) return;
    const res = await fetch(
      `${API_BASE}api/entities/${encodeURIComponent(selectedMac)}/${encodeURIComponent(
        button.dataset.slug
      )}`,
      { method: "DELETE" }
    );
    if (!res.ok) window.alert(await extractDetail(res));
    loadEntities();
  });

  updateEntityKindFields();
}

function init() {
  initTabs();
  initDeviceList();
  initCodes();
  initLearn();
  initTransfer();
  initWizard();
  initModelOverride();
  initEntities();
  $("#btn-scan").addEventListener("click", runScan);
  $("#add-device-form").addEventListener("submit", submitAddDevice);

  loadDevices();
  setInterval(loadDevices, POLL_MS);
}

document.addEventListener("DOMContentLoaded", init);
