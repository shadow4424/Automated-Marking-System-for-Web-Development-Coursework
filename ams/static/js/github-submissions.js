/**
 * AMS — GitHub Submissions (OAuth + Gradescope-style)
 *
 * Flow:
 *  1. Radio buttons toggle Upload ↔ GitHub section
 *  2. "Connect to GitHub" is a plain link (OAuth redirect — no AJAX)
 *  3. If already connected: repos load on page init → repo change loads branches
 *  4. Submit button is disabled until a valid repo + branch are selected
 */
(function () {
    'use strict';

    // ── DOM references ──────────────────────────────────────────────
    var methodUpload  = document.getElementById('methodUpload');
    var methodGithub  = document.getElementById('methodGithub');
    var uploadSection = document.getElementById('uploadSection');
    var githubSection = document.getElementById('githubSection');
    var fileInput     = document.getElementById('submission');
    var submitBtn     = document.getElementById('markSubmitBtn');

    // GitHub
    var connectedState    = document.getElementById('githubConnectedState');
    var disconnectedState = document.getElementById('githubDisconnectedState');
    var repoSection       = document.getElementById('githubRepoSection');
    var disconnectBtn     = document.getElementById('githubDisconnectBtn');
    var repoSelect        = document.getElementById('github_repo_select');
    var branchSelect      = document.getElementById('github_branch_select');

    if (!methodUpload || !methodGithub) return;  // not on the mark page

    var activeSource = 'upload';

    // ── Radio button switching ────────────────────────────────────
    function onMethodChange() {
        activeSource = methodGithub.checked ? 'github' : 'upload';

        if (activeSource === 'upload') {
            uploadSection.style.display = '';
            githubSection.style.display = 'none';
            if (fileInput) fileInput.setAttribute('required', '');
            // Remove name attrs from github selects so they don't submit
            if (repoSelect)   repoSelect.removeAttribute('name');
            if (branchSelect) branchSelect.removeAttribute('name');
        } else {
            uploadSection.style.display = 'none';
            githubSection.style.display = '';
            if (fileInput) fileInput.removeAttribute('required');
            // Restore name attrs
            if (repoSelect)   repoSelect.setAttribute('name', 'github_repo');
            if (branchSelect) branchSelect.setAttribute('name', 'github_branch');
        }
        _updateSubmitState();
    }

    methodUpload.addEventListener('change', onMethodChange);
    methodGithub.addEventListener('change', onMethodChange);

    // ── Submit button gating ──────────────────────────────────────
    function _updateSubmitState() {
        if (!submitBtn) return;
        // Don't override sandbox-disabled state
        if (submitBtn.title && submitBtn.title.indexOf('Docker') !== -1) return;

        if (activeSource === 'upload') {
            submitBtn.disabled = false;
            return;
        }

        // GitHub mode — require connection + both repo and branch selected
        var isConnected = !!connectedState;  // element only present when connected
        var repoOk   = repoSelect   && repoSelect.value;
        var branchOk = branchSelect  && branchSelect.value;
        submitBtn.disabled = !(isConnected && repoOk && branchOk);
    }

    // ── Disconnect ────────────────────────────────────────────────
    if (disconnectBtn) {
        disconnectBtn.addEventListener('click', function () {
            fetch('/api/github/disconnect', { method: 'POST' })
                .then(function () { window.location.reload(); })
                .catch(function () { window.location.reload(); });
        });
    }

    // ── Load repositories ─────────────────────────────────────────
    function _loadRepos() {
        if (!repoSelect) return;
        repoSelect.disabled = true;
        repoSelect.innerHTML = '<option value="">Loading repositories…</option>';
        _resetBranch();

        fetch('/api/github/repos')
            .then(function (res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(function (repos) {
                repoSelect.innerHTML = '<option value="">Select a repository…</option>';
                repos.forEach(function (repo) {
                    var opt = document.createElement('option');
                    opt.value = repo.full_name;
                    opt.textContent = repo.full_name + (repo.private ? ' 🔒' : '');
                    if (repo.description) opt.title = repo.description;
                    repoSelect.appendChild(opt);
                });
                repoSelect.disabled = false;
                _updateSubmitState();
            })
            .catch(function (err) {
                repoSelect.innerHTML = '<option value="">Failed to load repos</option>';
                repoSelect.disabled = true;
                console.error('GitHub repos load error:', err);
            });
    }

    // ── Load branches (cascading from repo) ───────────────────────
    function _loadBranches(repoFullName) {
        if (!branchSelect || !repoFullName) return;
        branchSelect.disabled = true;
        branchSelect.innerHTML = '<option value="">Loading branches…</option>';

        fetch('/api/github/repos/' + encodeURI(repoFullName) + '/branches')
            .then(function (res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            })
            .then(function (branches) {
                branchSelect.innerHTML = '<option value="">Select a branch…</option>';
                // Default branch first
                branches.sort(function (a, b) {
                    if (a.is_default) return -1;
                    if (b.is_default) return 1;
                    return a.name.localeCompare(b.name);
                });
                branches.forEach(function (branch) {
                    var opt = document.createElement('option');
                    opt.value = branch.name;
                    opt.textContent = branch.name + (branch.is_default ? ' (default)' : '');
                    branchSelect.appendChild(opt);
                });
                branchSelect.disabled = false;
                // Auto-select default branch
                if (branches.length === 1) {
                    branchSelect.value = branches[0].name;
                } else {
                    var def = branches.find(function (b) { return b.is_default; });
                    if (def) branchSelect.value = def.name;
                }
                _updateSubmitState();
            })
            .catch(function (err) {
                branchSelect.innerHTML = '<option value="">Failed to load branches</option>';
                branchSelect.disabled = true;
                console.error('GitHub branches load error:', err);
                _updateSubmitState();
            });
    }

    function _resetBranch() {
        if (!branchSelect) return;
        branchSelect.innerHTML = '<option value="">Select a branch…</option>';
        branchSelect.disabled = true;
        _updateSubmitState();
    }

    // ── Wire events ───────────────────────────────────────────────
    if (repoSelect) {
        repoSelect.addEventListener('change', function () {
            if (repoSelect.value) {
                _loadBranches(repoSelect.value);
            } else {
                _resetBranch();
            }
        });
    }

    if (branchSelect) {
        branchSelect.addEventListener('change', _updateSubmitState);
    }

    // ── Bootstrap ─────────────────────────────────────────────────
    // If already connected, load repos immediately
    if (connectedState && repoSelect) {
        _loadRepos();
    }

    _updateSubmitState();

})();
