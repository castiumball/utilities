// ==========================================
// Constants & DOM References
// ==========================================
const input = document.getElementById("input");
const inputBox = document.querySelector(".input-box");
const sendBtn = document.getElementById("send-btn");
const charCount = document.getElementById("char-count");
const limitError = document.getElementById("limit-error");
const inputMirror = document.getElementById("input-mirror");
const undoTip = document.getElementById("undo-tip");
const textareaWrapper = document.querySelector(".textarea-wrapper");
const scrollTrack = document.querySelector(".custom-scrollbar-track");
const scrollThumb = document.querySelector(".custom-scrollbar-thumb");
const messages = document.getElementById("messages");
const sidebar = document.getElementById("sidebar");
const sidebarOverlay = document.getElementById("sidebar-overlay");
const conversationList = document.getElementById("conversation-list");

const docPanel = document.getElementById("doc-panel");
const docPanelOverlay = document.getElementById("doc-panel-overlay");
const docPanelList = document.getElementById("doc-panel-list");

const MAX_CHARS = 1000;

// Auto-detect URL prefix for reverse proxy support
// e.g. "/polaris_v1/chat/" → prefix="/polaris_v1", locally "/chat/" → prefix=""
const PATH_PREFIX = window.location.pathname.replace(/\/chat\/?$/, "");
const API_BASE = `${PATH_PREFIX}/api/chat`;

// Per-browser user identity — isolates conversations between users
function getUserId() {
    let id = localStorage.getItem("polaris_user_id");
    if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem("polaris_user_id", id);
    }
    return id;
}
const USER_ID = getUserId();

// Configure marked for markdown rendering
marked.setOptions({
    highlight: function (code, lang) {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
    },
    breaks: true,
    gfm: true,
});
const isFirefox = navigator.userAgent.includes("Firefox");

let limitErrorTimeout = null;
let isShaking = false;
let undoTipShown = false;
let isDragging = false;
let dragStartY = 0;
let dragStartScroll = 0;
let currentConversationId = null;
let cachedDocuments = null;

// ==========================================
// API Helper
// ==========================================

async function apiCall(method, path, body = null) {
    const options = {
        method,
        headers: {
            "Content-Type": "application/json",
            "X-User-ID": USER_ID,
        },
    };
    if (body) {
        options.body = JSON.stringify(body);
    }
    const response = await fetch(`${API_BASE}${path}`, options);
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || `Request failed: ${response.status}`);
    }
    return response.json();
}

// ==========================================
// Input Functions
// ==========================================

function updateSendBtn() {
    sendBtn.disabled = input.value.trim().length === 0;
}

function updateCharCount() {
    const len = input.value.length;
    charCount.textContent = `${len} / ${MAX_CHARS}`;
    charCount.classList.remove("near-limit", "at-limit");
    if (len >= MAX_CHARS) {
        charCount.classList.add("at-limit");
        inputBox.classList.add("at-limit");
    } else {
        inputBox.classList.remove("at-limit");
        limitError.classList.remove("visible");
        if (len >= MAX_CHARS * 0.9) {
            charCount.classList.add("near-limit");
        }
    }
}

function showLimitError() {
    if (!isShaking) {
        isShaking = true;
        inputBox.classList.remove("at-limit");
        void inputBox.offsetWidth;
        inputBox.classList.add("at-limit");
        setTimeout(() => { isShaking = false; }, 400);
    }
    limitError.classList.add("visible");
    clearTimeout(limitErrorTimeout);
    limitErrorTimeout = setTimeout(() => {
        limitError.classList.remove("visible");
    }, 2000);
}

function resizeTextarea() {
    inputMirror.textContent = (input.value || "\u200b") + "\n";
    updateScrollbar();
}

function updateScrollbar() {
    const { scrollHeight, clientHeight, scrollTop } = input;
    const isScrollable = scrollHeight > clientHeight + 2;

    if (isScrollable) {
        textareaWrapper.classList.add("scrollable");
        const trackHeight = scrollTrack.clientHeight;
        const thumbHeight = Math.max(30, (clientHeight / scrollHeight) * trackHeight);
        const thumbTop = (scrollTop / (scrollHeight - clientHeight)) * (trackHeight - thumbHeight);
        scrollThumb.style.height = `${thumbHeight}px`;
        scrollThumb.style.top = `${thumbTop}px`;
    } else {
        textareaWrapper.classList.remove("scrollable");
    }
}

// ==========================================
// Message Functions
// ==========================================

function appendMessage(text, role) {
    hideEmptyState();
    const el = document.createElement("div");
    el.className = role === "user" ? "message-user" : "message-bot";
    if (role === "bot") {
        el.innerHTML = renderMarkdown(text);
    } else {
        el.textContent = text;
    }
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
}

function renderMarkdown(text) {
    return marked.parse(text);
}

function clearMessages() {
    messages.innerHTML = "";
    showEmptyState();
}

function showEmptyState() {
    // Only show if messages area is empty
    if (messages.children.length > 0) return;
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
        <svg class="empty-state-icon" width="44" height="44" viewBox="0 0 24 24" fill="none">
            <path d="M12 2L14.9 8.6L22 9.3L16.8 14L18.2 21L12 17.5L5.8 21L7.2 14L2 9.3L9.1 8.6L12 2Z" fill="#E8B84B"/>
        </svg>
        <span class="empty-state-text">Hello, I'm <span class="gradient-text">Polaris</span></span>
    `;
    messages.appendChild(empty);
}

function hideEmptyState() {
    const empty = messages.querySelector(".empty-state");
    if (empty) empty.remove();
}

function showStatusIndicator(text) {
    let indicator = document.getElementById("status-indicator");
    if (!text) {
        if (indicator) indicator.remove();
        return;
    }
    if (!indicator) {
        indicator = document.createElement("div");
        indicator.id = "status-indicator";
        indicator.className = "status-indicator";
        messages.appendChild(indicator);
    }
    indicator.textContent = text;
    messages.scrollTop = messages.scrollHeight;
}

function clearAndSend() {
    const message = input.value.trim();
    if (!message) return;

    input.value = "";
    resizeTextarea();
    updateCharCount();
    updateSendBtn();

    appendMessage(message, "user");
    sendMessage(message);
}

async function sendMessage(message) {
    // Ensure we have a conversation
    if (!currentConversationId) {
        await createNewChat();
    }

    // Disable input during streaming
    sendBtn.disabled = true;
    input.disabled = true;

    // Create empty bot message element for streaming
    hideEmptyState();

    // Reasoning panel (created lazily on first reasoning_step event)
    let reasoningPanel = null;
    let reasoningBody = null;
    let reasoningStepCount = 0;

    function ensureReasoningPanel() {
        if (reasoningPanel) return;
        reasoningPanel = document.createElement("details");
        reasoningPanel.className = "reasoning-panel";
        reasoningPanel.open = true;
        const summary = document.createElement("summary");
        summary.textContent = "Reasoning\u2026";
        reasoningPanel.appendChild(summary);
        reasoningBody = document.createElement("div");
        reasoningBody.className = "reasoning-body";
        reasoningPanel.appendChild(reasoningBody);
        messages.appendChild(reasoningPanel);
    }

    function addReasoningStep(label, detail, description) {
        ensureReasoningPanel();
        reasoningStepCount++;
        const entry = document.createElement("div");
        entry.className = "reasoning-entry";
        const header = document.createElement("div");
        header.className = "reasoning-entry-header";
        const labelSpan = document.createElement("span");
        labelSpan.className = "reasoning-label";
        labelSpan.textContent = label;
        header.appendChild(labelSpan);
        if (description) {
            const descSpan = document.createElement("span");
            descSpan.className = "reasoning-description";
            descSpan.textContent = description;
            header.appendChild(descSpan);
        }
        entry.appendChild(header);
        const detailSpan = document.createElement("div");
        detailSpan.className = "reasoning-detail";
        detailSpan.textContent = detail;
        entry.appendChild(detailSpan);
        reasoningBody.appendChild(entry);
        messages.scrollTop = messages.scrollHeight;
    }

    function collapseReasoningPanel() {
        if (!reasoningPanel) return;
        reasoningPanel.open = false;
        const summary = reasoningPanel.querySelector("summary");
        summary.textContent = `Reasoning (${reasoningStepCount} step${reasoningStepCount !== 1 ? "s" : ""})`;
    }

    const botEl = document.createElement("div");
    botEl.className = "message-bot";
    botEl.textContent = "";
    messages.appendChild(botEl);

    let rawText = "";
    let renderPending = false;

    function scheduleRender() {
        if (renderPending) return;
        renderPending = true;
        requestAnimationFrame(() => {
            botEl.innerHTML = renderMarkdown(rawText);
            messages.scrollTop = messages.scrollHeight;
            renderPending = false;
        });
    }

    try {
        const response = await fetch(
            `${API_BASE}/conversations/${currentConversationId}/completions`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-User-ID": USER_ID,
                },
                body: JSON.stringify({ message }),
            }
        );

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            botEl.textContent = err.error || "Failed to get response.";
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop(); // keep incomplete line in buffer

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const payload = line.slice(6).trim();
                if (payload === "[DONE]") {
                    // Re-enable input immediately — summarization may continue in background
                    botEl.innerHTML = renderMarkdown(rawText);
                    collapseReasoningPanel();
                    input.disabled = false;
                    sendBtn.disabled = false;
                    input.focus();
                    loadConversations();
                    continue;
                }
                try {
                    const data = JSON.parse(payload);
                    if (data.queue_position !== undefined) {
                        if (data.queue_position > 0) {
                            const plural = data.queue_position === 1 ? "request" : "requests";
                            showStatusIndicator(`In queue \u2014 ${data.queue_position} ${plural} ahead of you`);
                        } else {
                            showStatusIndicator("");
                        }
                    } else if (data.error) {
                        rawText += data.error;
                        scheduleRender();
                    } else if (data.token) {
                        rawText += data.token;
                        scheduleRender();
                    } else if (data.reasoning_step) {
                        addReasoningStep(data.reasoning_step.label, data.reasoning_step.detail, data.reasoning_step.description);
                    } else if (data.title) {
                        // LLM-generated title for first exchange
                        updateSidebarTitle(currentConversationId, data.title);
                    } else if ("status" in data) {
                        showStatusIndicator(data.status);
                    }
                } catch (e) {
                    // skip unparseable lines
                }
            }
        }
    } catch (err) {
        botEl.textContent = "Connection error. Is the server running?";
        console.error("Stream error:", err);
    } finally {
        // Ensure input is re-enabled even on errors
        input.disabled = false;
        sendBtn.disabled = false;
    }
}

function updateSidebarTitle(conversationId, title) {
    const item = conversationList.querySelector(
        `.conversation-item[data-id="${conversationId}"]`
    );
    const titleEl = item?.querySelector(".conversation-title");
    if (titleEl) {
        titleEl.textContent = title;
        titleEl.title = title;
    }
}

// ==========================================
// Sidebar Functions
// ==========================================

function toggleSidebar() {
    const isCollapsed = sidebar.classList.toggle("collapsed");
    if (isCollapsed) {
        sidebarOverlay.classList.remove("visible");
    } else if (window.innerWidth <= 768) {
        sidebarOverlay.classList.add("visible");
    }
}

function closeSidebarOnMobile() {
    if (window.innerWidth <= 768) {
        sidebar.classList.add("collapsed");
        sidebarOverlay.classList.remove("visible");
    }
}

// ==========================================
// Conversation Functions
// ==========================================

async function loadConversations() {
    const data = await apiCall("GET", "/conversations");
    renderConversationList(data.conversations);
}

function renderConversationList(conversations) {
    conversationList.innerHTML = "";
    for (const conv of conversations) {
        conversationList.appendChild(renderConversationItem(conv));
    }
}

function renderConversationItem(conv) {
    const item = document.createElement("div");
    item.className = "conversation-item";
    item.dataset.id = conv.id;
    if (conv.id === currentConversationId) {
        item.classList.add("active");
    }

    const icon = document.createElement("span");
    icon.className = "conversation-icon";
    icon.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M2 3C2 2.44772 2.44772 2 3 2H13C13.5523 2 14 2.44772 14 3V10C14 10.5523 13.5523 11 13 11H5L2 14V3Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;

    const title = document.createElement("span");
    title.className = "conversation-title";
    title.textContent = conv.title;
    title.title = conv.title;

    const time = document.createElement("span");
    time.className = "conversation-time";
    time.dataset.time = conv.updated_at;
    time.textContent = formatRelativeTime(conv.updated_at);

    const actions = document.createElement("div");
    actions.className = "conversation-actions";

    const renameBtn = document.createElement("button");
    renameBtn.setAttribute("aria-label", "Rename");
    renameBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="none">
        <path d="M11.5 1.5L14.5 4.5L5 14H2V11L11.5 1.5Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
    renameBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        startRename(item, conv);
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.setAttribute("aria-label", "Delete");
    deleteBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 16 16" fill="none">
        <path d="M2 4H14M5 4V2H11V4M6 7V12M10 7V12M3 4L4 14H12L13 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
    deleteBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteConversation(conv.id);
    });

    actions.appendChild(renameBtn);
    actions.appendChild(deleteBtn);

    item.appendChild(icon);
    item.appendChild(title);
    item.appendChild(time);
    item.appendChild(actions);

    item.addEventListener("click", () => switchConversation(conv.id));

    return item;
}

function formatRelativeTime(isoString) {
    // Server returns UTC timestamps without timezone suffix — append Z
    const date = new Date(isoString.replace(" ", "T") + "Z");
    const now = new Date();
    const diffMs = now - date;
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffMs / 60000);
    const diffHr = Math.floor(diffMs / 3600000);
    const diffDay = Math.floor(diffMs / 86400000);

    if (diffSec < 5) return "now";
    if (diffSec < 60) return `${diffSec}s`;
    if (diffMin < 60) return `${diffMin}m`;
    if (diffHr < 24) return `${diffHr}h`;
    if (diffDay < 7) return `${diffDay}d`;
    return date.toLocaleDateString();
}

function updateTimestamps() {
    document.querySelectorAll(".conversation-item").forEach(item => {
        const timeEl = item.querySelector(".conversation-time");
        if (timeEl && timeEl.dataset.time) {
            timeEl.textContent = formatRelativeTime(timeEl.dataset.time);
        }
    });
}

async function createNewChat() {
    const data = await apiCall("POST", "/conversations");
    currentConversationId = data.conversation.id;
    clearMessages();
    await loadConversations();
    closeSidebarOnMobile();
    input.focus();
}

async function switchConversation(id) {
    if (id === currentConversationId) {
        closeSidebarOnMobile();
        return;
    }

    currentConversationId = id;
    clearMessages();

    const data = await apiCall("GET", `/conversations/${id}`);
    for (const msg of data.messages) {
        appendMessage(msg.content, msg.role);
    }

    // Update active highlight
    for (const item of conversationList.children) {
        item.classList.toggle("active", item.dataset.id === id);
    }

    closeSidebarOnMobile();
    input.focus();
}

function startRename(item, conv) {
    const titleEl = item.querySelector(".conversation-title");
    const timeEl = item.querySelector(".conversation-time");
    const actionsEl = item.querySelector(".conversation-actions");

    // Hide time and actions during rename
    timeEl.style.display = "none";
    actionsEl.style.display = "none";

    // Replace title with input
    const renameInput = document.createElement("input");
    renameInput.type = "text";
    renameInput.className = "rename-input";
    renameInput.value = conv.title;
    titleEl.replaceWith(renameInput);
    renameInput.focus();
    renameInput.select();

    let finished = false;
    const finishRename = async (save) => {
        if (finished) return;
        finished = true;

        const newTitle = renameInput.value.trim();
        if (save && newTitle && newTitle !== conv.title) {
            await apiCall("PATCH", `/conversations/${conv.id}`, { title: newTitle });
            conv.title = newTitle;
        }
        // Restore elements
        const newTitleEl = document.createElement("span");
        newTitleEl.className = "conversation-title";
        newTitleEl.textContent = conv.title;
        newTitleEl.title = conv.title;
        renameInput.replaceWith(newTitleEl);
        timeEl.style.display = "";
        actionsEl.style.display = "";
    };

    renameInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            finishRename(true);
        } else if (e.key === "Escape") {
            finishRename(false);
        }
    });

    renameInput.addEventListener("blur", () => finishRename(true));
}

async function deleteConversation(id) {
    if (!confirm("Delete this conversation?")) return;

    await apiCall("DELETE", `/conversations/${id}`);

    if (id === currentConversationId) {
        currentConversationId = null;
        clearMessages();
        const data = await apiCall("GET", "/conversations");
        if (data.conversations.length > 0) {
            await switchConversation(data.conversations[0].id);
        } else {
            await createNewChat();
        }
    }

    loadConversations();
}

// ==========================================
// Document Knowledge Panel
// ==========================================

function toggleDocPanel() {
    const isCollapsed = docPanel.classList.toggle("collapsed");
    if (window.innerWidth <= 768) {
        if (!isCollapsed) {
            docPanelOverlay.classList.add("visible");
        } else {
            docPanelOverlay.classList.remove("visible");
        }
    }
}

function closeDocPanel() {
    docPanel.classList.add("collapsed");
    docPanelOverlay.classList.remove("visible");
}

async function loadDocuments() {
    try {
        const response = await fetch(`${PATH_PREFIX}/api/documents`);
        if (!response.ok) throw new Error("Failed to fetch documents");
        const data = await response.json();
        cachedDocuments = data.documents;
        renderDocumentList(cachedDocuments);
    } catch (err) {
        docPanelList.innerHTML = `
            <div class="doc-panel-empty">
                <span class="doc-panel-empty-text">Could not load documents</span>
            </div>`;
    }
}

const STATUS_STAGES = ["uploaded", "preprocessed", "parsed", "entity_extracted", "graph_staged", "graph_ready"];

function getProgressColor(progress) {
    if (progress >= 100) return "progress-complete";
    if (progress >= 50) return "progress-mid";
    return "progress-low";
}

function renderDocumentList(docs) {
    docPanelList.innerHTML = "";
    if (!docs || docs.length === 0) {
        docPanelList.innerHTML = `
            <div class="doc-panel-empty">
                <span class="doc-panel-empty-text">No documents ingested yet</span>
            </div>`;
        return;
    }

    const ready = docs.filter(d => d.status === "graph_ready");
    const processing = docs.filter(d => d.status !== "graph_ready");

    if (ready.length > 0) {
        ready.forEach(doc => docPanelList.appendChild(renderDocumentItem(doc, false)));
    } else {
        const emptyMsg = document.createElement("div");
        emptyMsg.className = "doc-panel-empty-inline";
        emptyMsg.textContent = "No documents ready yet";
        docPanelList.appendChild(emptyMsg);
    }

    if (processing.length > 0) {
        const divider = document.createElement("div");
        divider.className = "sidebar-divider";
        docPanelList.appendChild(divider);

        const labelRow = document.createElement("div");
        labelRow.className = "sidebar-section-label-row";

        const label = document.createElement("div");
        label.className = "sidebar-section-label";
        label.textContent = "Processing";
        labelRow.appendChild(label);

        const hideUploaded = localStorage.getItem("hideUploadedDocs") === "true";
        const toggleBtn = document.createElement("button");
        toggleBtn.className = "hide-uploaded-toggle";
        toggleBtn.textContent = hideUploaded ? "Show uploaded" : "Hide uploaded";
        toggleBtn.addEventListener("click", () => {
            const current = localStorage.getItem("hideUploadedDocs") === "true";
            localStorage.setItem("hideUploadedDocs", !current);
            renderDocumentList(cachedDocuments);
        });
        labelRow.appendChild(toggleBtn);
        docPanelList.appendChild(labelRow);

        const filtered = hideUploaded
            ? processing.filter(d => d.status !== "uploaded")
            : processing;
        filtered.forEach(doc => docPanelList.appendChild(renderDocumentItem(doc, true)));
    }
}

function renderDocumentItem(doc, showStage) {
    const item = document.createElement("div");
    item.className = "doc-item";

    const header = document.createElement("div");
    header.className = "doc-item-header";

    const icon = document.createElement("span");
    icon.className = "doc-item-icon";
    icon.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M3 1.5H10L13 4.5V14C13 14.2761 12.7761 14.5 12.5 14.5H3C2.72386 14.5 2.5 14.2761 2.5 14V2C2.5 1.72386 2.72386 1.5 3 1.5Z" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M5.5 8H10.5M5.5 10.5H9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
    </svg>`;

    const name = document.createElement("span");
    name.className = "doc-item-name";
    name.textContent = doc.name;
    name.title = doc.name;

    header.appendChild(icon);
    header.appendChild(name);

    if (showStage) {
        const progress = doc.progress || 0;
        const badge = document.createElement("span");
        badge.className = "doc-status-badge " + getProgressColor(progress);
        badge.textContent = `${progress}%`;
        header.appendChild(badge);
    }

    const details = document.createElement("div");
    details.className = "doc-item-details";
    details.innerHTML = `
        <div class="doc-item-details-inner">
            <div class="doc-detail-row">
                <span class="doc-detail-label">Size</span>
                <span class="doc-detail-value">${formatFileSize(doc.size)}</span>
            </div>
            <div class="doc-detail-row">
                <span class="doc-detail-label">Uploaded</span>
                <span class="doc-detail-value">${formatDocDate(doc.upload_date)}</span>
            </div>
            ${doc.parsed_at ? `<div class="doc-detail-row">
                <span class="doc-detail-label">Parsed</span>
                <span class="doc-detail-value">${formatDocDate(doc.parsed_at)}</span>
            </div>` : ""}
            ${doc.entity_extracted_at ? `<div class="doc-detail-row">
                <span class="doc-detail-label">Entities extracted</span>
                <span class="doc-detail-value">${formatDocDate(doc.entity_extracted_at)}</span>
            </div>` : ""}
            ${doc.graph_staged_at ? `<div class="doc-detail-row">
                <span class="doc-detail-label">Staged for testing</span>
                <span class="doc-detail-value">${formatDocDate(doc.graph_staged_at)}</span>
            </div>` : ""}
            ${doc.graph_ready_at ? `<div class="doc-detail-row">
                <span class="doc-detail-label">Production ready</span>
                <span class="doc-detail-value">${formatDocDate(doc.graph_ready_at)}</span>
            </div>` : ""}
            ${doc.pinned ? `<div class="doc-detail-row">
                <span class="doc-detail-label">Pinned</span>
                <span class="doc-detail-value">Yes</span>
            </div>` : ""}
        </div>`;

    item.appendChild(header);
    item.appendChild(details);

    header.addEventListener("click", () => {
        item.classList.toggle("expanded");
    });

    return item;
}

function formatFileSize(bytes) {
    if (!bytes) return "—";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
}

function formatStatusLabel(status) {
    const labels = {
        uploaded: "Uploaded",
        preprocessed: "Preprocessed",
        parsed: "Parsed",
        entity_extracted: "Entities",
        graph_ready: "Ready"
    };
    return labels[status] || status;
}

function formatDocDate(isoString) {
    if (!isoString) return "—";
    const date = new Date(isoString.replace(" ", "T") + "Z");
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

document.getElementById("doc-panel-toggle").addEventListener("click", toggleDocPanel);
document.getElementById("doc-panel-close").addEventListener("click", closeDocPanel);
docPanelOverlay.addEventListener("click", closeDocPanel);

// ==========================================
// Event Listeners — Input
// ==========================================

input.addEventListener("focus", () => {
    inputBox.classList.add("is-focused");
});

input.addEventListener("blur", () => {
    inputBox.classList.remove("is-focused");
});

input.addEventListener("input", () => {
    resizeTextarea();
    updateCharCount();
    updateSendBtn();
});

input.addEventListener("scroll", updateScrollbar);

// Catch typing attempts when already at limit (maxlength blocks the input event)
input.addEventListener("keydown", (event) => {
    const isTyping = event.key.length === 1 && !event.ctrlKey && !event.metaKey;
    if (isTyping && input.value.length >= MAX_CHARS) {
        showLimitError();
    }
});

// Send on Enter
input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        clearAndSend();
    }
});

// Catch paste attempts that would exceed the limit
input.addEventListener("paste", (event) => {
    const paste = (event.clipboardData || window.clipboardData).getData("text");
    const currentLen = input.value.length;
    const selectionLen = input.selectionEnd - input.selectionStart;
    const resultLen = currentLen - selectionLen + paste.length;
    if (resultLen > MAX_CHARS) {
        showLimitError();
    }
});

// Firefox undo tip — show once per session on first Ctrl+Z
if (isFirefox) {
    input.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === "z" && !undoTipShown) {
            undoTipShown = true;
            undoTip.classList.add("visible");
            setTimeout(() => {
                undoTip.classList.remove("visible");
            }, 20000);
        }
    });
}

// ==========================================
// Event Listeners — Send Button
// ==========================================

sendBtn.addEventListener("click", clearAndSend);

// ==========================================
// Event Listeners — Sidebar
// ==========================================

document.getElementById("sidebar-close").addEventListener("click", toggleSidebar);
document.getElementById("sidebar-open").addEventListener("click", toggleSidebar);
sidebarOverlay.addEventListener("click", toggleSidebar);
document.getElementById("new-chat-btn").addEventListener("click", createNewChat);

// ==========================================
// Event Listeners — Custom Scrollbar
// ==========================================

scrollThumb.addEventListener("mousedown", (e) => {
    isDragging = true;
    dragStartY = e.clientY;
    dragStartScroll = input.scrollTop;
    e.preventDefault();
});

document.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    const trackHeight = scrollTrack.clientHeight;
    const thumbHeight = scrollThumb.clientHeight;
    const deltaY = e.clientY - dragStartY;
    const scrollRange = input.scrollHeight - input.clientHeight;
    const trackRange = trackHeight - thumbHeight;
    input.scrollTop = dragStartScroll + (deltaY / trackRange) * scrollRange;
});

document.addEventListener("mouseup", () => {
    isDragging = false;
});

// Click on track to jump
scrollTrack.addEventListener("click", (e) => {
    if (e.target === scrollThumb) return;
    const trackRect = scrollTrack.getBoundingClientRect();
    const clickRatio = (e.clientY - trackRect.top) / trackRect.height;
    input.scrollTop = clickRatio * (input.scrollHeight - input.clientHeight);
});

// ==========================================
// Initialization
// ==========================================

resizeTextarea();
updateSendBtn();

// Re-measure after web fonts load to prevent jiggle from font swap,
// then reveal the input box once everything is sized correctly
document.fonts.ready.then(() => {
    resizeTextarea();
    inputBox.classList.add("ready");
});

// Load conversations and select the most recent, or create a new one
loadConversations().then(async () => {
    const items = conversationList.children;
    if (items.length > 0) {
        await switchConversation(items[0].dataset.id);
    } else {
        await createNewChat();
    }
});

// Load documents into the knowledge panel
// Start collapsed on mobile, open on desktop
if (window.innerWidth <= 768) {
    docPanel.classList.add("collapsed");
}
loadDocuments();

// Update timestamps every 5 seconds
setInterval(updateTimestamps, 5000);
