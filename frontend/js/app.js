/**
 * Main application logic for the Internship Bot Dashboard
 */

// ============================================
// STATE
// ============================================
const state = {
    isRunning: false,
    jobs: [],
    logs: [],
    stats: {
        total_jobs: 0,
        total_applications: 0,
        total_emails: 0,
        jobs_by_region: {},
        jobs_by_status: {}
    }
};

// ============================================
// DOM ELEMENTS
// ============================================
const elements = {
    // Navigation
    navItems: document.querySelectorAll('.nav-item'),
    sections: document.querySelectorAll('.content-section'),
    pageTitle: document.getElementById('pageTitle'),

    // Connection status
    connectionStatus: document.getElementById('connectionStatus'),
    botStatus: document.getElementById('botStatus'),

    // Form
    configForm: document.getElementById('configForm'),
    startBtn: document.getElementById('startBtn'),
    stopBtn: document.getElementById('stopBtn'),

    // Stats
    statJobsFound: document.getElementById('statJobsFound'),
    statApplications: document.getElementById('statApplications'),
    statEmails: document.getElementById('statEmails'),
    statRegions: document.getElementById('statRegions'),

    // Logs
    logsContainer: document.getElementById('logsContainer'),
    fullLogsContainer: document.getElementById('fullLogsContainer'),
    clearLogs: document.getElementById('clearLogs'),

    // Jobs table
    jobsTableBody: document.getElementById('jobsTableBody'),
    jobsRegionFilter: document.getElementById('jobsRegionFilter'),
    jobsStatusFilter: document.getElementById('jobsStatusFilter'),

    // Logs filter
    logsLevelFilter: document.getElementById('logsLevelFilter')
};

// ============================================
// NAVIGATION
// ============================================
function initNavigation() {
    elements.navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const section = item.dataset.section;

            // Update active nav
            elements.navItems.forEach(nav => nav.classList.remove('active'));
            item.classList.add('active');

            // Show section
            elements.sections.forEach(sec => sec.classList.remove('active'));
            document.getElementById(`${section}Section`).classList.add('active');

            // Update title
            const titles = {
                dashboard: 'Dashboard',
                jobs: 'Found Jobs',
                logs: 'Activity Logs'
            };
            elements.pageTitle.textContent = titles[section] || 'Dashboard';
        });
    });
}

// ============================================
// WEBSOCKET HANDLERS
// ============================================
function initWebSocket() {
    // Connect
    wsClient.on('connect', () => {
        updateConnectionStatus(true);
    });

    // Disconnect
    wsClient.on('disconnect', () => {
        updateConnectionStatus(false);
    });

    // Log messages
    wsClient.on('log', (data) => {
        addLogEntry(data);
    });

    // Stats updates
    wsClient.on('stats', (data) => {
        updateStats(data);
    });

    // Job updates
    wsClient.on('job', (data) => {
        if (data.action === 'created') {
            addJob(data.data);
        } else {
            updateJob(data.data);
        }
    });

    // Status updates
    wsClient.on('status', (data) => {
        updateBotStatus(data.status, data.details);
    });

    // Connect
    wsClient.connect();

    // Ping every 30 seconds to keep connection alive
    setInterval(() => {
        if (wsClient.isConnected) {
            wsClient.ping();
        }
    }, 30000);
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

    // Remove all status classes
    indicator.classList.remove('idle', 'running', 'error');

    // Add appropriate class
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
}

// ============================================
// STATS
// ============================================
function updateStats(data) {
    state.stats = data;

    elements.statJobsFound.textContent = data.total_jobs || 0;
    elements.statApplications.textContent = data.total_applications || 0;
    elements.statEmails.textContent = data.total_emails || 0;

    // Calculate active regions
    const regions = Object.keys(data.jobs_by_region || {});
    elements.statRegions.textContent = regions.length > 0 ? regions.join(', ') : '-';
}

// ============================================
// LOGS
// ============================================
function addLogEntry(data) {
    const entry = document.createElement('div');
    entry.className = 'log-entry';

    const time = new Date(data.timestamp).toLocaleTimeString('en-US', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });

    entry.innerHTML = `
        <span class="log-time">${time}</span>
        <span class="log-level ${data.level}">${data.level}</span>
        <span class="log-action">${data.action}</span>
        <span class="log-region">${data.region || '-'}</span>
        <span class="log-message">${escapeHtml(data.message)}</span>
    `;

    // Add to both log containers
    const placeholder = elements.logsContainer.querySelector('.log-placeholder');
    if (placeholder) {
        placeholder.remove();
    }

    elements.logsContainer.prepend(entry.cloneNode(true));

    // Also add to full logs
    const fullPlaceholder = elements.fullLogsContainer.querySelector('.log-placeholder');
    if (fullPlaceholder) {
        fullPlaceholder.remove();
    }
    elements.fullLogsContainer.prepend(entry);

    // Keep only last 100 entries in dashboard
    while (elements.logsContainer.children.length > 100) {
        elements.logsContainer.lastChild.remove();
    }

    // Save to state
    state.logs.unshift(data);
    if (state.logs.length > 500) {
        state.logs.pop();
    }
}

function clearLogs() {
    elements.logsContainer.innerHTML = `
        <div class="log-placeholder">
            <span>Activity logs will appear here when the bot starts...</span>
        </div>
    `;
    state.logs = [];
}

// ============================================
// JOBS
// ============================================
function addJob(job) {
    // Remove empty row if exists
    const emptyRow = elements.jobsTableBody.querySelector('.empty-row');
    if (emptyRow) {
        emptyRow.remove();
    }

    const row = document.createElement('tr');
    row.dataset.jobId = job.id;
    row.innerHTML = createJobRowHTML(job);

    elements.jobsTableBody.prepend(row);

    // Save to state
    state.jobs.unshift(job);
}

function updateJob(job) {
    const row = elements.jobsTableBody.querySelector(`tr[data-job-id="${job.id}"]`);
    if (row) {
        row.innerHTML = createJobRowHTML(job);
    }

    // Update in state
    const index = state.jobs.findIndex(j => j.id === job.id);
    if (index !== -1) {
        state.jobs[index] = job;
    }
}

function createJobRowHTML(job) {
    const date = new Date(job.created_at).toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric'
    });

    return `
        <td>${escapeHtml(job.company)}</td>
        <td>${escapeHtml(job.title)}</td>
        <td>${job.region}</td>
        <td>${job.source || '-'}</td>
        <td><span class="status-badge ${job.status}">${job.status}</span></td>
        <td>${date}</td>
    `;
}

async function loadJobs(region = '', status = '') {
    try {
        let url = '/api/jobs?limit=100';
        if (region) url += `&region=${region}`;
        if (status) url += `&status=${status}`;

        const response = await fetch(url);
        const data = await response.json();

        state.jobs = data.jobs || [];
        renderJobs();
    } catch (error) {
        console.error('Failed to load jobs:', error);
    }
}

function renderJobs() {
    if (state.jobs.length === 0) {
        elements.jobsTableBody.innerHTML = `
            <tr class="empty-row">
                <td colspan="6">No jobs found yet. Start the bot to begin searching.</td>
            </tr>
        `;
        return;
    }

    elements.jobsTableBody.innerHTML = state.jobs.map(job => `
        <tr data-job-id="${job.id}">
            ${createJobRowHTML(job)}
        </tr>
    `).join('');
}

// ============================================
// FORM HANDLING
// ============================================
async function handleStart(e) {
    e.preventDefault();

    // Get selected regions
    const selectedRegions = [];
    document.querySelectorAll('input[name="regions"]:checked').forEach(cb => {
        selectedRegions.push(cb.value);
    });

    if (selectedRegions.length === 0) {
        alert('Please select at least one region');
        return;
    }

    const contactEmail = document.getElementById('contactEmail').value;
    if (!contactEmail) {
        alert('Please enter your contact email');
        return;
    }

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
        elements.startBtn.textContent = 'Starting...';

        const response = await fetch('/api/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to start');
        }

        console.log('Bot started:', data);

    } catch (error) {
        console.error('Failed to start bot:', error);
        alert('Failed to start bot: ' + error.message);
        elements.startBtn.disabled = false;
    } finally {
        elements.startBtn.innerHTML = '<span class="btn-icon">▶</span> Start Bot';
    }
}

async function handleStop() {
    try {
        elements.stopBtn.disabled = true;
        elements.stopBtn.textContent = 'Stopping...';

        const response = await fetch('/api/stop', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: 'User requested stop' })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Failed to stop');
        }

        console.log('Bot stopped:', data);

    } catch (error) {
        console.error('Failed to stop bot:', error);
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
    elements.jobsRegionFilter.addEventListener('change', () => {
        loadJobs(elements.jobsRegionFilter.value, elements.jobsStatusFilter.value);
    });

    elements.jobsStatusFilter.addEventListener('change', () => {
        loadJobs(elements.jobsRegionFilter.value, elements.jobsStatusFilter.value);
    });
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
async function init() {
    console.log('Initializing Internship Bot Dashboard...');

    // Initialize navigation
    initNavigation();

    // Initialize WebSocket
    initWebSocket();

    // Initialize filters
    initFilters();

    // Form handlers
    elements.configForm.addEventListener('submit', handleStart);
    elements.stopBtn.addEventListener('click', handleStop);
    elements.clearLogs.addEventListener('click', clearLogs);

    // Load initial data
    try {
        const response = await fetch('/api/status');
        const status = await response.json();
        updateBotStatus(status.status);
    } catch (error) {
        console.error('Failed to load initial status:', error);
    }

    // Load jobs
    loadJobs();

    console.log('Dashboard initialized');
}

// Start the app
document.addEventListener('DOMContentLoaded', init);
