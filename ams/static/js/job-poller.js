/**
 * AMS Job Widget - non-blocking floating job status tray.
 *
 * Submits mark/batch forms and rerun forms via fetch(), tracks background jobs
 * in sessionStorage, and renders a collapsible widget that persists across
 * navigation.
 */
(function () {
    'use strict';

    var POLL_INITIAL_MS = 2000;
    var POLL_MAX_MS = 10000;
    var POLL_BACKOFF = 1.3;
    var STORAGE_KEY = 'ams_jobs';

    var jobs = _loadJobs();
    var _widgetEl = null;
    var _expanded = false;
    var _polling = {};

    function _loadJobs() {
        try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]'); }
        catch (e) { return []; }
    }

    function _saveJobs() {
        try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(jobs)); }
        catch (e) {}
    }

    function _addJob(jobId, runId, label, assignmentId, viewUrl, refreshUrl) {
        jobs.push({
            jobId: jobId,
            runId: runId,
            label: label,
            assignmentId: assignmentId || '',
            viewUrl: viewUrl || '',
            refreshUrl: refreshUrl || '',
            autoRefreshed: false,
            status: 'processing',
            startedAt: Date.now(),
            completedAt: null,
            error: null
        });
        _saveJobs();
        _render();
    }

    function _updateJob(jobId, patch) {
        for (var i = 0; i < jobs.length; i++) {
            if (jobs[i].jobId === jobId) {
                var key;
                for (key in patch) jobs[i][key] = patch[key];
                break;
            }
        }
        _saveJobs();
        _render();
    }

    function _dismissJob(jobId) {
        jobs = jobs.filter(function (job) { return job.jobId !== jobId; });
        _saveJobs();
        _render();
    }

    function _activeJobs() {
        return jobs.filter(function (job) { return job.status === 'processing'; });
    }

    function _ensureStyles() {
        // Styles moved to static/css/pages/job-widget.css - loaded globally via base.html.
        // This stub is kept so call sites below do not break.
    }

    function _esc(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function _elapsed(startedAt) {
        var seconds = Math.floor((Date.now() - startedAt) / 1000);
        return seconds < 60 ? seconds + 's' : Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
    }

    function _durationStr(ms) {
        var seconds = Math.max(0, Math.floor(ms / 1000));
        return seconds < 60 ? seconds + 's' : Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
    }

    function _isStudent() {
        var body = document.body;
        return body && body.getAttribute('data-user-role') === 'student';
    }

    function _isReleased(assignmentId) {
        return window.AMS_RELEASED_AIDS && window.AMS_RELEASED_AIDS.indexOf(assignmentId) !== -1;
    }

    function _normalizePath(url) {
        if (!url) return '';
        try { return new URL(url, window.location.origin).pathname; }
        catch (e) { return String(url); }
    }

    function _maybeAutoRefresh(jobId) {
        var job = jobs.filter(function (item) { return item.jobId === jobId; })[0];
        if (!job || !job.refreshUrl || job.autoRefreshed) return;
        if (_normalizePath(job.refreshUrl) !== window.location.pathname) return;
        _updateJob(jobId, { autoRefreshed: true });
        window.setTimeout(function () { window.location.reload(); }, 500);
    }

    function _rowHtml(job) {
        var icon;
        var meta;
        var action;
        var dismissBtn = '';
        var viewUrl = job.viewUrl || '/runs/' + encodeURIComponent(job.runId || '');

        if (job.status === 'processing') {
            icon = '<div class="wg-spinner wg-row-icon"></div>';
            meta = 'Running &middot; ' + _elapsed(job.startedAt);
            action = '';
        } else if (job.status === 'completed') {
            icon = '<span class="wg-done wg-row-icon">&#10003;</span>';
            meta = 'Done in ' + _durationStr(job.duration || 0);
            action = (_isStudent() && !_isReleased(job.assignmentId))
                ? '<span style="color:#818cf8;font-size:.8rem;">Awaiting release</span>'
                : '<a href="' + _esc(viewUrl) + '" class="wg-view">View &rarr;</a>';
            dismissBtn = '<button class="wg-dismiss" data-dismiss="' + _esc(job.jobId) + '" title="Dismiss">&times;</button>';
        } else {
            icon = '<span class="wg-fail wg-row-icon">&#10007;</span>';
            meta = 'Failed after ' + _durationStr(job.duration || 0);
            action = job.error
                ? '<span class="wg-fail" style="font-size:.75rem;max-width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block" title="' + _esc(job.error) + '">' + _esc(job.error.substring(0, 30)) + '</span>'
                : '';
            dismissBtn = '<button class="wg-dismiss" data-dismiss="' + _esc(job.jobId) + '" title="Dismiss">&times;</button>';
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
            if (_widgetEl) {
                _widgetEl.remove();
                _widgetEl = null;
            }
            return;
        }

        if (!_widgetEl || !document.body.contains(_widgetEl)) {
            _widgetEl = document.createElement('div');
            _widgetEl.id = 'ams-job-widget';
            document.body.append(_widgetEl);
            _widgetEl.addEventListener('click', _handleClick);
        }

        var active = _activeJobs();
        var headerJob = active.length > 0 ? active[active.length - 1] : jobs[jobs.length - 1];
        var headerIcon = active.length > 0 ? '<div class="wg-spinner"></div>' : '<span class="wg-done">&#10003;</span>';
        var badge = active.length > 0 ? '<span class="wg-badge">' + active.length + '</span>' : '';
        var chevron = '<svg class="wg-chevron" width="11" height="11" viewBox="0 0 12 12" fill="none">' +
            '<path d="M2 4l4 4 4-4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

        _widgetEl.innerHTML =
            '<div class="wg-header">' +
            headerIcon +
            '<span class="wg-title">' + _esc(headerJob ? headerJob.label : '') + '</span>' +
            badge +
            chevron +
            '</div>' +
            '<div class="wg-body">' + jobs.map(_rowHtml).join('') + '</div>';

        if (_expanded) _widgetEl.classList.add('wg-open');
        else _widgetEl.classList.remove('wg-open');
    }

    function _handleClick(e) {
        var dismissBtn = e.target.closest
            ? e.target.closest('[data-dismiss]')
            : (e.target.dataset && e.target.dataset.dismiss ? e.target : null);

        if (dismissBtn) {
            e.stopPropagation();
            _dismissJob(dismissBtn.getAttribute('data-dismiss'));
            return;
        }

        if (e.target.tagName === 'A' || (e.target.closest && e.target.closest('a'))) return;

        _expanded = !_expanded;
        if (_widgetEl) {
            if (_expanded) _widgetEl.classList.add('wg-open');
            else _widgetEl.classList.remove('wg-open');
        }
    }

    function _poll(jobId) {
        if (_polling[jobId]) return;
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
                        _render();
                        delay = Math.min(delay * POLL_BACKOFF, POLL_MAX_MS);
                        window.setTimeout(tick, delay);
                        return;
                    }

                    _polling[jobId] = false;
                    var now = Date.now();
                    var job = jobs.filter(function (item) { return item.jobId === jobId; })[0];
                    var duration = job ? (now - job.startedAt) : 0;
                    var result = data.result || {};

                    if (data.status === 'completed') {
                        _updateJob(jobId, {
                            status: 'completed',
                            completedAt: now,
                            duration: duration,
                            runId: result.run_id || (job && job.runId) || '',
                            viewUrl: result.view_url || (job && job.viewUrl) || '',
                            refreshUrl: result.refresh_url || (job && job.refreshUrl) || ''
                        });
                        _expanded = true;
                        _maybeAutoRefresh(jobId);
                    } else {
                        _updateJob(jobId, {
                            status: 'failed',
                            completedAt: now,
                            duration: duration,
                            error: data.error || 'Unknown error',
                            viewUrl: result.view_url || (job && job.viewUrl) || '',
                            refreshUrl: result.refresh_url || (job && job.refreshUrl) || ''
                        });
                        _expanded = true;
                        _maybeAutoRefresh(jobId);
                    }
                })
                .catch(function (err) {
                    _polling[jobId] = false;
                    var now = Date.now();
                    var job = jobs.filter(function (item) { return item.jobId === jobId; })[0];
                    var duration = job ? (now - job.startedAt) : 0;
                    _updateJob(jobId, {
                        status: 'failed',
                        completedAt: now,
                        duration: duration,
                        error: err.message
                    });
                    _expanded = true;
                });
        }

        window.setTimeout(tick, delay);
        _render();
    }

    function _bindAsyncForm(form, options) {
        if (!form) return;
        if (form.dataset.jobBound === '1') return;
        form.dataset.jobBound = '1';

        form.addEventListener('submit', function (e) {
            e.preventDefault();

            if (_activeJobs().length >= 3) {
                alert('Maximum 3 submissions can run at the same time. Please wait for one to complete before submitting another.');
                return;
            }

            var fd = new FormData(form);
            var studentId = (fd.get('student_id') || '').toString().trim();
            var assignmentId = (fd.get('assignment_id') || '').toString().trim();
            var label = options.labelBuilder
                ? options.labelBuilder(form, fd)
                : (studentId ? 'Single: ' + studentId : (assignmentId ? 'Batch: ' + assignmentId : 'Batch'));

            fetch(form.action || window.location.pathname, {
                method: 'POST',
                body: fd,
                headers: { 'X-AMS-Async': '1' }
            })
                .then(function (res) {
                    if (res.status === 202) return res.json();
                    var contentType = res.headers.get('content-type') || '';
                    if (contentType.indexOf('application/json') !== -1) {
                        return res.json().then(function (data) {
                            throw new Error((data && data.error) || 'Request failed');
                        });
                    }
                    return res.text().then(function (html) {
                        document.open();
                        document.write(html);
                        document.close();
                        return null;
                    });
                })
                .then(function (data) {
                    if (!data) return;
                    _addJob(
                        data.job_id,
                        data.run_id,
                        data.label || label,
                        data.assignment_id || assignmentId,
                        data.view_url || '',
                        data.refresh_url || ''
                    );
                    _poll(data.job_id);
                    if (options.redirectToHome) {
                        window.location.href = '/';
                        return;
                    }
                    if (options.refreshAfterQueue && data.refresh_url) {
                        window.location.href = data.refresh_url;
                    }
                })
                .catch(function (err) {
                    alert((options.errorPrefix || 'Submission failed: ') + err.message);
                });
        });
    }

    function _interceptForm(formId) {
        var form = document.getElementById(formId);
        _bindAsyncForm(form, {
            redirectToHome: true,
            errorPrefix: 'Submission failed: ',
            labelBuilder: function (_form, fd) {
                var studentId = (fd.get('student_id') || '').toString().trim();
                var assignmentId = (fd.get('assignment_id') || '').toString().trim();
                return studentId
                    ? 'Single: ' + studentId
                    : (assignmentId ? 'Batch: ' + assignmentId : 'Batch');
            }
        });
    }

    function _interceptRerunForms() {
        var forms = document.querySelectorAll('form[data-job-form="rerun"]');
        forms.forEach(function (form) {
            _bindAsyncForm(form, {
                redirectToHome: false,
                refreshAfterQueue: true,
                errorPrefix: 'Rerun failed: ',
                labelBuilder: function (_form, fd) {
                    var submissionId = (fd.get('submission_id') || '').toString().trim();
                    var runId = (fd.get('run_id') || '').toString().trim();
                    return submissionId
                        ? 'Rerun: ' + submissionId
                        : (runId ? 'Rerun: ' + runId : 'Rerun submission');
                }
            });
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        _activeJobs().forEach(function (job) { _poll(job.jobId); });
        _render();

        window.setInterval(function () {
            if (_activeJobs().length > 0) _render();
        }, 1000);

        _interceptForm('markForm');
        _interceptForm('batchForm');
        _interceptRerunForms();
    });
})();
