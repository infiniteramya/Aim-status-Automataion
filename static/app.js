const $ = (sel) => document.querySelector(sel);

const uploadSection = $("#upload-section");
const progressSection = $("#progress-section");
const resultsSection = $("#results-section");

let currentJobId = null;
let eventSource = null;

// --- Drop Zone ---

const dropZone = $("#drop-zone");
const fileInput = $("#file-input");

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
    if (fileInput.files.length) uploadFile(fileInput.files[0]);
});

// --- Upload ---

async function uploadFile(file) {
    if (!file.name.endsWith(".csv")) {
        alert("Please select a CSV file.");
        return;
    }

    const form = new FormData();
    form.append("file", file);

    try {
        const res = await fetch("/api/upload", { method: "POST", body: form });
        if (!res.ok) {
            const err = await res.json();
            alert(err.detail || "Upload failed");
            return;
        }
        const data = await res.json();
        currentJobId = data.job_id;
        showPreview(data);
    } catch (e) {
        alert("Upload error: " + e.message);
    }
}

function showPreview(data) {
    $("#file-name").textContent = data.filename;
    $("#row-count").textContent = data.total_schools;

    const head = $("#preview-head");
    const body = $("#preview-body");
    head.innerHTML = "";
    body.innerHTML = "";

    // Header row
    const tr = document.createElement("tr");
    data.columns.forEach((col) => {
        const th = document.createElement("th");
        th.textContent = col;
        tr.appendChild(th);
    });
    head.appendChild(tr);

    // Data rows
    data.preview.forEach((row) => {
        const tr = document.createElement("tr");
        data.columns.forEach((col) => {
            const td = document.createElement("td");
            td.textContent = row[col] ?? "";
            tr.appendChild(td);
        });
        body.appendChild(tr);
    });

    $("#preview-section").hidden = false;
}

// --- Start Job ---

$("#start-btn").addEventListener("click", async () => {
    if (!currentJobId) return;

    try {
        const res = await fetch(`/api/jobs/${currentJobId}/start`, { method: "POST" });
        if (!res.ok) {
            const err = await res.json();
            alert(err.detail || "Could not start job");
            return;
        }
        showProgress();
        connectSSE();
    } catch (e) {
        alert("Start error: " + e.message);
    }
});

function showProgress() {
    uploadSection.hidden = true;
    progressSection.hidden = false;
    resultsSection.hidden = true;
    $("#log-output").textContent = "";
    $("#progress-bar").value = 0;
    $("#progress-text").textContent = "Starting...";
    $("#current-school").textContent = "";
}

// --- SSE ---

function connectSSE() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(`/api/jobs/${currentJobId}/progress`);

    eventSource.onmessage = (e) => {
        const data = JSON.parse(e.data);
        handleEvent(data);
    };

    eventSource.onerror = () => {
        // EventSource auto-reconnects. Only log if connection is fully closed.
        if (eventSource && eventSource.readyState === EventSource.CLOSED) {
            appendLog("[Connection closed — reconnecting...]");
            // Reconnect after a short delay
            setTimeout(() => {
                if (currentJobId && progressSection && !progressSection.hidden) {
                    connectSSE();
                }
            }, 3000);
        }
    };
}

function handleEvent(data) {
    if (data.event === "ping") return;

    if (data.event === "state") {
        if (data.total_schools > 0) {
            const pct = Math.round((data.processed / data.total_schools) * 100);
            $("#progress-bar").value = pct;
            $("#progress-text").textContent = `${data.processed}/${data.total_schools} schools processed (${pct}%)`;
        }
        return;
    }

    if (data.message) appendLog(data.message);

    if (data.event === "school_start") {
        $("#current-school").textContent = data.school_name || "";
        if (data.total_schools > 0) {
            const pct = Math.round(((data.school_index - 1) / data.total_schools) * 100);
            $("#progress-bar").value = pct;
            $("#progress-text").textContent = `${data.school_index - 1}/${data.total_schools} schools processed (${pct}%)`;
        }
    }

    if (data.event === "school_done") {
        if (data.total_schools > 0) {
            const pct = Math.round((data.school_index / data.total_schools) * 100);
            $("#progress-bar").value = pct;
            $("#progress-text").textContent = `${data.school_index}/${data.total_schools} schools processed (${pct}%)`;
        }
    }

    if (data.event === "done" || data.event === "error") {
        if (eventSource) eventSource.close();
        eventSource = null;
        $("#progress-bar").value = 100;
        $("#progress-text").textContent = data.event === "error" ? "Failed!" : "Complete!";
        loadResults();
    }
}

function appendLog(msg) {
    const log = $("#log-output");
    const ts = new Date().toLocaleTimeString();
    log.textContent += `[${ts}] ${msg}\n`;
    const container = $("#log-container");
    container.scrollTop = container.scrollHeight;
}

// --- Stop ---

$("#stop-btn").addEventListener("click", async () => {
    if (!currentJobId) return;
    try {
        await fetch(`/api/jobs/${currentJobId}/stop`, { method: "POST" });
        appendLog("Stop requested — waiting for current school to finish...");
        $("#stop-btn").disabled = true;
    } catch (e) {
        appendLog("Stop error: " + e.message);
    }
});

// --- Results ---

async function loadResults() {
    try {
        const res = await fetch(`/api/jobs/${currentJobId}/results`);
        if (!res.ok) {
            appendLog("Could not load results.");
            return;
        }
        const data = await res.json();
        showResults(data);
    } catch (e) {
        appendLog("Error loading results: " + e.message);
    }
}

function showResults(data) {
    progressSection.hidden = true;
    resultsSection.hidden = false;

    // Summary
    $("#sum-total").textContent = data.summary.total;
    $("#sum-approved").textContent = data.summary.approved;
    $("#sum-pending").textContent = data.summary.pending;
    $("#sum-failed").textContent = data.summary.failed;

    // Table
    const head = $("#results-head");
    const body = $("#results-body");
    head.innerHTML = "";
    body.innerHTML = "";

    const cols = data.columns;
    const htr = document.createElement("tr");
    cols.forEach((col) => {
        const th = document.createElement("th");
        th.textContent = col;
        htr.appendChild(th);
    });
    head.appendChild(htr);

    data.rows.forEach((row) => {
        const tr = document.createElement("tr");
        cols.forEach((col) => {
            const td = document.createElement("td");
            const val = String(row[col] ?? "");
            td.textContent = val;

            if (col === "status") {
                if (val === "APPROVED") td.className = "status-approved";
                else if (val.includes("PENDING")) td.className = "status-pending";
                else if (val) td.className = "status-failed";
            }
            tr.appendChild(td);
        });
        body.appendChild(tr);
    });
}

// --- Download ---

$("#download-btn").addEventListener("click", () => {
    if (currentJobId) {
        window.location.href = `/api/jobs/${currentJobId}/download`;
    }
});

// --- New Check ---

$("#new-btn").addEventListener("click", () => {
    currentJobId = null;
    if (eventSource) { eventSource.close(); eventSource = null; }
    uploadSection.hidden = false;
    progressSection.hidden = true;
    resultsSection.hidden = true;
    $("#preview-section").hidden = true;
    $("#stop-btn").disabled = false;
    fileInput.value = "";
});
