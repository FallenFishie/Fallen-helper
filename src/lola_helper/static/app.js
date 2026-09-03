"use strict";

const $ = (selector) => document.querySelector(selector);
const elements = {
  body: document.body,
  conversation: $("#conversation"),
  welcome: $("#welcome"),
  messages: $("#messages"),
  thinking: $("#thinking"),
  composer: $("#composer"),
  input: $("#message-input"),
  send: $("#send-button"),
  mic: $("#mic-button"),
  model: $("#model-select"),
  status: $("#status-pill"),
  ollamaReadout: $("#ollama-readout"),
  commandReadout: $("#command-readout"),
  taskList: $("#task-list"),
  taskCount: $("#task-count"),
  newThread: $("#new-thread"),
  clearThread: $("#clear-thread"),
  voiceToggle: $("#voice-toggle"),
  mobileMenu: $("#mobile-menu"),
  sidebarScrim: $("#sidebar-scrim"),
  approvalDialog: $("#approval-dialog"),
  approvalTitle: $("#approval-title"),
  approvalDescription: $("#approval-description"),
  approvalArguments: $("#approval-arguments"),
  approveAction: $("#approve-action"),
  denyAction: $("#deny-action"),
  toastRegion: $("#toast-region"),
  greeting: $("#greeting"),
};

const makeId = () => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const state = {
  sessionId: sessionStorage.getItem("lola-session") || makeId(),
  busy: false,
  pendingApproval: null,
  speechEnabled: localStorage.getItem("lola-speech") === "true",
  recognition: null,
  listening: false,
  hasMessages: false,
};
sessionStorage.setItem("lola-session", state.sessionId);

function friendlyTime() {
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(
    new Date(),
  );
}

function setGreeting() {
  const hour = new Date().getHours();
  const period = hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
  elements.greeting.textContent = `Good ${period}.`;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body) headers.set("Content-Type", "application/json");
  if ((options.method || "GET") !== "GET") {
    headers.set("X-Lola-Client", "control-panel");
  }
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch (_error) {
    throw new Error("The Lola service is unreachable. Is it still running?");
  }
  if (response.status === 204) return null;
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = {};
  }
  if (!response.ok) {
    let message = payload.detail || `Request failed (${response.status})`;
    if (Array.isArray(message)) message = message.map((item) => item.msg).join("; ");
    throw new Error(String(message));
  }
  return payload;
}

function setBusy(busy) {
  state.busy = busy;
  elements.send.disabled = busy;
  elements.mic.disabled = busy;
  elements.input.disabled = busy;
  elements.model.disabled = busy;
  elements.thinking.classList.toggle("hidden", !busy);
  if (busy) scrollToBottom();
}

function hideWelcome() {
  state.hasMessages = true;
  elements.welcome.classList.add("hidden");
}

function appendMessage(role, text, actions = [], isError = false) {
  hideWelcome();
  const article = document.createElement("article");
  article.className = `message ${role}${isError ? " error" : ""}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = role === "user" ? "YOU" : isError ? "!" : "F";
  avatar.setAttribute("aria-hidden", "true");

  const body = document.createElement("div");
  body.className = "message-body";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const name = document.createElement("span");
  name.textContent = role === "user" ? "You" : isError ? "System notice" : "Lola";
  const time = document.createElement("time");
  time.textContent = friendlyTime();
  meta.append(name, time);

  const messageText = document.createElement("div");
  messageText.className = "message-text";
  messageText.textContent = text;
  body.append(meta, messageText);

  if (actions.length) {
    const trace = document.createElement("div");
    trace.className = "action-trace";
    for (const action of actions) {
      const chip = document.createElement("span");
      chip.className = `action-chip ${action.status || "complete"}`;
      chip.textContent = action.summary || action.tool || "Action complete";
      chip.title = action.tool || "Computer action";
      trace.append(chip);
    }
    body.append(trace);
  }

  article.append(avatar, body);
  elements.messages.append(article);
  scrollToBottom();
  return article;
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    elements.conversation.scrollTop = elements.conversation.scrollHeight;
  });
}

function resizeInput() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 130)}px`;
}

async function sendMessage(forcedText = null) {
  const text = (forcedText ?? elements.input.value).trim();
  if (!text || state.busy || state.pendingApproval) return;
  appendMessage("user", text);
  elements.input.value = "";
  resizeInput();
  setBusy(true);
  try {
    const result = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        message: text,
        model: elements.model.value || null,
      }),
    });
    await handleAgentResult(result);
  } catch (error) {
    appendMessage("assistant", error.message, [], true);
    if (error.message.toLowerCase().includes("ollama")) {
      toast("Start Ollama and install the selected model, then try again.", "error", 6500);
    }
  } finally {
    if (!state.pendingApproval) setBusy(false);
  }
}

async function handleAgentResult(result) {
  if (result.type === "approval_required") {
    setBusy(false);
    showApproval(result.approval);
    return;
  }
  if (result.type === "message") {
    appendMessage("assistant", result.message || "Done.", result.actions || []);
    if (state.speechEnabled) speak(result.message || "Done.");
    await loadTasks();
    return;
  }
  throw new Error("The assistant returned an unexpected response.");
}

function showApproval(approval) {
  state.pendingApproval = approval;
  const labels = {
    run_command: "Run this command?",
    write_text_file: "Write this file?",
  };
  elements.approvalTitle.textContent = labels[approval.tool] || "Allow this action?";
  elements.approvalDescription.textContent = approval.description || approval.tool;
  elements.approvalArguments.textContent = JSON.stringify(approval.arguments || {}, null, 2);
  if (typeof elements.approvalDialog.showModal === "function") {
    elements.approvalDialog.showModal();
  } else {
    toast("This browser cannot display the approval panel.", "error");
  }
}

async function resolveApproval(approved) {
  const approval = state.pendingApproval;
  if (!approval) return;
  state.pendingApproval = null;
  if (elements.approvalDialog.open) elements.approvalDialog.close();
  setBusy(true);
  try {
    const result = await api("/api/approval", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        approval_id: approval.id,
        approved,
      }),
    });
    await handleAgentResult(result);
  } catch (error) {
    appendMessage("assistant", error.message, [], true);
  } finally {
    if (!state.pendingApproval) setBusy(false);
  }
}

function toast(message, type = "info", duration = 4200) {
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.textContent = message;
  elements.toastRegion.append(item);
  window.setTimeout(() => {
    item.style.opacity = "0";
    window.setTimeout(() => item.remove(), 250);
  }, duration);
}

function option(value, label = value) {
  const item = document.createElement("option");
  item.value = value;
  item.textContent = label;
  return item;
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    const connected = Boolean(health.ollama?.connected);
    elements.status.className = `status-pill ${connected ? "online" : "offline"}`;
    elements.status.querySelector("span").textContent = connected ? "Online" : "Offline";
    elements.ollamaReadout.textContent = connected
      ? health.ollama.version
        ? `LINKED · V${health.ollama.version}`
        : "LINKED"
      : "NOT FOUND";
    elements.ollamaReadout.classList.toggle("good", connected);
    elements.commandReadout.textContent = health.commands_enabled ? "APPROVAL ON" : "DISABLED";
    elements.commandReadout.classList.toggle("good", Boolean(health.commands_enabled));
    updateModels(health.models || [], health.default_model);
  } catch (_error) {
    elements.status.className = "status-pill offline";
    elements.status.querySelector("span").textContent = "Service error";
    elements.ollamaReadout.textContent = "UNREACHABLE";
  }
}

function updateModels(models, defaultModel) {
  const saved = localStorage.getItem("lola-model");
  const current = elements.model.value;
  const preferred = saved || (current && !current.includes("Loading") ? current : defaultModel);
  const names = [...new Set(models.filter(Boolean))];
  if (defaultModel && !names.includes(defaultModel)) names.unshift(defaultModel);
  if (preferred && !names.includes(preferred)) names.unshift(preferred);
  elements.model.replaceChildren();
  if (!names.length) {
    elements.model.append(option(defaultModel || "qwen3:4b", defaultModel || "qwen3:4b"));
  } else {
    for (const name of names) {
      const installed = models.includes(name);
      const label = installed ? name : `${name} · not installed`;
      elements.model.append(option(name, label));
    }
  }
  elements.model.value = preferred || defaultModel || names[0];
}

async function loadTasks() {
  try {
    const result = await api("/api/tasks");
    const tasks = result.tasks || [];
    elements.taskCount.textContent = String(tasks.length);
    elements.taskList.replaceChildren();
    if (!tasks.length) {
      const empty = document.createElement("p");
      empty.className = "empty-small";
      empty.textContent = "Ask Lola to remember something.";
      elements.taskList.append(empty);
      return;
    }
    for (const task of tasks.slice(0, 8)) {
      const item = document.createElement("div");
      item.className = "task-item";
      const check = document.createElement("button");
      check.type = "button";
      check.className = "task-check";
      check.setAttribute("aria-label", `Complete task: ${task.title}`);
      check.title = "Mark complete";
      check.addEventListener("click", () => completeTask(task.id, task.title, check));
      const copy = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = task.title;
      copy.append(title);
      if (task.due) {
        const due = document.createElement("small");
        due.textContent = task.due;
        copy.append(due);
      }
      item.append(check, copy);
      elements.taskList.append(item);
    }
  } catch (_error) {
    // The task list is secondary to chat; leave the previous state in place.
  }
}

async function completeTask(taskId, title, button) {
  button.disabled = true;
  try {
    await api(`/api/tasks/${encodeURIComponent(taskId)}/complete`, { method: "POST" });
    toast(`Completed: ${title}`);
    await loadTasks();
  } catch (error) {
    button.disabled = false;
    toast(error.message, "error");
  }
}

async function clearThread() {
  if (state.busy) return;
  try {
    await api("/api/reset", {
      method: "POST",
      body: JSON.stringify({ session_id: state.sessionId }),
    });
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  state.sessionId = makeId();
  sessionStorage.setItem("lola-session", state.sessionId);
  state.pendingApproval = null;
  state.hasMessages = false;
  elements.messages.replaceChildren();
  elements.welcome.classList.remove("hidden");
  globalThis.speechSynthesis?.cancel();
  closeSidebar();
  elements.input.focus();
  toast("Started a fresh private thread.");
}

function updateVoiceToggle() {
  elements.voiceToggle.setAttribute("aria-pressed", String(state.speechEnabled));
  elements.voiceToggle.querySelector("span").textContent = state.speechEnabled
    ? "Voice on"
    : "Voice off";
}

function speak(text) {
  if (!("speechSynthesis" in window) || !text) return;
  window.speechSynthesis.cancel();
  const clean = text
    .replace(/```[\s\S]*?```/g, " code omitted ")
    .replace(/[*_`#>]/g, " ")
    .slice(0, 4000);
  const utterance = new SpeechSynthesisUtterance(clean);
  utterance.rate = 0.97;
  utterance.pitch = 0.92;
  const voices = window.speechSynthesis.getVoices();
  const preferred = voices.find(
    (voice) => voice.lang.startsWith(navigator.language.split("-")[0]) && voice.localService,
  );
  if (preferred) utterance.voice = preferred;
  window.speechSynthesis.speak(utterance);
}

function setupRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    elements.mic.title = "Speech recognition is not supported by this browser";
    elements.mic.addEventListener("click", () =>
      toast("Voice input is available in browsers that support Web Speech Recognition.", "error"),
    );
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = navigator.language || "en-US";
  recognition.interimResults = true;
  recognition.continuous = false;
  let finalTranscript = "";

  recognition.addEventListener("start", () => {
    finalTranscript = "";
    state.listening = true;
    elements.mic.classList.add("listening");
    elements.input.placeholder = "Listening…";
    if (!localStorage.getItem("lola-voice-notice")) {
      toast("Voice recognition is provided by your browser and may use its online speech service.");
      localStorage.setItem("lola-voice-notice", "shown");
    }
  });
  recognition.addEventListener("result", (event) => {
    let interim = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const transcript = event.results[index][0].transcript;
      if (event.results[index].isFinal) finalTranscript += transcript;
      else interim += transcript;
    }
    elements.input.value = finalTranscript || interim;
    resizeInput();
  });
  recognition.addEventListener("error", (event) => {
    if (event.error !== "no-speech" && event.error !== "aborted") {
      toast(`Microphone error: ${event.error}`, "error");
    }
  });
  recognition.addEventListener("end", () => {
    state.listening = false;
    elements.mic.classList.remove("listening");
    elements.input.placeholder = "Ask Lola to think or act…";
    const spoken = finalTranscript.trim();
    if (spoken && !state.busy) sendMessage(spoken);
  });
  state.recognition = recognition;
  elements.mic.addEventListener("click", () => {
    if (state.listening) recognition.stop();
    else if (!state.busy) recognition.start();
  });
}

function openSidebar() {
  elements.body.classList.add("sidebar-open");
}

function closeSidebar() {
  elements.body.classList.remove("sidebar-open");
}

elements.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});
elements.input.addEventListener("input", resizeInput);
elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendMessage();
  }
});
elements.model.addEventListener("change", () => {
  localStorage.setItem("lola-model", elements.model.value);
  toast(`Reasoning engine set to ${elements.model.value}.`);
});
elements.newThread.addEventListener("click", clearThread);
elements.clearThread.addEventListener("click", clearThread);
elements.voiceToggle.addEventListener("click", () => {
  state.speechEnabled = !state.speechEnabled;
  localStorage.setItem("lola-speech", String(state.speechEnabled));
  if (!state.speechEnabled) globalThis.speechSynthesis?.cancel();
  updateVoiceToggle();
});
elements.mobileMenu.addEventListener("click", openSidebar);
elements.sidebarScrim.addEventListener("click", closeSidebar);
elements.approveAction.addEventListener("click", (event) => {
  event.preventDefault();
  resolveApproval(true);
});
elements.denyAction.addEventListener("click", (event) => {
  event.preventDefault();
  resolveApproval(false);
});
elements.approvalDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
});
elements.approvalDialog.querySelector("form").addEventListener("submit", (event) => {
  event.preventDefault();
});
for (const suggestion of document.querySelectorAll("[data-prompt]")) {
  suggestion.addEventListener("click", () => sendMessage(suggestion.dataset.prompt));
}
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "n") {
    event.preventDefault();
    clearThread();
  }
  if (event.key === "Escape") closeSidebar();
});

setGreeting();
updateVoiceToggle();
setupRecognition();
loadHealth();
loadTasks();
window.setInterval(loadHealth, 30000);
elements.input.focus();
