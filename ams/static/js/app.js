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
    // Submission Detail Tabs
    // =========================================================================

    function initTabGroups() {
        document.querySelectorAll('[data-tab-group]').forEach(group => {
            const tabs = Array.from(group.querySelectorAll('[data-tab-target]'));
            if (!tabs.length) return;

            const panels = tabs
                .map(tab => document.getElementById(tab.dataset.tabTarget || ''))
                .filter(Boolean);

            function scrollToPageTop() {
                const navbar = document.querySelector('.navbar');
                const hero = group.closest('.submission-detail-shell')?.querySelector('.page-hero');
                const target = hero || group.closest('.submission-detail-shell') || group;
                const offset = (navbar ? navbar.getBoundingClientRect().height : 0) + 16;
                const top = Math.max(0, window.scrollY + target.getBoundingClientRect().top - offset);

                window.scrollTo({
                    top,
                    behavior: 'smooth',
                });
            }

            function activateTab(tabToActivate, { focus = false } = {}) {
                tabs.forEach(tab => {
                    const isActive = tab === tabToActivate;
                    const panelId = tab.dataset.tabTarget || '';
                    const panel = panelId ? document.getElementById(panelId) : null;

                    tab.classList.toggle('is-active', isActive);
                    tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
                    tab.setAttribute('tabindex', isActive ? '0' : '-1');

                    if (panel) {
                        panel.hidden = !isActive;
                    }
                });

                if (focus) {
                    tabToActivate.focus();
                }
            }

            let initialTab = tabs.find(tab => tab.getAttribute('aria-selected') === 'true') || tabs[0];

            if (window.location.hash) {
                const hashMatch = tabs.find(tab => {
                    const panelId = tab.dataset.tabTarget || '';
                    return `#${panelId}` === window.location.hash;
                });
                if (hashMatch) {
                    initialTab = hashMatch;
                }
            }

            tabs.forEach((tab, index) => {
                tab.addEventListener('click', () => {
                    activateTab(tab);
                    const panelId = tab.dataset.tabTarget || '';
                    if (panelId && history.replaceState) {
                        history.replaceState(null, '', `#${panelId}`);
                    }
                    scrollToPageTop();
                });

                tab.addEventListener('keydown', event => {
                    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                    event.preventDefault();

                    let nextIndex = index;
                    if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
                    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
                    if (event.key === 'Home') nextIndex = 0;
                    if (event.key === 'End') nextIndex = tabs.length - 1;

                    activateTab(tabs[nextIndex], { focus: true });
                    const panelId = tabs[nextIndex].dataset.tabTarget || '';
                    if (panelId && history.replaceState) {
                        history.replaceState(null, '', `#${panelId}`);
                    }
                    scrollToPageTop();
                });
            });

            panels.forEach(panel => {
                if (!tabs.some(tab => tab.dataset.tabTarget === panel.id && tab === initialTab)) {
                    panel.hidden = true;
                }
            });

            activateTab(initialTab);
        });
    }

    // =========================================================================
    // Submission Evidence Filters
    // =========================================================================

    function initEvidenceFilters() {
        document.querySelectorAll('[data-evidence-filter-root]').forEach(root => {
            const searchInput = root.querySelector('[data-evidence-search]');
            const statusButtons = Array.from(root.querySelectorAll('[data-evidence-status]'));
            const componentButtons = Array.from(root.querySelectorAll('[data-evidence-component]'));
            const resetButton = root.querySelector('[data-evidence-reset]');
            const items = Array.from(root.querySelectorAll('[data-evidence-item]'));
            const countDisplay = root.querySelector('[data-evidence-count]');
            const emptyState = root.querySelector('[data-evidence-empty]');

            if (!items.length) return;

            function setActive(buttons, activeValue, datasetKey) {
                buttons.forEach(button => {
                    const isActive = (button.dataset[datasetKey] || '') === activeValue;
                    button.classList.toggle('is-active', isActive);
                    button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
                });
            }

            function activeValue(buttons, datasetKey) {
                const selected = buttons.find(button => button.getAttribute('aria-pressed') === 'true');
                return (selected?.dataset[datasetKey] || 'all').toLowerCase();
            }

            function applyFilters() {
                const term = (searchInput?.value || '').toLowerCase().trim();
                const status = activeValue(statusButtons, 'evidenceStatus');
                const component = activeValue(componentButtons, 'evidenceComponent');
                let visibleCount = 0;

                items.forEach(item => {
                    const searchText = (item.dataset.search || item.textContent || '').toLowerCase();
                    const itemStatus = (item.dataset.status || '').toLowerCase();
                    const itemComponent = (item.dataset.component || '').toLowerCase();
                    const matchesTerm = !term || searchText.includes(term);
                    const matchesStatus = status === 'all' || itemStatus === status;
                    const matchesComponent = component === 'all' || itemComponent === component;
                    const isVisible = matchesTerm && matchesStatus && matchesComponent;

                    item.classList.toggle('hidden', !isVisible);
                    if (!isVisible) {
                        item.open = false;
                    }
                    if (isVisible) {
                        visibleCount += 1;
                    }
                });

                if (countDisplay) {
                    countDisplay.textContent = `${visibleCount} result${visibleCount === 1 ? '' : 's'}`;
                }
                if (emptyState) {
                    emptyState.classList.toggle('hidden', visibleCount !== 0);
                }
            }

            searchInput?.addEventListener('input', debounce(applyFilters, 180));

            statusButtons.forEach(button => {
                button.addEventListener('click', () => {
                    setActive(statusButtons, button.dataset.evidenceStatus || 'all', 'evidenceStatus');
                    applyFilters();
                });
            });

            componentButtons.forEach(button => {
                button.addEventListener('click', () => {
                    setActive(componentButtons, button.dataset.evidenceComponent || 'all', 'evidenceComponent');
                    applyFilters();
                });
            });

            resetButton?.addEventListener('click', () => {
                if (searchInput) {
                    searchInput.value = '';
                }
                setActive(statusButtons, 'all', 'evidenceStatus');
                setActive(componentButtons, 'all', 'evidenceComponent');
                applyFilters();
            });

            setActive(statusButtons, 'all', 'evidenceStatus');
            setActive(componentButtons, 'all', 'evidenceComponent');
            applyFilters();
        });
    }

    // =========================================================================
    // Sticky Local Navigation
    // =========================================================================

    function initStickyLocalNavs() {
        const stickyNavs = document.querySelectorAll('[data-sticky-local-nav]');
        if (!stickyNavs.length) return;

        function updateOffsets() {
            const navbar = document.querySelector('.navbar');
            const navTop = navbar ? Math.round(navbar.getBoundingClientRect().height + 16) : 88;
            document.documentElement.style.setProperty('--detail-local-nav-top', `${navTop}px`);
        }

        updateOffsets();
        window.addEventListener('resize', updateOffsets);
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
    // Disclosure Controls
    // =========================================================================

    function initDisclosureControls() {
        document.querySelectorAll('[data-disclosure-action][data-disclosure-target]').forEach(button => {
            button.addEventListener('click', () => {
                const targetId = button.dataset.disclosureTarget || '';
                const action = button.dataset.disclosureAction || '';
                const container = document.getElementById(targetId);
                if (!container) return;

                container.querySelectorAll('details').forEach(detail => {
                    detail.open = action === 'expand';
                });
            });
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
        initTabGroups();
        initEvidenceFilters();
        initStickyLocalNavs();
        initCopyButtons();
        initDisclosureControls();
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
