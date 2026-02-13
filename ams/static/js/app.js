/**
 * AMS - Application JavaScript
 * DOM utilities, filtering, clipboard, and interactivity
 */

(function () {
    'use strict';

    // =========================================================================
    // Utilities
    // =========================================================================

    /**
     * Debounce function calls
     */
    function debounce(fn, delay) {
        let timer;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }

    /**
     * Copy text to clipboard
     */
    async function copyToClipboard(text) {
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(text);
                return true;
            }
            // Fallback
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.cssText = 'position:fixed;left:-9999px';
            document.body.appendChild(textarea);
            textarea.select();
            const ok = document.execCommand('copy');
            document.body.removeChild(textarea);
            return ok;
        } catch {
            return false;
        }
    }

    /**
     * Show copy feedback on button
     */
    function showCopyFeedback(btn, success) {
        const original = btn.innerHTML;
        btn.innerHTML = success ? '✓ Copied' : '✗ Failed';
        btn.disabled = true;
        setTimeout(() => {
            btn.innerHTML = original;
            btn.disabled = false;
        }, 1500);
    }

    // =========================================================================
    // Dashboard Filtering
    // =========================================================================

    function initDashboardFilters() {
        const searchInput = document.querySelector('[data-search]');
        const filterBtns = document.querySelectorAll('[data-filter]');
        const items = document.querySelectorAll('[data-filterable]');
        const countDisplay = document.querySelector('[data-count]');

        if (!items.length) return;

        function applyFilters() {
            const searchTerm = searchInput?.value.toLowerCase().trim() || '';
            const activeFilters = {};

            filterBtns.forEach(btn => {
                if (btn.classList.contains('active')) {
                    const [key, val] = btn.dataset.filter.split(':');
                    activeFilters[key] = val;
                }
            });

            let visible = 0;

            items.forEach(item => {
                let show = true;

                // Search
                if (searchTerm) {
                    const text = (item.dataset.search || item.textContent || '').toLowerCase();
                    if (!text.includes(searchTerm)) show = false;
                }

                // Filters
                for (const [key, val] of Object.entries(activeFilters)) {
                    const itemVal = item.dataset[key]?.toLowerCase() || '';
                    if (itemVal !== val.toLowerCase()) show = false;
                }

                item.classList.toggle('hidden', !show);
                if (show) visible++;
            });

            if (countDisplay) {
                countDisplay.textContent = `Showing ${visible} of ${items.length}`;
            }
        }

        searchInput?.addEventListener('input', debounce(applyFilters, 200));

        filterBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                const [key] = btn.dataset.filter.split(':');
                // Deselect others in same group
                filterBtns.forEach(b => {
                    if (b !== btn && b.dataset.filter.startsWith(key + ':')) {
                        b.classList.remove('active');
                    }
                });
                btn.classList.toggle('active');
                applyFilters();
            });
        });

        applyFilters();
    }

    // =========================================================================
    // Findings Filtering
    // =========================================================================

    function initFindingsFilters() {
        const toolbar = document.querySelector('.findings-toolbar');
        if (!toolbar) return;

        const searchInput = toolbar.querySelector('[data-findings-search]');
        const severityBtns = toolbar.querySelectorAll('[data-severity]');
        const componentSelect = toolbar.querySelector('[data-component]');
        const resetBtn = toolbar.querySelector('[data-reset]');
        const findings = document.querySelectorAll('.finding');
        const countDisplay = toolbar.querySelector('[data-findings-count]');

        if (!findings.length) return;

        function applyFilters() {
            const term = searchInput?.value.toLowerCase().trim() || '';
            const activeSeverities = new Set();

            severityBtns.forEach(btn => {
                if (btn.classList.contains('active')) {
                    activeSeverities.add(btn.dataset.severity.toLowerCase());
                }
            });

            const component = componentSelect?.value.toLowerCase() || '';
            let visible = 0;

            findings.forEach(finding => {
                let show = true;

                // Search
                if (term) {
                    const text = finding.textContent.toLowerCase();
                    const ruleId = (finding.dataset.ruleId || '').toLowerCase();
                    if (!text.includes(term) && !ruleId.includes(term)) show = false;
                }

                // Severity
                if (activeSeverities.size > 0) {
                    const sev = (finding.dataset.severity || '').toLowerCase();
                    if (!activeSeverities.has(sev)) show = false;
                }

                // Component
                if (component) {
                    const comp = (finding.dataset.component || '').toLowerCase();
                    if (comp !== component) show = false;
                }

                finding.classList.toggle('hidden', !show);
                if (show) visible++;
            });

            if (countDisplay) {
                countDisplay.textContent = `${visible} events`;
            }
        }

        searchInput?.addEventListener('input', debounce(applyFilters, 200));

        severityBtns.forEach(btn => {
            btn.addEventListener('click', () => {
                btn.classList.toggle('active');
                applyFilters();
            });
        });

        componentSelect?.addEventListener('change', applyFilters);

        resetBtn?.addEventListener('click', () => {
            if (searchInput) searchInput.value = '';
            // On reset, clear all filters (show everything)
            severityBtns.forEach(b => b.classList.remove('active'));
            if (componentSelect) componentSelect.value = '';
            applyFilters();
        });

        applyFilters();
    }

    // =========================================================================
    // Copy Buttons
    // =========================================================================

    function initCopyButtons() {
        document.querySelectorAll('[data-copy]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const selector = btn.dataset.copy;
                const target = document.querySelector(selector);
                if (!target) return;
                const text = target.textContent || target.value || '';
                const ok = await copyToClipboard(text);
                showCopyFeedback(btn, ok);
            });
        });

        document.querySelectorAll('[data-copy-text]').forEach(btn => {
            btn.addEventListener('click', async () => {
                const ok = await copyToClipboard(btn.dataset.copyText);
                showCopyFeedback(btn, ok);
            });
        });
    }

    // =========================================================================
    // Expand/Collapse All
    // =========================================================================

    function initExpandCollapse() {
        const expandBtn = document.querySelector('[data-expand-all]');
        const collapseBtn = document.querySelector('[data-collapse-all]');
        const details = document.querySelectorAll('details');

        expandBtn?.addEventListener('click', () => {
            details.forEach(d => d.open = true);
        });

        collapseBtn?.addEventListener('click', () => {
            details.forEach(d => d.open = false);
        });
    }

    // =========================================================================
    // Alert Dismiss
    // =========================================================================

    function initAlertDismiss() {
        document.querySelectorAll('.alert-close').forEach(btn => {
            btn.addEventListener('click', () => {
                btn.closest('.alert')?.remove();
            });
        });
    }

    // =========================================================================
    // Form Validation Feedback
    // =========================================================================

    function initFormValidation() {
        document.querySelectorAll('form').forEach(form => {
            form.addEventListener('submit', (e) => {
                const inputs = form.querySelectorAll('[required]');
                inputs.forEach(input => {
                    if (!input.value.trim()) {
                        input.classList.add('error');
                    } else {
                        input.classList.remove('error');
                    }
                });
            });
        });
    }

    // =========================================================================
    // Initialize
    // =========================================================================

    function init() {
        initDashboardFilters();
        initFindingsFilters();
        initCopyButtons();
        initExpandCollapse();
        initAlertDismiss();
        initFormValidation();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Export utilities
    window.AMS = { copyToClipboard, debounce };

})();
