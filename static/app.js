/* Council of Alignment — Client JS */

// ─── Markdown rendering ──────────────────────────────────

function renderMarkdown(el) {
    if (!el || el.dataset.rendered) return;
    const raw = el.textContent;
    if (typeof marked !== 'undefined') {
        const html = marked.parse(raw);
        el.innerHTML = typeof DOMPurify !== 'undefined' ? DOMPurify.sanitize(html) : html;
    }
    el.dataset.rendered = 'true';
}

document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.markdown-body').forEach(renderMarkdown);
});

document.body.addEventListener('htmx:afterSwap', function(e) {
    if (e.detail.target) {
        e.detail.target.querySelectorAll('.markdown-body').forEach(renderMarkdown);
    }
});


// ─── Chat helpers ────────────────────────────────────────

function scrollChat() {
    const el = document.getElementById('chat-messages');
    if (el) {
        setTimeout(() => { el.scrollTop = el.scrollHeight; }, 50);
    }
}


// ─── Tab switching ──────────────────────────────────────

function switchTab(tabId, clickedTab) {
    // Find the closest review content container (works for both server-rendered and dynamic)
    const container = clickedTab.closest('.council-review-content') || clickedTab.closest('.council-results');
    if (!container) return;

    // Deactivate all tabs
    container.querySelectorAll('.council-tabs .tab').forEach(t => t.classList.remove('active'));
    // Activate clicked tab
    clickedTab.classList.add('active');

    // Hide all tab content
    container.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
    // Show selected tab content
    const target = container.querySelector('#tab-' + tabId);
    if (target) target.classList.add('active');
}


// ─── User menu ───────────────────────────────────────────

function toggleUserMenu() {
    const dropdown = document.getElementById('user-dropdown');
    if (dropdown) dropdown.classList.toggle('open');
}

document.addEventListener('click', function(e) {
    const menu = document.getElementById('user-menu');
    const dropdown = document.getElementById('user-dropdown');
    if (menu && dropdown && !menu.contains(e.target)) {
        dropdown.classList.remove('open');
    }
});

// ─── Auth: redirect to login on 401 ─────────────────────

document.body.addEventListener('htmx:responseError', function(e) {
    if (e.detail.xhr && e.detail.xhr.status === 401) {
        window.location.href = '/auth/login';
    }
});


// ─── Council decisions ───────────────────────────────────

const decisions = {};

function setDecision(btn, changeId, accepted) {
    decisions[changeId] = { id: changeId, accepted: accepted, reason: '' };

    const item = btn.closest('.change-item');

    // Clear previous state
    item.classList.remove('decided-accept', 'decided-reject');
    item.classList.add(accepted ? 'decided-accept' : 'decided-reject');

    // Update button states
    const actions = item.querySelector('.change-actions');
    actions.querySelectorAll('.btn').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');

    // Handle reject reason input
    if (!accepted) {
        const existing = item.querySelector('.reject-reason-input');
        if (!existing) {
            const input = document.createElement('input');
            input.type = 'text';
            input.placeholder = 'Reason for rejecting (optional)';
            input.className = 'reject-reason-input';
            input.addEventListener('input', function() {
                decisions[changeId].reason = this.value;
            });
            item.querySelector('.change-info').appendChild(input);
        }
    } else {
        const existing = item.querySelector('.reject-reason-input');
        if (existing) existing.remove();
    }
}

function submitDecisions() {
    const decisionList = Object.values(decisions);
    if (decisionList.length === 0) {
        alert('No decisions made yet. Accept or reject at least one change.');
        return;
    }

    const form = document.getElementById('decide-form');
    const action = form.getAttribute('data-action');
    const changesSection = form.closest('.changes-section');

    // Show loading state with spinner + timer
    const submitBtn = form.querySelector('.btn-submit-decisions');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner"></span> Submitting...';
    }
    const startTime = Date.now();
    const loadingHtml = `
        <div class="convene-progress" style="padding: var(--space-3);" id="decide-loading">
            <div class="spinner-large"></div>
            <h3>Lead AI is incorporating your decisions...</h3>
            <p class="dim">This may take 15-30 seconds.</p>
            <p class="convene-elapsed" id="decide-elapsed">0s</p>
        </div>
    `;
    if (changesSection) changesSection.insertAdjacentHTML('beforeend', loadingHtml);

    const timerInterval = setInterval(() => {
        const secs = Math.floor((Date.now() - startTime) / 1000);
        const el = document.getElementById('decide-elapsed');
        if (el) el.textContent = secs < 60 ? `${secs}s` : `${Math.floor(secs/60)}m ${secs%60}s`;
    }, 1000);

    fetch(action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decisions: decisionList })
    })
    .then(r => {
        if (r.status === 401) { window.location.href = '/auth/login'; return; }
        return r.text();
    })
    .then(html => {
        if (!html) return;
        clearInterval(timerInterval);

        // Parse the response
        const temp = document.createElement('div');
        temp.innerHTML = html;

        const chatMessages = document.getElementById('chat-messages');

        // Append the decide summary to chat
        const summary = temp.querySelector('.decide-summary');
        if (summary) {
            chatMessages.appendChild(summary);
        }

        // Append Lead's response to chat
        const leadMsgs = temp.querySelectorAll('.message.assistant-message');
        leadMsgs.forEach(msg => {
            chatMessages.appendChild(msg);
        });

        chatMessages.querySelectorAll('.markdown-body').forEach(renderMarkdown);
        scrollChat();

        // Refresh evolution timeline
        if (typeof loadTimeline === 'function') loadTimeline();
    })
    .catch(err => {
        clearInterval(timerInterval);
        document.getElementById('decide-loading')?.remove();
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = 'Submit Decisions';
        }
        if (changesSection) {
            changesSection.insertAdjacentHTML('beforeend',
                '<div style="color:var(--red);padding:1rem;">Error submitting decisions. Please try again.</div>');
        }
    });
}
