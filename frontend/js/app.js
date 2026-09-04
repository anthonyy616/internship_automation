/**
 * Main application logic for the Internship Bot Dashboard.
 *
 * The dashboard shows the TRUTH about the pipeline:
 *   - "Applications Sent" counts only real submissions (submit clicked).
 *   - Dry-run fills, failures and paused questions are separate numbers.
 *   - The Live Activity feed streams worker events in real time (via the
 *     server's event bridge), including errors (red) and screenshots.
 *   - The queue strip shows worker liveness (heartbeat) + queue depth.
 */

// ============================================
// STATE
// ============================================
const state = {
    isRunning: false,
    jobs: [],
    activity: [], // agent events, newest first
    stats: {
        jobs_found: 0,
        awaiting: 0,
        applications_sent: 0,
        dry_run_completed: 0,
        attempts_failed: 0,
        paused_awaiting_input: 0,
        emails_sent: 0,
        jobs_by_region: {},
        jobs_by_status: {}
    }
};

const MAX_DASHBOARD_ROWS = 200;
const MAX_FULL_ROWS = 500;

// ============================================
// DOM ELEMENTS
// ============================================
const elements = {
    navItems: document.querySelectorAll('.nav-item'),
    sections: document.querySelectorAll('.content-section'),
    pageTitle: document.getElementById('pageTitle'),
    connectionStatus: document.getElementById('connectionStatus'),
    botStatus: document.getElementById('botStatus'),
    configForm: document.getElementById('configForm'),
    startBtn: document.getElementById('startBtn'),
    stopBtn: document.getElementById('stopBtn'),
    statJobsFound: document.getElementById('statJobsFound'),
    statAwaiting: document.getElementById('statAwaiting'),
    statApplications: document.getElementById('statApplications'),
    statApplicationsSub: document.getElementById('statApplicationsSub'),
    statEmails: document.getElementById('statEmails'),
    statDryRuns: document.getElementById('statDryRuns'),
    logsContainer: document.getElementById('logsContainer'),
    fullLogsContainer: document.getElementById('fullLogsContainer'),
    clearLogs: document.getElementById('clearLogs'),
    jobsTableBody: document.getElementById('jobsTableBody'),
    jobsRegionFilter: document.getElementById('jobsRegionFilter'),
    jobsStatusFilter: document.getElementById('jobsStatusFilter'),
    logsLevelFilter: document.getElementById('logsLevelFilter'),
    qWorker: document.getElementById('qWorker'),
    qQueued: document.getElementById('qQueued'),
    qInProgress: document.getElementById('qInProgress'),
    qAwaiting: document.getElementById('qAwaiting'),
    qLastActivity: document.getElementById('qLastActivity'),
    qMode: document.getElementById('qMode'),
    analyticsCard: document.getElementById('analyticsCard'),
    analyticsTable: document.getElementById('analyticsTable')
};

// ============================================
// NAVIGATION
// ============================================
function initNavigation() {
    elements.navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const section = item.dataset.section;

            elements.navItems.forEach(nav => nav.classList.remove('active'));
            item.classList.add('active');

            elements.sections.forEach(sec => sec.classList.remove('active'));
            document.getElementById(`${section}Section`).classList.add('active');

            const titles = { dashboard: 'Dashboard', jobs: 'Found Jobs', logs: 'Activity Logs' };
            elements.pageTitle.textContent = titles[section] || 'Dashboard';
        });
    });
}

// ============================================
// WEBSOCKET HANDLERS
// ============================================
function initWebSocket() {
    wsClient.on('connect', () => updateConnectionStatus(true));
    wsClient.on('disconnect', () => updateConnectionStatus(false));
    wsClient.on('stats', (data) => updateStats(data));
    wsClient.on('agent_event', (event) => onAgentEvent(event));
    wsClient.on('history', (events) => onHistory(events));
    wsClient.on('job', (data) => {
        if (data.action === 'created') addJob(data.data); else updateJob(data.data);
    });
    wsClient.on('status', (data) => updateBotStatus(data.status, data.details));
    wsClient.connect();

    // Keep-alive ping every 30s
    setInterval(() => { if (wsClient.isConnected) wsClient.ping(); }, 30000);
}

function updateConnectionStatus(connected) {
    const dot = elements.connectionStatus.querySelector('.status-dot');
    const text = elements.connectionStatus.querySelector('span:last-child');
    if (connected) {
        dot.classList.remove('disconnected');
        dot.classList.add('connected');
        text.textContent = 'Connected';
    } else {
        dot.classList.remove('connected');
        dot.classList.add('disconnected');
        text.textContent = 'Disconnected';
    }
}

function updateBotStatus(status, details) {
    const indicator = elements.botStatus.querySelector('.status-indicator');
    const text = elements.botStatus.querySelector('.status-text');

    indicator.classList.remove('idle', 'running', 'error');
    if (status === 'running') {
        indicator.classList.add('running');
        state.isRunning = true;
        elements.startBtn.disabled = true;
        elements.stopBtn.disabled = false;
    } else if (status === 'error') {
        indicator.classList.add('error');
        state.isRunning = false;
        elements.startBtn.disabled = false;
        elements.stopBtn.disabled = true;
    } else {
        indicator.classList.add('idle');
        state.isRunning = false;
        elements.startBtn.disabled = false;
        elements.stopBtn.disabled = true;
    }
    text.textContent = status.charAt(0).toUpperCase() + status.slice(1);
    if (details) text.title = details;
}

// ============================================
// STATS (truthful numbers)
// ============================================
function updateStats(data) {
    if (!data) return;
    state.stats = Object.assign({}, state.stats, data);

    const jobsFound = data.jobs_found ?? data.total_jobs ?? 0;
    const awaiting = data.awaiting ?? 0;
    const sent = data.applications_sent ?? data.applications_submitted ?? data.total_applications ?? 0;
    const emails = data.emails_sent ?? data.total_emails ?? 0;
    const dry = data.dry_run_completed ?? 0;
    const failed = data.attempts_failed ?? 0;
    const paused = data.paused_awaiting_input ?? 0;

    elements.statJobsFound.textContent = jobsFound;
    elements.statAwaiting.textContent = awaiting;
    elements.statApplications.textContent = sent;
    elements.statEmails.textContent = emails;
    elements.statDryRuns.textContent = dry;

    const parts = [];
    if (dry) parts.push(`${dry} dry-run`);
    if (failed) parts.push(`${failed} failed`);
    if (paused) parts.push(`${paused} waiting on you`);
    elements.statApplicationsSub.textContent = parts.join(' · ') || '—';
}

// ============================================
// QUEUE / WORKER STATUS STRIP
// ============================================
function setQueueWorker(el, alive) {
    el.textContent = alive ? '● alive' : '○ offline';
    el.className = 'queue-val ' + (alive ? 'q-alive' : 'q-dead');
}

function timeAgo(iso) {
    if (!iso) return '–';
    const then = new Date(iso).getTime();
    if (isNaN(then)) return '–';
    const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (secs < 5) return 'just now';
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    return `${Math.floor(mins / 60)}h ${mins % 60}m ago`;
}

async function refreshQueueStatus() {
    try {
        const res = await fetch('/api/queue');
        const data = await res.json();
        setQueueWorker(elements.qWorker, !!data.worker_alive);
        elements.qQueued.textContent = data.queued_tasks ?? 0;
        elements.qInProgress.textContent = data.in_progress_tasks ?? 0;
        elements.qAwaiting.textContent = data.jobs_waiting ?? 0;
        elements.qLastActivity.textContent = timeAgo(data.last_activity);
        elements.qLastActivity.title = data.last_activity ? new Date(data.last_activity).toLocaleString() : '';
        elements.qMode.textContent = data.dry_run_mode ? 'DRY RUN (no submits)' : 'LIVE (real submits)';
        elements.qMode.className = 'queue-val ' + (data.dry_run_mode ? 'q-dry' : 'q-live');
    } catch (e) {
        setQueueWorker(elements.qWorker, false);
    }
}

async function refreshAnalytics() {
    try {
        const res = await fetch('/api/analytics?limit=15');
        const data = await res.json();
        const rows = data.domains || [];
        if (!rows.length) {
            elements.analyticsCard.style.display = 'none';
            return;
        }
        elements.analyticsCard.style.display = 'flex';
        elements.analyticsTable.querySelector('tbody').innerHTML = rows.map(d => {
            const rate = d.rate == null ? '—' : Math.round(d.rate * 100) + '%';
            const cls = d.rate == null ? '' : (d.rate >= 0.5 ? 'rate-good' : 'rate-bad');
            return `<tr>
                <td>${escapeHtml(d.domain)}</td>
                <td>${d.applied ?? 0}</td>
                <td>${d.failed ?? 0}</td>
                <td class="${cls}">${rate}</td>
                <td>${timeAgo(d.last_attempt)}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        /* analytics are best-effort */
    }
}

async function refreshStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        updateBotStatus(data.status || 'idle');
        updateStats(data);
    } catch (e) {
        /* server not reachable yet */
    }
}

// ============================================
// LIVE ACTIVITY FEED
// ============================================
const STATUS_ICONS = { started: '🔄', success: '✅', failed: '❌', escalated: '❓' };

function hostOf(url) {
    if (!url) return '';
    try { return new URL(url).hostname.replace('www.', ''); } catch (e) { return ''; }
}

function prettyAction(action) {
    return (action || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function describeEvent(ev) {
    const stage = ev.stage || '';
    const action = ev.action || '';
    const meta = ev.metadata || {};
    const pair = `${stage}/${action}`;
    const company = meta.company ? meta.company : '';
    const title = meta.title ? ` — ${meta.title}` : '';
    const host = hostOf(ev.target_url);

    switch (pair) {
        case 'scrape/search': return `Searching ${meta.source || '?'} (${meta.region || host || ''})`;
        case 'scrape/search_complete':
            return `Search ${meta.source || ''} ${meta.region || ''}: found ${meta.found ?? 0}` +
                (meta.new ? `, ${meta.new} new` : '') + (meta.filtered_out ? `, ${meta.filtered_out} filtered` : '');
        case 'scrape/found_job': return `New job: ${company || '?'}${title} (${meta.source || ''})`;
        case 'apply/apply': return `Applying to ${company || host || 'job'}${title}`;
        case 'apply/browser_session':
            return `🌐 Browser session opened — ${company || host || 'job'}${title}` +
                (meta.platform ? ` (${meta.platform})` : '');
        case 'apply/filled_field': return `✍️ Filled "${meta.field || ''}" on ${company || host || 'the form'}`;
        case 'apply/applied':
            return `APPLIED ✓ ${company || host || ''}${title}` + (meta.ats ? ` · via ${meta.ats}` : '') +
                (meta.filled_fields ? ` · ${meta.filled_fields} fields filled` : '');
        case 'apply/dry_run_completed': return `Dry-run fill done (no submit) — ${company || host || ''}${title}`;
        case 'apply/apply_failed':
        case 'apply/apply_error': return `Apply failed — ${company || host || 'job'}${title}`;
        case 'apply/tier1_failed': return `⚠️ ${meta.platform || 'ATS'} adapter failed — trying the generic form filler`;
        case 'apply/paused_awaiting_input': return `Waiting for your Telegram answer — ${company || host || ''}${title}`;
        case 'apply/question_escalated': return `❓ Telegram question sent: "${meta.question || ''}"`;
        case 'apply/daily_limit_reached': return `Daily cap reached (${meta.count}/${meta.max}) — stop or raise the limit`;
        case 'system/llm_unavailable':
            return `⚠️ LLM unavailable — field mapping degraded. Add OpenAI credits or check the key.`;
        case 'apply/llm_mapping_failed': return `⚠️ LLM field mapping failed — using profile & saved answers`;
        case 'system/submission_notified': return `Sent you proof: ${(meta.channels || []).join(' + ')} — ${company || ''}`;
        case 'system/queue_processed':
            return `Queue drained: moved ${meta.jobs_moved ?? 0} job(s) → apply queue` +
                (meta.stale_recovered ? `, recovered ${meta.stale_recovered} stuck` : '');
        case 'system/scheduled_scrape': return `Scheduled scrape: ${(meta.sources || []).join(', ')}`;
        case 'system/telegram_answered': return `📱 Telegram answer saved: "${meta.answer || ''}"`;
        case 'system/telegram_bot_started': return `Telegram bot polling started`;
        default: {
            const base = `${prettyAction(stage)} › ${prettyAction(action)}`;
            return company ? `${base} — ${company}${title}` : base;
        }
    }
}

function screenshotSrc(url) {
    if (!url) return null;
    if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('/')) return url;
    return '/' + url; // "screenshots/<app>/x.png" -> "/screenshots/<app>/x.png"
}

function createActivityElement(ev) {
    const row = document.createElement('div');
    const icon = STATUS_ICONS[ev.status] || '•';
    const cls = ['started', 'success', 'failed', 'escalated'].includes(ev.status) ? ev.status : 'info';
    row.className = `activity-entry ${cls}`;
    row.dataset.status = ev.status || '';
    row.dataset.eventId = ev.id || '';

    const time = ev.created_at ? new Date(ev.created_at).toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', second: '2-digit'
    }) : '';
    const ts = document.createElement('span');
    ts.className = 'log-time';
    ts.textContent = time;

    const iconSpan = document.createElement('span');
    iconSpan.className = 'activity-icon';
    iconSpan.textContent = icon;

    const body = document.createElement('div');
    body.className = 'activity-body';

    const line = document.createElement('div');
    line.className = 'activity-line';
    line.appendChild(document.createTextNode(describeEvent(ev)));
    body.appendChild(line);

    if (ev.error_text) {
        const err = document.createElement('div');
        err.className = 'activity-error';
        err.textContent = ev.error_text;
        err.title = 'Full detail is in the terminal (worker output).';
        body.appendChild(err);
    }

    const shot = screenshotSrc(ev.screenshot_url);
    if (shot) {
        const a = document.createElement('a');
        a.href = shot;
        a.target = '_blank';
        a.title = 'Open screenshot (evidence the browser filled the form)';
        const img = document.createElement('img');
        img.className = 'activity-shot';
        img.src = shot;
        img.loading = 'lazy';
        img.onerror = () => a.remove();
        a.appendChild(img);
        body.appendChild(a);
    }

    if (ev.target_url && ev.status === 'success' && ev.action === 'applied') {
        const link = document.createElement('a');
        link.className = 'activity-job-link';
        link.href = ev.target_url;
        link.target = '_blank';
        link.textContent = '↗ open job';
        body.appendChild(link);
    }

    row.appendChild(ts);
    row.appendChild(iconSpan);
    row.appendChild(body);
    return row;
}

function removePlaceholder(container) {
    const ph = container.querySelector('.log-placeholder');
    if (ph) ph.remove();
}

function trimActivity(container, max) {
    while (container.children.length > max) {
        container.lastChild.remove();
    }
}

function renderActivityFiltered() {
    elements.fullLogsContainer.innerHTML = '';
    removePlaceholder(elements.fullLogsContainer);
    const filter = elements.logsLevelFilter.value;
    state.activity.forEach(ev => {
        if (filter && (ev.status || '') !== filter) return;
        elements.fullLogsContainer.appendChild(createActivityElement(ev));
    });
    trimActivity(elements.fullLogsContainer, MAX_FULL_ROWS);
    if (!elements.fullLogsContainer.children.length) {
        elements.fullLogsContainer.innerHTML = `<div class="log-placeholder"><span>No activity yet.</span></div>`;
    }
}

function prependActivity(ev) {
    // Dashboard container: live, unfiltered, newest first
    const node = createActivityElement(ev);
    const container = elements.logsContainer;
    removePlaceholder(container);
    container.prepend(node);
    trimActivity(container, MAX_DASHBOARD_ROWS);

    // Full page container: respects the outcome filter
    const full = elements.fullLogsContainer;
    const filter = elements.logsLevelFilter.value;
    if (!filter || (ev.status || '') === filter) {
        removePlaceholder(full);
        full.prepend(createActivityElement(ev));
        trimActivity(full, MAX_FULL_ROWS);
    }
}

function onAgentEvent(ev) {
    if (!ev || !ev.action) return;
    state.activity.unshift(ev);
    if (state.activity.length > MAX_FULL_ROWS) state.activity.pop();
    prependActivity(ev);

    // Refresh stats occasionally (events imply counts changed)
    refreshStatus();
}

function onHistory(events) {
    if (!events || !events.length) return;
    const fragment = document.createDocumentFragment();
    events.forEach(ev => {
        if (ev && ev.action) {
            state.activity.unshift(ev);
            fragment.appendChild(createActivityElement(ev));
        }
    });
    if (state.activity.length > MAX_FULL_ROWS) {
        state.activity.length = MAX_FULL_ROWS;
    }
    const container = elements.logsContainer;
    removePlaceholder(container);
    container.prepend(fragment);
    trimActivity(container, MAX_DASHBOARD_ROWS);

    // Full page follows the filter
    renderActivityFiltered();
}

function clearLogs() {
    state.activity = [];
    elements.logsContainer.innerHTML =
        `<div class="log-placeholder"><span>Waiting for activity… open the bot (worker) and scrape/apply events will stream here live, with errors shown in red and full details in the terminal.</span></div>`;
    renderActivityFiltered();
}

// ============================================
// JOBS
// ============================================
function addJob(job) {
    const emptyRow = elements.jobsTableBody.querySelector('.empty-row');
    if (emptyRow) emptyRow.remove();
    const row = document.createElement('tr');
    row.dataset.jobId = job.id;
    row.innerHTML = createJobRowHTML(job);
    elements.jobsTableBody.prepend(row);
    state.jobs.unshift(job);
}

function updateJob(job) {
    const row = elements.jobsTableBody.querySelector(`tr[data-job-id="${job.id}"]`);
    if (row) row.innerHTML = createJobRowHTML(job);
    const index = state.jobs.findIndex(j => j.id === job.id);
    if (index !== -1) state.jobs[index] = job;
}

function createJobRowHTML(job) {
    const date = new Date(job.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

    const btnStates = {
        discovered: { label: 'Apply', disabled: false },
        filtered: { label: 'Apply', disabled: false },
        queued: { label: 'Queued…', disabled: true },
        applying: { label: 'Applying…', disabled: true },
        dry_run: { label: 'Apply', disabled: false }, // dry-run fill can be submitted for real
        failed_needs_manual: { label: 'Retry', disabled: false },
        applied: { label: '✅', disabled: true }
    };
    const st = btnStates[job.status] || { label: '—', disabled: true };
    const action = st.disabled
        ? `<span class="status-cell">${st.label}</span>`
        : `<button class="btn btn-small btn-primary job-apply-btn" data-job-id="${escapeHtml(job.id)}">${st.label}</button>`;

    const title = job.status === 'failed_needs_manual'
        ? `<span class="status-badge ${job.status}" title="Click Retry to attempt again with the current code">${job.status}</span>`
        : `<span class="status-badge ${job.status}">${job.status}</span>`;

    return `
        <td>${escapeHtml(job.company || '')}</td>
        <td><a href="${escapeHtml(job.url || '#')}" target="_blank" rel="noopener">${escapeHtml(job.title || '')}</a></td>
        <td>${escapeHtml(job.region || '-')}</td>
        <td>${escapeHtml(job.source || '-')}</td>
        <td>${title}</td>
        <td>${date}</td>
        <td>${action}</td>
    `;
}

async function handleJobApplyClick(jobId, btn) {
    if (!jobId) return;
    btn.disabled = true;
    btn.textContent = 'Queuing…';
    try {
        const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/apply`, { method: 'POST' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Failed to queue');
        loadJobs(elements.jobsRegionFilter.value, elements.jobsStatusFilter.value);
        refreshQueueStatus();
        refreshStatus();
    } catch (error) {
        alert('Failed to queue: ' + error.message);
        btn.disabled = false;
        btn.textContent = 'Apply';
    }
}

async function loadJobs(region = '', status = '') {
    try {
        let url = '/api/jobs?limit=200';
        if (region) url += `&region=${encodeURIComponent(region)}`;
        if (status) url += `&status=${encodeURIComponent(status)}`;
        const response = await fetch(url);
        const data = await response.json();
        state.jobs = data.jobs || [];
        renderJobs();
    } catch (error) {
        console.error('Failed to load jobs:', error);
    }
}

function renderJobs() {
    if (!state.jobs.length) {
        elements.jobsTableBody.innerHTML = `
            <tr class="empty-row"><td colspan="7">No jobs found yet. Start the bot to begin searching.</td></tr>`;
        return;
    }
    elements.jobsTableBody.innerHTML = state.jobs.map(job =>
        `<tr data-job-id="${job.id}">${createJobRowHTML(job)}</tr>`).join('');
}

function initJobApplyButtons() {
    elements.jobsTableBody.addEventListener('click', (e) => {
        const btn = e.target.closest('.job-apply-btn');
        if (btn && !btn.disabled) handleJobApplyClick(btn.dataset.jobId, btn);
    });
}

// ============================================
// FORM HANDLING
// ============================================
async function handleStart(e) {
    e.preventDefault();

    const selectedRegions = [];
    document.querySelectorAll('input[name="regions"]:checked').forEach(cb => selectedRegions.push(cb.value));
    if (selectedRegions.length === 0) { alert('Please select at least one region'); return; }

    const contactEmail = document.getElementById('contactEmail').value;
    if (!contactEmail) { alert('Please enter your contact email'); return; }

    const payload = {
        regions: selectedRegions,
        contact_email: contactEmail,
        portfolio_url: document.getElementById('portfolioUrl').value,
        max_applications: parseInt(document.getElementById('maxApplications').value) || 50,
        max_emails: parseInt(document.getElementById('maxEmails').value) || 50,
        dry_run: document.getElementById('dryRun').checked
    };

    try {
        elements.startBtn.disabled = true;
        elements.startBtn.textContent = 'Starting…';
        const response = await fetch('/api/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Failed to start');

        let msg = 'Bot started.\n';
        if (data.apply_tasks_enqueued) msg += `Scraping ${data.scrape_tasks_enqueued} source(s) and draining the apply queue now.\n`;
        if (!data.worker_started && data.already_running) msg += 'Worker was already running.\n';
        else if (!data.worker_started) msg += "Warning: worker process could not be started — start it manually with:\npython -m arq backend.workers.settings.WorkerSettings";
        if (data.warning) msg += `\nNote: ${data.warning}`;
        alert(msg);
    } catch (error) {
        alert('Failed to start bot: ' + error.message);
        elements.startBtn.disabled = false;
    } finally {
        elements.startBtn.innerHTML = '<span class="btn-icon">▶</span> Start Bot';
    }
    setTimeout(() => { refreshStatus(); refreshQueueStatus(); loadJobs(); }, 1500);
}

async function handleStop() {
    try {
        elements.stopBtn.disabled = true;
        elements.stopBtn.textContent = 'Stopping…';
        const response = await fetch('/api/stop', { method: 'POST' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Failed to stop');
        alert(data.worker_stopped
            ? 'Worker stopped.'
            : "The worker wasn't started from this dashboard (it may be running in a terminal). Stop it there with Ctrl+C.");
    } catch (error) {
        alert('Failed to stop bot: ' + error.message);
    } finally {
        elements.stopBtn.innerHTML = '<span class="btn-icon">⏹</span> Stop Bot';
        elements.stopBtn.disabled = true;
        elements.startBtn.disabled = false;
    }
}

// ============================================
// FILTERS
// ============================================
function initFilters() {
    elements.jobsRegionFilter.addEventListener('change', () => loadJobs(elements.jobsRegionFilter.value, elements.jobsStatusFilter.value));
    elements.jobsStatusFilter.addEventListener('change', () => loadJobs(elements.jobsRegionFilter.value, elements.jobsStatusFilter.value));
    elements.logsLevelFilter.addEventListener('change', renderActivityFiltered);
}

// ============================================
// UTILITIES
// ============================================
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================
// INITIALIZATION
// ============================================
async function prefillConfigForm() {
    try {
        const response = await fetch('/api/config');
        const data = await response.json();
        if (!data || !data.profile) return;
        document.getElementById('contactEmail').value = data.profile.email || '';
        document.getElementById('portfolioUrl').value = data.profile.portfolio_url || '';
        if (data.limits) {
            document.getElementById('maxApplications').value = data.limits.max_applications_per_day ?? 50;
            document.getElementById('maxEmails').value = data.limits.max_emails_per_day ?? 50;
        }
        if (Array.isArray(data.regions)) {
            document.querySelectorAll('input[name="regions"]').forEach(cb => {
                cb.checked = data.regions.includes(cb.value);
            });
        }
        if (data.apply) {
            document.getElementById('dryRun').checked = data.apply.dry_run === true;
        }
    } catch (error) {
        console.error('Failed to load config:', error);
    }
}

async function init() {
    initNavigation();
    initWebSocket();
    initFilters();
    elements.configForm.addEventListener('submit', handleStart);
    elements.stopBtn.addEventListener('click', handleStop);
    elements.clearLogs.addEventListener('click', clearLogs);
    initJobApplyButtons();

    await prefillConfigForm();
    await refreshStatus();
    refreshQueueStatus();
    refreshAnalytics();
    loadJobs();

    // Keep the strip + buttons truthful while the bot works
    setInterval(refreshQueueStatus, 4000);
    setInterval(refreshStatus, 8000);
    setInterval(refreshAnalytics, 20000);
}

document.addEventListener('DOMContentLoaded', init);
