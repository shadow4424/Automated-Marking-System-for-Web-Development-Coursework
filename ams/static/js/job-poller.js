/**
 * AMS Job Widget — non-blocking floating job status tray.
 *
 * Submits mark/batch forms via fetch(), tracks every background job in
 * sessionStorage, and renders a collapsible widget (bottom-right) that
 * persists across page navigation.
 *
 * Included in base.html so it is active on every page.
 */
(function () {
    'use strict';

    var POLL_INITIAL_MS = 2000;
    var POLL_MAX_MS     = 10000;
    var POLL_BACKOFF    = 1.3;
    var STORAGE_KEY     = 'ams_jobs';

    // ── Job registry (sessionStorage-backed) ─────────────────────────

    var jobs = _loadJobs();
    var _widgetEl = null;
    var _expanded = false;
    // Track which jobIds we are actively polling so we don't double-up
    var _polling = {};

    function _loadJobs() {
        try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]'); }
        catch (e) { return []; }
    }

    function _saveJobs() {
        try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(jobs)); }
        catch (e) {}
    }

    function _addJob(jobId, runId, label, assignmentId) {
        jobs.push({ jobId: jobId, runId: runId, label: label,
                    assignmentId: assignmentId || '',
                    status: 'processing', startedAt: Date.now(),
                    completedAt: null, error: null });
        _saveJobs();
        _render();
    }

    function _updateJob(jobId, patch) {
        for (var i = 0; i < jobs.length; i++) {
            if (jobs[i].jobId === jobId) {
                var k; for (k in patch) jobs[i][k] = patch[k];
                break;
            }
        }
        _saveJobs();
        _render();
    }

    function _dismissJob(jobId) {
        jobs = jobs.filter(function (j) { return j.jobId !== jobId; });
        _saveJobs();
        _render();
    }

    function _activeJobs()    { return jobs.filter(function (j) { return j.status === 'processing'; }); }
    function _completedJobs() { return jobs.filter(function (j) { return j.status !== 'processing'; }); }

    // ── Widget rendering ──────────────────────────────────────────────

    function _ensureStyles() {
        if (document.getElementById('ams-widget-style')) return;
        var s = document.createElement('style');
        s.id = 'ams-widget-style';
        s.textContent = [
            '@keyframes ams-wspin{to{transform:rotate(360deg)}}',

            '#ams-job-widget{',
            '  position:fixed;bottom:1.25rem;right:1.25rem;z-index:9000;',
            '  width:300px;background:#1e1e2e;color:#e2e8f0;',
            '  border-radius:10px;box-shadow:0 8px 32px rgba(0,0,0,.4);',
            '  font-family:inherit;font-size:.875rem;',
            '  border:1px solid rgba(255,255,255,.08);overflow:hidden;}',

            '#ams-job-widget .wg-header{',
            '  display:flex;align-items:center;gap:.55rem;',
            '  padding:.65rem 1rem;cursor:pointer;user-select:none;',
            '  background:#2d2d44;}',
            '#ams-job-widget .wg-header:hover{background:#363655;}',

            '#ams-job-widget .wg-spinner{',
            '  width:14px;height:14px;flex-shrink:0;',
            '  border:2px solid rgba(255,255,255,.2);border-top-color:#6366f1;',
            '  border-radius:50%;animation:ams-wspin .7s linear infinite;}',

            '#ams-job-widget .wg-title{flex:1;font-weight:600;',
            '  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',

            '#ams-job-widget .wg-badge{',
            '  background:#6366f1;color:#fff;border-radius:9999px;',
            '  font-size:.72rem;padding:.1em .45em;flex-shrink:0;}',

            '#ams-job-widget .wg-chevron{flex-shrink:0;transition:transform .2s;}',
            '#ams-job-widget.wg-open .wg-chevron{transform:rotate(180deg);}',

            '#ams-job-widget .wg-body{display:none;}',
            '#ams-job-widget.wg-open .wg-body{display:block;}',

            '#ams-job-widget .wg-row{',
            '  display:flex;align-items:center;gap:.5rem;',
            '  padding:.6rem 1rem;',
            '  border-top:1px solid rgba(255,255,255,.06);}',

            '#ams-job-widget .wg-row-icon{flex-shrink:0;}',
            '#ams-job-widget .wg-row-info{flex:1;min-width:0;}',
            '#ams-job-widget .wg-row-name{font-weight:600;',
            '  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
            '#ams-job-widget .wg-row-meta{opacity:.55;font-size:.78rem;margin-top:.1rem;}',

            '#ams-job-widget .wg-view{',
            '  color:#818cf8;text-decoration:none;font-size:.8rem;flex-shrink:0;}',
            '#ams-job-widget .wg-view:hover{text-decoration:underline;}',

            '#ams-job-widget .wg-dismiss{',
            '  background:none;border:none;color:rgba(255,255,255,.35);',
            '  cursor:pointer;padding:0 0 0 .35rem;font-size:1rem;line-height:1;flex-shrink:0;}',
            '#ams-job-widget .wg-dismiss:hover{color:rgba(255,255,255,.7);}',

            '.wg-done{color:#34d399;}.wg-fail{color:#f87171;}'
        ].join('');
        document.head.appendChild(s);
    }

    function _esc(str) {
        return String(str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function _elapsed(ms) {
        var s = Math.floor((Date.now() - ms) / 1000);
        return s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + (s % 60) + 's';
    }

    // Format a duration in milliseconds as a human-readable string
    function _durationStr(ms) {
        var s = Math.max(0, Math.floor(ms / 1000));
        return s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + (s % 60) + 's';
    }

    function _isStudent() {
        var body = document.body;
        return body && body.getAttribute('data-user-role') === 'student';
    }

    function _isReleased(assignmentId) {
        return window.AMS_RELEASED_AIDS && window.AMS_RELEASED_AIDS.indexOf(assignmentId) !== -1;
    }

    function _rowHtml(job) {
        var icon, meta, action, dismissBtn = '';

        if (job.status === 'processing') {
            icon   = '<div class="wg-spinner wg-row-icon"></div>';
            meta   = 'Running &middot; ' + _elapsed(job.startedAt);
            action = '';
        } else if (job.status === 'completed') {
            icon         = '<span class="wg-done wg-row-icon">&#10003;</span>';
            meta         = 'Done in ' + _durationStr(job.duration || 0);
            action       = (_isStudent() && !_isReleased(job.assignmentId))
                               ? '<span style="color:#818cf8;font-size:.8rem;">Awaiting release</span>'
                               : '<a href="/runs/' + _esc(job.runId) + '" class="wg-view">View &rarr;</a>';
            dismissBtn   = '<button class="wg-dismiss" data-dismiss="' + _esc(job.jobId) + '" title="Dismiss">&times;</button>';
        } else {
            icon         = '<span class="wg-fail wg-row-icon">&#10007;</span>';
            meta         = 'Failed after ' + _durationStr(job.duration || 0);
            action       = job.error
                               ? '<span class="wg-fail" style="font-size:.75rem;max-width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block" title="' + _esc(job.error) + '">' + _esc(job.error.substring(0, 30)) + '</span>'
                               : '';
            dismissBtn   = '<button class="wg-dismiss" data-dismiss="' + _esc(job.jobId) + '" title="Dismiss">&times;</button>';
        }

        return '<div class="wg-row">' +
                   icon +
                   '<div class="wg-row-info">' +
                     '<div class="wg-row-name">' + _esc(job.label) + '</div>' +
                     '<div class="wg-row-meta">' + meta + '</div>' +
                   '</div>' +
                   action +
                   dismissBtn +
               '</div>';
    }

    function _render() {
        _ensureStyles();

        if (jobs.length === 0) {
            if (_widgetEl) { _widgetEl.remove(); _widgetEl = null; }
            return;
        }

        // Create element once; reuse thereafter
        if (!_widgetEl || !document.body.contains(_widgetEl)) {
            _widgetEl = document.createElement('div');
            _widgetEl.id = 'ams-job-widget';
            document.body.appendChild(_widgetEl);
            // Single delegated click listener on the outer element
            _widgetEl.addEventListener('click', _handleClick);
        }

        var active = _activeJobs();

        // Header shows the most recently added active job, else latest overall
        var headerJob = active.length > 0
            ? active[active.length - 1]
            : jobs[jobs.length - 1];

        var headerIcon = active.length > 0
            ? '<div class="wg-spinner"></div>'
            : '<span class="wg-done">&#10003;</span>';

        var badge = active.length > 0
            ? '<span class="wg-badge">' + active.length + '</span>'
            : '';

        var chevron = '<svg class="wg-chevron" width="11" height="11" viewBox="0 0 12 12" fill="none">' +
                      '<path d="M2 4l4 4 4-4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

        var rows = jobs.map(_rowHtml).join('');

        _widgetEl.innerHTML =
            '<div class="wg-header">' +
                headerIcon +
                '<span class="wg-title">' + _esc(headerJob ? headerJob.label : '') + '</span>' +
                badge +
                chevron +
            '</div>' +
            '<div class="wg-body">' + rows + '</div>';

        if (_expanded) _widgetEl.classList.add('wg-open');
        else           _widgetEl.classList.remove('wg-open');
    }

    function _handleClick(e) {
        // Dismiss button
        var dismissBtn = e.target.closest
            ? e.target.closest('[data-dismiss]')
            : (e.target.dataset && e.target.dataset.dismiss ? e.target : null);

        if (dismissBtn) {
            e.stopPropagation();
            _dismissJob(dismissBtn.getAttribute('data-dismiss'));
            return;
        }

        // Links — let them navigate normally
        if (e.target.tagName === 'A' || (e.target.closest && e.target.closest('a'))) return;

        // Header click — toggle expand/collapse
        _expanded = !_expanded;
        if (_widgetEl) {
            if (_expanded) _widgetEl.classList.add('wg-open');
            else           _widgetEl.classList.remove('wg-open');
        }
    }

    // ── Polling ───────────────────────────────────────────────────────

    function _poll(jobId) {
        if (_polling[jobId]) return;  // already running
        _polling[jobId] = true;

        var delay = POLL_INITIAL_MS;

        function tick() {
            fetch('/api/jobs/' + encodeURIComponent(jobId))
                .then(function (res) {
                    if (!res.ok) throw new Error('HTTP ' + res.status);
                    return res.json();
                })
                .then(function (data) {
                    if (data.status === 'processing') {
                        _render();  // refresh elapsed time display
                        delay = Math.min(delay * POLL_BACKOFF, POLL_MAX_MS);
                        setTimeout(tick, delay);
                        return;
                    }
                    _polling[jobId] = false;
                    var _now = Date.now();
                    var _job = jobs.filter(function (j) { return j.jobId === jobId; })[0];
                    var _dur = _job ? (_now - _job.startedAt) : 0;
                    if (data.status === 'completed') {
                        _updateJob(jobId, { status: 'completed', completedAt: _now, duration: _dur });
                        _expanded = true;
                    } else {
                        _updateJob(jobId, { status: 'failed', completedAt: _now, duration: _dur,
                                            error: data.error || 'Unknown error' });
                        _expanded = true;
                    }
                })
                .catch(function (err) {
                    _polling[jobId] = false;
                    var _now = Date.now();
                    var _job = jobs.filter(function (j) { return j.jobId === jobId; })[0];
                    var _dur = _job ? (_now - _job.startedAt) : 0;
                    _updateJob(jobId, { status: 'failed', completedAt: _now, duration: _dur,
                                        error: err.message });
                    _expanded = true;
                });
        }

        setTimeout(tick, delay);
        _render();
    }

    // ── Form intercept ────────────────────────────────────────────────

    function _interceptForm(formId) {
        var form = document.getElementById(formId);
        if (!form) return;

        form.addEventListener('submit', function (e) {
            e.preventDefault();

            // Enforce maximum of 3 concurrent jobs
            if (_activeJobs().length >= 3) {
                alert('Maximum 3 submissions can run at the same time. Please wait for one to complete before submitting another.');
                return;
            }

            var fd = new FormData(form);
            var studentId    = (fd.get('student_id')    || '').toString().trim();
            var assignmentId = (fd.get('assignment_id') || '').toString().trim();
            var label = studentId
                ? 'Single: ' + studentId
                : (assignmentId ? 'Batch: ' + assignmentId : 'Batch');

            fetch(form.action || window.location.pathname, { method: 'POST', body: fd })
                .then(function (res) {
                    if (res.status === 202) return res.json();
                    // Validation error — re-render the page with flash messages
                    return res.text().then(function (html) {
                        document.open(); document.write(html); document.close();
                        return null;
                    });
                })
                .then(function (data) {
                    if (!data) return;
                    _addJob(data.job_id, data.run_id, label, assignmentId);
                    _poll(data.job_id);
                    window.location.href = '/';
                })
                .catch(function (err) {
                    alert('Submission failed: ' + err.message);
                });
        });
    }

    // ── Bootstrap ─────────────────────────────────────────────────────

    document.addEventListener('DOMContentLoaded', function () {
        // Restore any jobs that were still in-flight from a previous page
        _activeJobs().forEach(function (job) { _poll(job.jobId); });
        _render();

        // Refresh the running-time display every 5 seconds without needing a poll
        setInterval(function () { if (_activeJobs().length > 0) _render(); }, 5000);

        // Bind form handlers (no-ops if forms don't exist on this page)
        _interceptForm('markForm');
        _interceptForm('batchForm');
    });
})();

