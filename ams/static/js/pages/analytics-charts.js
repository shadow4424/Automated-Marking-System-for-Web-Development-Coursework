    (function () {
        var attentionRows = Array.prototype.slice.call(document.querySelectorAll('.attention-row'));
        var ruleRows = Array.prototype.slice.call(document.querySelectorAll('.js-rule-row'));
        var signalCards = Array.prototype.slice.call(document.querySelectorAll('.js-signal-card'));
        var attentionList = document.getElementById('attention-list');
        var attentionCounter = document.getElementById('attention-visible-count');
        var attentionNoResults = document.getElementById('attention-no-results');
        var banner = document.getElementById('attention-filter-banner');
        var bannerText = document.getElementById('attention-filter-banner-text');
        var exportLink = document.getElementById('needs-attention-export');
        var rulesExport = document.getElementById('rules-export');
        var needsAttentionExportLinks = {
            csv: document.getElementById('needs-attention-export-csv'),
            json: document.getElementById('needs-attention-export-json'),
            txt: document.getElementById('needs-attention-export-txt'),
            pdf: document.getElementById('needs-attention-export-pdf')
        };
        var rulesExportLinks = {
            csv: document.getElementById('rules-export-csv'),
            json: document.getElementById('rules-export-json'),
            txt: document.getElementById('rules-export-txt'),
            pdf: document.getElementById('rules-export-pdf')
        };
        var toggleRules = document.getElementById('toggle-rules');
        var toggleSignals = document.getElementById('toggle-signals');
        var rulesNoResults = document.getElementById('rules-no-results');
        var attentionState = { signalLabel: '', signalRules: [], signalStudents: [] };
        var showAllRules = false;
        var showAllSignals = false;
        var teachingSummaryButton = document.getElementById('generate-teaching-summary');
        var teachingSummaryList = document.getElementById('teaching-summary-list');
        var teachingSummaryStatus = document.getElementById('teaching-summary-status');
        var teachingSummaryFeedback = document.getElementById('teaching-summary-feedback');
        var teachingSummaryHeadline = document.getElementById('teaching-summary-headline');
        var graphConfig = document.getElementById('analytics-js-config');
        var graphData = window.AMS_CHART_DATA || {};
        var tooltip = document.getElementById('analytics-tooltip');
        var studentDrawer = document.getElementById('analytics-student-drawer');
        var studentDrawerTitle = document.getElementById('analytics-drawer-title');
        var studentDrawerBadges = document.getElementById('analytics-drawer-badges');
        var studentDrawerList = document.getElementById('analytics-drawer-list');
        var studentDrawerFilterQueue = document.getElementById('analytics-drawer-filter-queue');
        var scatterQuadrantNote = document.getElementById('graph-scatter-quadrant-note');
        var closeStudentDrawer = document.getElementById('close-analytics-drawer');
        var clearGraphSelectionButton = document.getElementById('analytics-drawer-clear-selection');
        var requirementFocusBanner = document.getElementById('requirement-focus-banner');
        var requirementFocusText = document.getElementById('requirement-focus-text');
        var clearRequirementFocusButton = document.getElementById('clear-requirement-focus');
        var requirementCards = Array.prototype.slice.call(document.querySelectorAll('.analytics-requirement-card'));
        var selectedGraphElement = null;
        var selectedRequirementComponent = '';
        var currentDrawerStudentIds = [];
        var currentDrawerLabel = '';
        var runDetailPattern = graphConfig ? (graphConfig.getAttribute('data-run-detail-pattern') || '') : '';
        var batchReportPattern = graphConfig ? (graphConfig.getAttribute('data-batch-report-pattern') || '') : '';
        var exportLinkBase = exportLink ? (exportLink.getAttribute('href') || '') : '';
        var rulesExportBase = rulesExport ? (rulesExport.getAttribute('href') || '') : '';
        var needsAttentionExportBase = {};
        var rulesExportLinkBase = {};
        Object.keys(needsAttentionExportLinks).forEach(function(key) {
            needsAttentionExportBase[key] = needsAttentionExportLinks[key] ? (needsAttentionExportLinks[key].getAttribute('href') || '') : '';
        });
        Object.keys(rulesExportLinks).forEach(function(key) {
            rulesExportLinkBase[key] = rulesExportLinks[key] ? (rulesExportLinks[key].getAttribute('href') || '') : '';
        });
        var attentionControls = {
            student: document.getElementById('attention-student-search'),
            scoreBand: document.getElementById('attention-score-band'),
            grade: document.getElementById('attention-grade'),
            severity: document.getElementById('attention-severity'),
            confidence: document.getElementById('attention-confidence'),
            reason: document.getElementById('attention-reason'),
            flag: document.getElementById('attention-flag'),
            rule: document.getElementById('attention-rule-filter'),
            sort: document.getElementById('attention-sort')
        };
        var ruleControls = {
            severity: document.getElementById('rule-severity-filter'),
            component: document.getElementById('rule-component-filter'),
            impact: document.getElementById('rule-impact-filter')
        };
        var navbar = document.querySelector('.navbar');
        var localNav = document.getElementById('analytics-local-nav');
        var localNavLinks = Array.prototype.slice.call(document.querySelectorAll('.analytics-local-nav-link'));
        var jumpControls = Array.prototype.slice.call(document.querySelectorAll('[data-analytics-target]'));
        var collapsibleSections = Array.prototype.slice.call(document.querySelectorAll('.analytics-collapse-card'));
        var trackedSections = [
            { id: 'analytics-overview', element: document.getElementById('analytics-overview') },
            { id: 'interactive-graphs', element: document.getElementById('interactive-graphs') },
            { id: 'needs-attention', element: document.getElementById('needs-attention') },
            { id: 'top-rules', element: document.getElementById('top-rules') },
            { id: 'requirement-coverage', element: document.getElementById('requirement-coverage') },
            { id: 'confidence-reliability', element: document.getElementById('confidence-reliability') },
            { id: 'cohort-issues', element: document.getElementById('cohort-issues') },
            { id: 'scoring-sources', element: document.getElementById('scoring-sources') }
        ].filter(function (item) { return !!item.element; });
        var navAliases = {};
        var expansionTargets = {};
        var scrollSyncPending = false;

        function splitList(value) {
            return (value || '').split('|').map(function (item) { return item.trim(); }).filter(Boolean);
        }

        function intersects(a, b) {
            if (!a.length || !b.length) return false;
            for (var i = 0; i < a.length; i += 1) {
                if (b.indexOf(a[i]) !== -1) return true;
            }
            return false;
        }

        function escapeHtml(value) {
            return String(value == null ? '' : value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        function formatPercent(value) {
            if (value === null || value === undefined || value === '') return 'No score';
            var numeric = typeof value === 'number' ? value : parseFloat(value);
            if (!isFinite(numeric)) return 'No score';
            return (Math.round(numeric * 10) / 10) + '%';
        }

        function studentDisplayName(student) {
            if (!student) return 'Unknown student';
            return student.student_name || student.student_id || 'Unknown student';
        }

        function confidenceBadgeClass(value) {
            var level = String(value || '').toLowerCase();
            if (level === 'high') return 'badge-success';
            if (level === 'medium') return 'badge-warning';
            if (level === 'low') return 'badge-danger';
            return 'badge-secondary';
        }

        function studentSubsetExamples(studentIds, limit) {
            return (studentIds || []).slice(0, limit || 3).map(function (studentId) {
                return studentDisplayName(studentSnapshot(studentId));
            });
        }

        function chartEmptyState(message, detail) {
            return '<div class="analytics-graph-empty"><strong style="display:block; margin-bottom:0.35rem; color:var(--color-text);">' +
                escapeHtml(message || 'Chart unavailable') +
                '</strong>' +
                (detail ? '<div>' + escapeHtml(detail) + '</div>' : '') +
                '</div>';
        }

        function graphColor(kind) {
            var palette = {
                success: '#22c55e',
                warning: '#f59e0b',
                danger: '#ef4444',
                info: '#2563eb',
                muted: '#94a3b8',
                ink: '#0f172a'
            };
            return palette[kind] || palette.info;
        }

        function inferSegmentTone(label) {
            var text = String(label || '').toLowerCase();
            if (text.indexOf('met') !== -1 || text.indexOf('full') !== -1 || text.indexOf('high confidence') !== -1 || text.indexOf('fully evaluated') !== -1) return 'success';
            if (text.indexOf('partial') !== -1 || text.indexOf('medium') !== -1 || text.indexOf('skipped') !== -1) return 'warning';
            if (text.indexOf('unmet') !== -1 || text.indexOf('low') !== -1 || text.indexOf('issue') !== -1 || text.indexOf('not analysable') !== -1 || text.indexOf('missing') !== -1) return 'danger';
            if (text.indexOf('not evaluable') !== -1 || text.indexOf('not scored') !== -1) return 'muted';
            return 'info';
        }

        function reportUrlForStudent(student) {
            if (!student || !student.run_id) return '';
            if (student.source_mode === 'batch' && student.submission_id) {
                return batchReportPattern
                    .replace('__RUN_ID__', encodeURIComponent(student.run_id))
                    .replace('__SUBMISSION_ID__', encodeURIComponent(student.submission_id));
            }
            return runDetailPattern.replace('__RUN_ID__', encodeURIComponent(student.run_id));
        }

        function studentSnapshot(studentId) {
            var index = (graphData && graphData.student_index) || {};
            if (index[studentId]) return index[studentId];
            return {
                student_id: studentId,
                student_name: '',
                submission_id: '',
                score_percent: null,
                static_score_percent: null,
                behavioural_score_percent: null,
                grade: 'missing',
                confidence: 'n/a',
                evaluation_state: 'missing_submission',
                severity: 'low',
                manual_review_recommended: false,
                primary_issue: 'No active submission is currently in scope for this student.',
                reason: 'missing submission',
                reason_detail: 'No active submission is currently in scope for this student.',
                flags: ['missing submission'],
                matched_rule_ids: [],
                matched_rule_labels: [],
                run_id: '',
                source_mode: ''
            };
        }

        function setSelectedGraphNode(node) {
            if (selectedGraphElement && selectedGraphElement !== node) {
                selectedGraphElement.classList.remove('is-selected');
            }
            selectedGraphElement = node || null;
            if (selectedGraphElement) selectedGraphElement.classList.add('is-selected');
        }

        function clearSelectedGraphNode() {
            if (!selectedGraphElement) return;
            selectedGraphElement.classList.remove('is-selected');
            selectedGraphElement = null;
        }

        function clearRequirementFocus() {
            selectedRequirementComponent = '';
            if (requirementFocusBanner) requirementFocusBanner.classList.remove('is-visible');
            if (requirementFocusText) requirementFocusText.textContent = '';
            requirementCards.forEach(function (card) {
                card.classList.remove('is-selected');
            });
        }

        function focusRequirement(component, label) {
            if (!component) return;
            selectedRequirementComponent = component;
            requirementCards.forEach(function (card) {
                card.classList.toggle('is-selected', card.dataset.requirementComponent === component);
            });
            if (requirementFocusBanner && requirementFocusText) {
                requirementFocusBanner.classList.add('is-visible');
                requirementFocusText.textContent = label || ('Requirement focus: ' + component.toUpperCase());
            }
        }

        function filterReviewQueueToStudents(studentIds, label) {
            attentionState.signalLabel = label || '';
            attentionState.signalRules = [];
            attentionState.signalStudents = (studentIds || []).map(function (item) { return String(item || '').toLowerCase(); }).filter(Boolean);
            if (!attentionState.signalStudents.length) attentionState.signalLabel = '';
            navigateToSection('needs-attention');
            renderAttention();
        }

        function highlightRuleRow(ruleId) {
            if (!ruleId) return;
            var matchedRow = null;
            ruleRows.forEach(function (row) {
                var isMatch = row.dataset.ruleId === ruleId;
                row.classList.toggle('is-selected', isMatch);
                if (isMatch) matchedRow = row;
            });
            if (!matchedRow) return;
            navigateToSection('top-rules');
            showAllRules = true;
            if (ruleControls.component) ruleControls.component.value = matchedRow.dataset.component || '';
            renderRuleRows();
            window.requestAnimationFrame(function () {
                matchedRow.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            });
        }

        function tooltipText(title, lines) {
            return '<strong>' + escapeHtml(title) + '</strong>' + (lines || []).filter(Boolean).map(function (line) {
                return '<div>' + escapeHtml(line) + '</div>';
            }).join('');
        }

        function showTooltip(event, html) {
            if (!tooltip || !html) return;
            tooltip.innerHTML = html;
            tooltip.classList.add('is-visible');
            tooltip.setAttribute('aria-hidden', 'false');
            positionTooltip(event);
        }

        function positionTooltip(event) {
            if (!tooltip || !tooltip.classList.contains('is-visible')) return;
            var x = (event.clientX || 0) + 16;
            var y = (event.clientY || 0) + 16;
            var width = tooltip.offsetWidth || 220;
            var height = tooltip.offsetHeight || 90;
            if (x + width > window.innerWidth - 12) x = window.innerWidth - width - 12;
            if (y + height > window.innerHeight - 12) y = window.innerHeight - height - 12;
            tooltip.style.left = x + 'px';
            tooltip.style.top = y + 'px';
        }

        function hideTooltip() {
            if (!tooltip) return;
            tooltip.classList.remove('is-visible');
            tooltip.setAttribute('aria-hidden', 'true');
        }

        function bindTooltip(element, html) {
            if (!element) return;
            element.addEventListener('mouseenter', function (event) { showTooltip(event, html); });
            element.addEventListener('mousemove', positionTooltip);
            element.addEventListener('mouseleave', hideTooltip);
            element.addEventListener('focus', function (event) { showTooltip(event, html); });
            element.addEventListener('blur', hideTooltip);
        }

        function renderDrawerRows(studentIds) {
            if (!studentDrawerList) return;
            var rows = (studentIds || []).map(studentSnapshot);
            if (!rows.length) {
                studentDrawerList.innerHTML = '<div class="analytics-drawer-empty">No student records match this graph selection.</div>';
                return;
            }
            studentDrawerList.innerHTML = rows.map(function (student) {
                var metrics = [
                    '<span class="badge badge-secondary">Overall ' + escapeHtml(formatPercent(student.score_percent)) + '</span>',
                    '<span class="badge ' + confidenceBadgeClass(student.confidence) + '">' + escapeHtml((student.confidence || 'n/a') + ' confidence') + '</span>'
                ];
                if (student.static_score_percent !== null && student.static_score_percent !== undefined) {
                    metrics.push('<span class="analytics-chip">Static ' + escapeHtml(formatPercent(student.static_score_percent)) + '</span>');
                }
                if (student.behavioural_score_percent !== null && student.behavioural_score_percent !== undefined) {
                    metrics.push('<span class="analytics-chip">Functional ' + escapeHtml(formatPercent(student.behavioural_score_percent)) + '</span>');
                }
                if (student.grade) {
                    metrics.push('<span class="analytics-chip">' + escapeHtml(student.grade) + '</span>');
                }
                var reportUrl = reportUrlForStudent(student);
                var reportAction = reportUrl
                    ? '<a class="btn btn-secondary btn-sm analytics-report-link" href="' + escapeHtml(reportUrl) + '">Open report</a>'
                    : '<div class="analytics-helper analytics-report-link">Report unavailable</div>';
                var identityLine = student.student_name && student.student_id && student.student_name !== student.student_id
                    ? escapeHtml(student.student_id + ' - ' + (student.submission_id || 'No submission id'))
                    : escapeHtml(student.submission_id || student.student_id || 'No submission id');
                return '' +
                    '<article class="analytics-student-card">' +
                        '<div class="analytics-student-meta">' +
                            '<div>' +
                                '<strong>' + escapeHtml(studentDisplayName(student)) + '</strong>' +
                                '<div class="analytics-table-note">' + identityLine + '</div>' +
                            '</div>' +
                            '<span class="analytics-helper">' + escapeHtml((student.evaluation_state || '').replace(/_/g, ' ')) + '</span>' +
                        '</div>' +
                        '<div class="analytics-student-card-metrics">' + metrics.join('') + '</div>' +
                        '<p class="analytics-student-primary-issue">' + escapeHtml(student.primary_issue || student.reason_detail || student.reason || 'No additional detail recorded.') + '</p>' +
                        '<div class="analytics-student-card-actions">' +
                            reportAction +
                            (student.manual_review_recommended ? '<span class="analytics-helper">Manual review recommended</span>' : '') +
                        '</div>' +
                    '</article>';
            }).join('');
        }

        function openStudentDrawer(options) {
            if (!studentDrawer) return;
            currentDrawerStudentIds = (options && options.studentIds) ? options.studentIds.slice() : [];
            currentDrawerLabel = options && options.label ? options.label : 'Student subset';
            if (studentDrawerTitle) studentDrawerTitle.textContent = currentDrawerLabel;
            if (studentDrawerBadges) {
                studentDrawerBadges.innerHTML =
                    '<span class="badge badge-secondary">' + currentDrawerStudentIds.length + ' student' + (currentDrawerStudentIds.length === 1 ? '' : 's') + '</span>' +
                    ((options && options.badgeText) ? '<span class="badge badge-info">' + escapeHtml(options.badgeText) + '</span>' : '');
            }
            renderDrawerRows(currentDrawerStudentIds);
            studentDrawer.classList.add('is-open');
            studentDrawer.setAttribute('aria-hidden', 'false');
        }

        function closeDrawer() {
            if (!studentDrawer) return;
            studentDrawer.classList.remove('is-open');
            studentDrawer.setAttribute('aria-hidden', 'true');
        }

        function clearGraphSelection() {
            clearSelectedGraphNode();
            clearRequirementFocus();
            renderScatterQuadrantNote(null);
            closeDrawer();
        }

        function chartButton(title, subtitle, count, width, tone, tooltipHtml) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'analytics-bar-button';
            button.innerHTML = '' +
                '<div class="analytics-stage-meta"><strong>' + escapeHtml(title) + '</strong><span>' + escapeHtml(String(count)) + '</span></div>' +
                (subtitle ? '<div class="analytics-table-note">' + escapeHtml(subtitle) + '</div>' : '') +
                '<div class="analytics-bar-visual"><span style="width:' + width + '%; background:' + graphColor(tone) + ';"></span></div>';
            if (tooltipHtml) bindTooltip(button, tooltipHtml);
            return button;
        }

        function createSvgText(x, y, value, className, anchor, rotate) {
            var text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            text.setAttribute('x', x);
            text.setAttribute('y', y);
            text.setAttribute('class', className || 'analytics-chart-text');
            if (anchor) text.setAttribute('text-anchor', anchor);
            if (rotate) text.setAttribute('transform', 'rotate(' + rotate + ' ' + x + ' ' + y + ')');
            text.textContent = value;
            return text;
        }

        function appendReferenceHoverLine(svg, x1, y1, x2, y2, html) {
            var hoverLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            hoverLine.setAttribute('x1', x1);
            hoverLine.setAttribute('x2', x2);
            hoverLine.setAttribute('y1', y1);
            hoverLine.setAttribute('y2', y2);
            hoverLine.setAttribute('stroke', 'rgba(15, 23, 42, 0.001)');
            hoverLine.setAttribute('stroke-width', '14');
            hoverLine.setAttribute('pointer-events', 'stroke');
            bindTooltip(hoverLine, html);
            svg.appendChild(hoverLine);
        }

        function renderScatterQuadrantNote(region) {
            if (!scatterQuadrantNote) return;
            if (!region) {
                scatterQuadrantNote.innerHTML = 'Click a quadrant to display what that region represents.';
                return;
            }
            scatterQuadrantNote.innerHTML = '<strong>' + escapeHtml(region.title || 'Quadrant guidance') + '</strong>' +
                (region.lines || []).map(function (line) {
                    return '<div>' + escapeHtml(line) + '</div>';
                }).join('');
        }

        function appendQuadrantHoverRegion(svg, region) {
            var rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            rect.setAttribute('x', region.x);
            rect.setAttribute('y', region.y);
            rect.setAttribute('width', Math.max(region.width, 0));
            rect.setAttribute('height', Math.max(region.height, 0));
            rect.setAttribute('fill', 'rgba(37, 99, 235, 0.001)');
            rect.setAttribute('stroke', 'none');
            rect.setAttribute('pointer-events', 'all');
            rect.setAttribute('tabindex', '0');
            rect.setAttribute('role', 'button');
            rect.addEventListener('mouseenter', function () {
                rect.setAttribute('fill', 'rgba(37, 99, 235, 0.06)');
            });
            rect.addEventListener('mouseleave', function () {
                rect.setAttribute('fill', 'rgba(37, 99, 235, 0.001)');
            });
            rect.addEventListener('focus', function () {
                rect.setAttribute('fill', 'rgba(37, 99, 235, 0.06)');
            });
            rect.addEventListener('blur', function () {
                rect.setAttribute('fill', 'rgba(37, 99, 235, 0.001)');
            });
            rect.addEventListener('click', function () {
                renderScatterQuadrantNote(region);
            });
            rect.addEventListener('keydown', function (event) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    renderScatterQuadrantNote(region);
                }
            });
            svg.appendChild(rect);
        }

        function niceIntegerTicks(maxValue, maxSteps) {
            var upper = Math.max(1, Math.ceil(maxValue || 0));
            var targetSteps = Math.max(2, maxSteps || 5);
            if (upper <= targetSteps) {
                return Array.from({ length: upper + 1 }, function (_, value) { return value; });
            }
            var roughStep = upper / targetSteps;
            var magnitude = Math.pow(10, Math.floor(Math.log10(roughStep)));
            var normalised = roughStep / magnitude;
            var niceMultiplier = 10;
            if (normalised <= 1.5) niceMultiplier = 1;
            else if (normalised <= 3) niceMultiplier = 2;
            else if (normalised <= 7) niceMultiplier = 5;
            var step = Math.max(1, niceMultiplier * magnitude);
            var ceiling = Math.ceil(upper / step) * step;
            var ticks = [0];
            for (var value = step; value < ceiling; value += step) ticks.push(value);
            if (ticks[ticks.length - 1] !== ceiling) ticks.push(ceiling);
            return ticks;
        }

        function getNavbarOffset() {
            return navbar ? navbar.offsetHeight : 0;
        }

        function getLocalNavHeight() {
            return localNav ? localNav.offsetHeight : 0;
        }

        function setAnalyticsOffsets() {
            var navTop = getNavbarOffset() + 10;
            var anchorOffset = getNavbarOffset() + getLocalNavHeight() + 20;
            document.documentElement.style.setProperty('--analytics-local-nav-top', navTop + 'px');
            document.documentElement.style.setProperty('--analytics-anchor-offset', anchorOffset + 'px');
        }

        function setActiveNav(targetId) {
            if (!targetId) return;
            localNavLinks.forEach(function (link) {
                var isActive = link.dataset.analyticsTarget === targetId;
                link.classList.toggle('is-active', isActive);
                if (isActive) {
                    link.setAttribute('aria-current', 'location');
                } else {
                    link.removeAttribute('aria-current');
                }
            });
        }

        function getSectionTop(element) {
            return element.getBoundingClientRect().top + window.scrollY;
        }

        function syncActiveNav() {
            if (!trackedSections.length) return;
            setAnalyticsOffsets();
            var threshold = window.scrollY + getNavbarOffset() + getLocalNavHeight() + 32;
            var activeId = trackedSections[0].id;
            trackedSections.forEach(function (item) {
                if (getSectionTop(item.element) <= threshold) activeId = item.id;
            });
            if ((window.innerHeight + window.scrollY) >= (document.documentElement.scrollHeight - 8)) {
                activeId = trackedSections[trackedSections.length - 1].id;
            }
            setActiveNav(activeId);
        }

        function requestActiveNavSync() {
            if (scrollSyncPending) return;
            scrollSyncPending = true;
            window.requestAnimationFrame(function () {
                scrollSyncPending = false;
                syncActiveNav();
            });
        }

        function expandTarget(targetId) {
            var target = document.getElementById(targetId);
            if (target && target.tagName.toLowerCase() === 'details' && !target.open) {
                target.open = true;
            }
            return target;
        }

        function navigateToSection(targetId, options) {
            if (!targetId) return;
            var target = document.getElementById(targetId);
            if (!target) return;
            var navTargetId = navAliases[targetId] || targetId;
            expandTarget(targetId);
            if (expansionTargets[targetId]) {
                expansionTargets[targetId].forEach(expandTarget);
            }
            window.requestAnimationFrame(function () {
                setAnalyticsOffsets();
                var top = getSectionTop(target) - (getNavbarOffset() + getLocalNavHeight() + 20);
                window.scrollTo({
                    top: top > 0 ? top : 0,
                    behavior: options && options.instant ? 'auto' : 'smooth'
                });
                setActiveNav(navTargetId);
                if (!options || options.updateHash !== false) {
                    if (window.history && window.history.replaceState) {
                        window.history.replaceState(null, '', '#' + targetId);
                    }
                }
            });
        }

        function updateAttentionExportLink() {
            var params = new URLSearchParams();
            if (attentionControls.student.value.trim()) params.set('student', attentionControls.student.value.trim());
            if (attentionControls.scoreBand.value) params.set('score_band', attentionControls.scoreBand.value);
            if (attentionControls.grade.value) params.set('grade', attentionControls.grade.value);
            if (attentionControls.severity.value) params.set('severity', attentionControls.severity.value);
            if (attentionControls.confidence.value) params.set('confidence', attentionControls.confidence.value);
            if (attentionControls.reason.value) params.set('reason', attentionControls.reason.value);
            if (attentionControls.flag.value) params.set('flag', attentionControls.flag.value);
            if (attentionControls.rule.value.trim()) params.set('rule', attentionControls.rule.value.trim());
            if (attentionControls.sort.value) params.set('sort', attentionControls.sort.value);
            if (attentionState.signalRules.length) params.set('signal_rules', attentionState.signalRules.join(','));
            if (attentionState.signalStudents.length) params.set('signal_students', attentionState.signalStudents.join(','));
            var queryStr = params.toString() ? ('?' + params.toString()) : '';
            // Update all format links
            Object.keys(needsAttentionExportLinks).forEach(function(key) {
                if (needsAttentionExportLinks[key]) {
                    needsAttentionExportLinks[key].href = needsAttentionExportBase[key] + queryStr;
                }
            });
        }

        function updateRulesExportLink() {
            var params = new URLSearchParams();
            if (ruleControls.severity && ruleControls.severity.value) params.set('severity', ruleControls.severity.value);
            if (ruleControls.component && ruleControls.component.value) params.set('component', ruleControls.component.value);
            if (ruleControls.impact && ruleControls.impact.value) params.set('impact_type', ruleControls.impact.value);
            var queryStr = params.toString() ? ('?' + params.toString()) : '';
            // Update all format links
            Object.keys(rulesExportLinks).forEach(function(key) {
                if (rulesExportLinks[key]) {
                    rulesExportLinks[key].href = rulesExportLinkBase[key] + queryStr;
                }
            });
        }

        function matchesAttentionRow(row) {
            var dataset = row.dataset;
            var studentValue = (attentionControls.student.value || '').trim().toLowerCase();
            var ruleValue = (attentionControls.rule.value || '').trim();
            var rowRules = splitList(dataset.rules);
            var rowFlags = splitList(dataset.flags);
            var signalMatch = true;
            if (attentionState.signalRules.length || attentionState.signalStudents.length) {
                signalMatch = attentionState.signalStudents.indexOf(dataset.student) !== -1 || intersects(attentionState.signalRules, rowRules);
            }
            return (!studentValue || dataset.student.indexOf(studentValue) !== -1) &&
                (!attentionControls.scoreBand.value || dataset.scoreBand === attentionControls.scoreBand.value) &&
                (!attentionControls.grade.value || dataset.grade === attentionControls.grade.value) &&
                (!attentionControls.severity.value || dataset.severity === attentionControls.severity.value) &&
                (!attentionControls.confidence.value || dataset.confidence === attentionControls.confidence.value) &&
                (!attentionControls.reason.value || dataset.reason === attentionControls.reason.value) &&
                (!attentionControls.flag.value || rowFlags.indexOf(attentionControls.flag.value) !== -1) &&
                (!ruleValue || rowRules.indexOf(ruleValue) !== -1) &&
                signalMatch;
        }

        function sortAttentionRows(rows) {
            var severityRank = { high: 0, medium: 1, low: 2 };
            var sorter = attentionControls.sort.value || 'severity';
            rows.sort(function (a, b) {
                var aScore = a.dataset.score === '' ? -1 : parseFloat(a.dataset.score);
                var bScore = b.dataset.score === '' ? -1 : parseFloat(b.dataset.score);
                if (sorter === 'score_asc') return aScore - bScore || a.dataset.student.localeCompare(b.dataset.student);
                if (sorter === 'score_desc') return bScore - aScore || a.dataset.student.localeCompare(b.dataset.student);
                if (sorter === 'grade') return (parseInt(b.dataset.gradeRank || '0', 10) - parseInt(a.dataset.gradeRank || '0', 10)) || a.dataset.student.localeCompare(b.dataset.student);
                return (severityRank[a.dataset.severity] || 9) - (severityRank[b.dataset.severity] || 9) || aScore - bScore || a.dataset.student.localeCompare(b.dataset.student);
            });
        }

        function renderAttention() {
            if (!attentionRows.length || !attentionList) return;
            var visibleRows = attentionRows.filter(matchesAttentionRow);
            sortAttentionRows(visibleRows);
            attentionRows.forEach(function (row) { row.style.display = 'none'; });
            visibleRows.forEach(function (row) {
                row.style.display = '';
                attentionList.appendChild(row);
            });
            if (attentionCounter) attentionCounter.textContent = String(visibleRows.length);
            if (attentionNoResults) attentionNoResults.style.display = visibleRows.length ? 'none' : '';
            if (banner && bannerText) {
                if (attentionState.signalLabel) {
                    banner.classList.add('is-visible');
                    bannerText.textContent = 'Filter: ' + attentionState.signalLabel;
                } else {
                    banner.classList.remove('is-visible');
                    bannerText.textContent = '';
                }
            }
            updateAttentionExportLink();
        }

        function matchesRuleRow(row) {
            return (!ruleControls.severity || !ruleControls.severity.value || row.dataset.severity === ruleControls.severity.value) &&
                (!ruleControls.component || !ruleControls.component.value || row.dataset.component === ruleControls.component.value) &&
                (!ruleControls.impact || !ruleControls.impact.value || row.dataset.impact === ruleControls.impact.value);
        }

        function renderRuleRows() {
            if (!ruleRows.length) return;
            var visibleCount = 0;
            ruleRows.forEach(function (row) {
                var matches = matchesRuleRow(row);
                var isDefaultVisible = row.dataset.defaultVisible === 'true';
                var show = matches && (showAllRules || isDefaultVisible);
                row.style.display = show ? '' : 'none';
                if (matches) visibleCount += 1;
            });
            if (rulesNoResults) rulesNoResults.style.display = visibleCount ? 'none' : '';
            if (toggleRules) toggleRules.textContent = showAllRules ? 'Show fewer rules' : 'Show all rules';
            updateRulesExportLink();
        }

        function renderSignalCards() {
            if (!signalCards.length) return;
            signalCards.forEach(function (card) {
                var isDefaultVisible = card.dataset.defaultVisible === 'true';
                card.style.display = (showAllSignals || isDefaultVisible) ? '' : 'none';
            });
            if (toggleSignals) toggleSignals.textContent = showAllSignals ? 'Show fewer issues' : 'Show all issues';
        }

        function setTeachingSummaryFeedback(message, isError) {
            if (!teachingSummaryFeedback) return;
            teachingSummaryFeedback.textContent = message || '';
            teachingSummaryFeedback.style.color = isError ? 'var(--color-danger)' : '';
        }

        function setTeachingSummaryHeadline(headline) {
            if (!teachingSummaryHeadline) return;
            var text = String(headline || '').trim();
            teachingSummaryHeadline.textContent = text;
            teachingSummaryHeadline.hidden = !text;
        }

        function renderTeachingSummary(headline, insights) {
            if (!teachingSummaryList) return;
            setTeachingSummaryHeadline(headline);
            teachingSummaryList.innerHTML = '';
            (insights || []).forEach(function (insight, index) {
                var li = document.createElement('li');
                li.dataset.insightType = insight.insight_type || ('insight_' + (index + 1));

                var strong = document.createElement('strong');
                strong.textContent = ((insight.priority || 'medium').charAt(0).toUpperCase() + (insight.priority || 'medium').slice(1)) + ' priority:';
                li.appendChild(strong);
                if (insight.title) {
                    li.appendChild(document.createTextNode(' '));
                    var title = document.createElement('span');
                    title.className = 'analytics-summary-item-title';
                    title.textContent = insight.title + ':';
                    li.appendChild(title);
                }
                li.appendChild(document.createTextNode(' ' + (insight.text || '')));
                teachingSummaryList.appendChild(li);
            });
        }

        function updateTeachingSummaryFromLlm() {
            if (!teachingSummaryButton) return;
            var endpoint = teachingSummaryButton.dataset.endpoint;
            if (!endpoint) return;

            teachingSummaryButton.disabled = true;
            teachingSummaryButton.textContent = 'Generating...';
            if (teachingSummaryStatus) teachingSummaryStatus.textContent = 'Generating LLM wording';
            if (teachingSummaryList) teachingSummaryList.setAttribute('aria-busy', 'true');
            setTeachingSummaryFeedback('Generating the LLM summary in the background.', false);

            fetch(endpoint, {
                headers: {
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest'
                },
                credentials: 'same-origin'
            })
                .then(function (response) {
                    return response.json().then(function (payload) {
                        if (!response.ok) {
                            throw new Error((payload && payload.error) || 'LLM summary generation failed.');
                        }
                        return payload;
                    });
                })
                .then(function (payload) {
                    if (payload && Array.isArray(payload.insights) && payload.insights.length) {
                        renderTeachingSummary(payload.headline || '', payload.insights);
                    } else {
                        setTeachingSummaryHeadline('');
                    }
                    if (teachingSummaryStatus) {
                        teachingSummaryStatus.textContent = payload && payload.source === 'llm'
                            ? 'LLM-enhanced wording'
                            : 'Deterministic wording';
                    }
                    if (payload && payload.source === 'llm') {
                        setTeachingSummaryFeedback('LLM summary ready.', false);
                        teachingSummaryButton.textContent = 'Refresh LLM summary';
                    } else {
                        setTeachingSummaryFeedback(
                            (payload && payload.fallback_reason)
                                ? payload.fallback_reason
                                : 'LLM summary was unavailable. Deterministic wording remains in place.',
                            true
                        );
                        teachingSummaryButton.textContent = 'Generate LLM summary';
                    }
                })
                .catch(function (error) {
                    if (teachingSummaryStatus) teachingSummaryStatus.textContent = 'Deterministic wording';
                    setTeachingSummaryFeedback(error.message || 'LLM summary generation failed.', true);
                    teachingSummaryButton.textContent = 'Generate LLM summary';
                })
                .finally(function () {
                    teachingSummaryButton.disabled = false;
                    if (teachingSummaryList) teachingSummaryList.removeAttribute('aria-busy');
                });
        }

        function drawMarkDistribution() {
            var container = document.getElementById('graph-mark-distribution');
            var meta = document.getElementById('graph-mark-distribution-meta');
            var data = graphData && graphData.mark_distribution_histogram;
            if (!container) return;
            if (!data || !data.bins || !data.bins.length) {
                container.innerHTML = chartEmptyState(
                    'Histogram unavailable',
                    'Histogram data will appear once active assignment scores are available.'
                );
                if (meta) meta.innerHTML = '';
                return;
            }
            var bins = data.bins.slice().sort(function (left, right) {
                return Number(left.range_min || 0) - Number(right.range_min || 0);
            });
            var width = 660;
            var height = 540;
            var margin = { top: 10, right: 12, bottom: 78, left: 72 };
            var innerWidth = width - margin.left - margin.right;
            var innerHeight = height - margin.top - margin.bottom;
            var maxCount = Math.max.apply(null, bins.map(function (bin) { return bin.count; }).concat([1]));
            var yTicks = niceIntegerTicks(maxCount, 5);
            var yTickMax = Math.max(yTicks[yTicks.length - 1] || maxCount || 1, 1);
            var yMax = maxCount <= 1 ? 1.15 : Math.max(yTickMax, maxCount * 1.1);
            var xTicks = Array.isArray(data.x_ticks) && data.x_ticks.length ? data.x_ticks : [0, 20, 40, 60, 80, 100];
            var primaryReference = data.primary_reference || {
                key: 'mean_percent',
                label: 'Mean',
                value: data.reference_lines && data.reference_lines.mean_percent,
                detail: 'Cohort mean mark across the active submissions in scope.'
            };
            var summaryStats = data.summary_stats || data.reference_lines || {};
            var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
            svg.setAttribute('class', 'analytics-chart-svg analytics-chart-svg--histogram');

            var axis = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            axis.setAttribute('d', 'M' + margin.left + ' ' + (margin.top + innerHeight) + 'H' + (margin.left + innerWidth) + 'M' + margin.left + ' ' + margin.top + 'V' + (margin.top + innerHeight));
            axis.setAttribute('class', 'analytics-chart-axis');

            yTicks.forEach(function (tick) {
                var y = margin.top + innerHeight - ((tick / yMax) * innerHeight);
                if (tick !== 0) {
                    var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                    line.setAttribute('x1', margin.left);
                    line.setAttribute('x2', margin.left + innerWidth);
                    line.setAttribute('y1', y);
                    line.setAttribute('y2', y);
                    line.setAttribute('class', 'analytics-chart-grid');
                    svg.appendChild(line);
                }
                svg.appendChild(createSvgText(margin.left - 10, y + 5, String(tick), 'analytics-chart-text analytics-chart-text--histogram', 'end'));
            });

            xTicks.forEach(function (tick) {
                var x = margin.left + ((tick / 100) * innerWidth);
                svg.appendChild(createSvgText(x, margin.top + innerHeight + 26, String(tick), 'analytics-chart-text analytics-chart-text--histogram', 'middle'));
            });

            svg.appendChild(axis);

            var fallbackBinWidth = Number(data.bin_width || (100 / Math.max(bins.length, 1)));
            bins.forEach(function (bin, index) {
                var rangeMin = Number(bin.range_min != null ? bin.range_min : (index * fallbackBinWidth));
                var rangeMax = Number(bin.range_max != null ? bin.range_max : Math.min(rangeMin + fallbackBinWidth, 100));
                var barHeight = (bin.count / yMax) * innerHeight;
                var exampleNames = studentSubsetExamples(bin.student_ids || [], 3);
                if (bin.count > 0) {
                    var barX = margin.left + ((rangeMin / 100) * innerWidth) + 0.5;
                    var barWidth = Math.max((((Math.min(rangeMax, 100) - rangeMin) / 100) * innerWidth) - 1, 1);
                    var rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                    rect.setAttribute('x', barX);
                    rect.setAttribute('y', margin.top + innerHeight - barHeight);
                    rect.setAttribute('width', barWidth);
                    rect.setAttribute('height', barHeight);
                    rect.setAttribute('fill', 'rgba(20, 184, 166, 0.92)');
                    rect.setAttribute('stroke', 'rgba(15, 23, 42, 0.55)');
                    rect.setAttribute('stroke-width', '0.75');
                    rect.setAttribute('class', 'analytics-chart-bar');
                    rect.setAttribute('tabindex', '0');
                    bindTooltip(rect, tooltipText(bin.label, [
                        bin.count + ' student' + (bin.count === 1 ? '' : 's'),
                        Math.round(bin.percent) + '% of active submissions',
                        exampleNames.length ? ('Examples: ' + exampleNames.join(', ')) : ''
                    ]));
                    rect.addEventListener('click', function () {
                        setSelectedGraphNode(rect);
                        openStudentDrawer({
                            label: 'Mark band: ' + bin.label,
                            subtitle: 'Latest active submissions that fall inside this assignment mark band.',
                            studentIds: bin.student_ids || [],
                            badgeText: Math.round(bin.percent) + '% of cohort'
                        });
                        filterReviewQueueToStudents(bin.student_ids || [], 'Mark band ' + bin.label);
                    });
                    svg.appendChild(rect);
                }
            });

            if (primaryReference && primaryReference.value !== null && primaryReference.value !== undefined) {
                var referenceX = margin.left + ((primaryReference.value / 100) * innerWidth);
                var halo = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                halo.setAttribute('x1', referenceX);
                halo.setAttribute('x2', referenceX);
                halo.setAttribute('y1', margin.top);
                halo.setAttribute('y2', margin.top + innerHeight);
                halo.setAttribute('stroke', 'rgba(255, 255, 255, 0.96)');
                halo.setAttribute('stroke-width', '4.6');
                halo.setAttribute('stroke-linecap', 'round');
                svg.appendChild(halo);
                var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                line.setAttribute('x1', referenceX);
                line.setAttribute('x2', referenceX);
                line.setAttribute('y1', margin.top);
                line.setAttribute('y2', margin.top + innerHeight);
                line.setAttribute('stroke', 'rgba(15, 23, 42, 0.9)');
                line.setAttribute('stroke-width', '2.25');
                line.setAttribute('stroke-dasharray', '7 5');
                line.setAttribute('stroke-linecap', 'round');
                svg.appendChild(line);
                var referenceLines = [
                    (primaryReference.detail || 'Central tendency for the cohort marks in scope.'),
                    'Value: ' + formatPercent(primaryReference.value)
                ];
                if (summaryStats.median_percent !== null && summaryStats.median_percent !== undefined && primaryReference.key !== 'median_percent') {
                    referenceLines.push('Median: ' + formatPercent(summaryStats.median_percent));
                }
                if (summaryStats.pass_threshold_percent !== null && summaryStats.pass_threshold_percent !== undefined) {
                    referenceLines.push('Pass threshold: ' + formatPercent(summaryStats.pass_threshold_percent));
                }
                appendReferenceHoverLine(svg, referenceX, margin.top, referenceX, margin.top + innerHeight, tooltipText(primaryReference.label || 'Reference line', referenceLines));
            }

            svg.appendChild(createSvgText(margin.left + (innerWidth / 2), height - 12, 'Mark', 'analytics-chart-text axis-title analytics-chart-text--histogram-axis', 'middle'));
            svg.appendChild(createSvgText(24, margin.top + (innerHeight / 2), 'Number of Students', 'analytics-chart-text axis-title analytics-chart-text--histogram-axis', 'middle', -90));

            container.innerHTML = '';
            container.appendChild(svg);
            if (meta) {
                var metaParts = [];
                if (summaryStats.mean_percent !== null && summaryStats.mean_percent !== undefined) {
                    metaParts.push('<span><strong>Mean</strong> ' + escapeHtml(formatPercent(summaryStats.mean_percent)) + '</span>');
                }
                if (summaryStats.median_percent !== null && summaryStats.median_percent !== undefined) {
                    metaParts.push('<span><strong>Median</strong> ' + escapeHtml(formatPercent(summaryStats.median_percent)) + '</span>');
                }
                if (summaryStats.pass_threshold_percent !== null && summaryStats.pass_threshold_percent !== undefined) {
                    metaParts.push('<span><strong>Pass threshold</strong> ' + escapeHtml(formatPercent(summaryStats.pass_threshold_percent)) + '</span>');
                }
                if (data.bin_width) {
                    metaParts.push('<span><strong>Bar width</strong> ' + escapeHtml(String(data.bin_width)) + ' marks</span>');
                }
                meta.innerHTML = metaParts.join('');
            }
        }

        function drawStaticFunctionalScatter() {
            var container = document.getElementById('graph-static-functional-scatter');
            var data = graphData && graphData.static_functional_scatter_plot;
            if (!container) return;
            renderScatterQuadrantNote(null);
            if (!data) {
                container.innerHTML = chartEmptyState(
                    'Scatter plot unavailable',
                    'Scatter plot data will appear once assignment submissions are available.'
                );
                return;
            }
            if (!data.supported) {
                container.innerHTML = chartEmptyState(
                    'Static vs functional view unavailable',
                    data.unsupported_reason || 'The current assignment does not have enough behavioural evidence for a meaningful comparison.'
                );
                return;
            }
            if (!data.points || !data.points.length) {
                container.innerHTML = chartEmptyState(
                    'Scatter plot unavailable',
                    'No active submissions are available to plot.'
                );
                return;
            }
            var width = 640;
            var height = 640;
            var margin = { top: 34, right: 34, bottom: 82, left: 82 };
            var innerWidth = width - margin.left - margin.right;
            var innerHeight = height - margin.top - margin.bottom;
            var ticks = [0, 25, 50, 75, 100];
            var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
            svg.setAttribute('class', 'analytics-chart-svg');
            var hoverTargets = [];

            var plot = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            plot.setAttribute('x', margin.left);
            plot.setAttribute('y', margin.top);
            plot.setAttribute('width', innerWidth);
            plot.setAttribute('height', innerHeight);
            plot.setAttribute('rx', '18');
            plot.setAttribute('fill', 'rgba(255, 255, 255, 0.96)');
            plot.setAttribute('stroke', 'rgba(148, 163, 184, 0.2)');
            svg.appendChild(plot);

            ticks.forEach(function (tick) {
                var gridX = margin.left + ((tick / 100) * innerWidth);
                var vertical = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                vertical.setAttribute('x1', gridX);
                vertical.setAttribute('x2', gridX);
                vertical.setAttribute('y1', margin.top);
                vertical.setAttribute('y2', margin.top + innerHeight);
                vertical.setAttribute('class', 'analytics-chart-grid');
                svg.appendChild(vertical);
                svg.appendChild(createSvgText(gridX, margin.top + innerHeight + 22, String(tick), 'analytics-chart-text', 'middle'));
            });

            ticks.forEach(function (tick) {
                var gridY = margin.top + innerHeight - ((tick / 100) * innerHeight);
                var horizontal = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                horizontal.setAttribute('x1', margin.left);
                horizontal.setAttribute('x2', margin.left + innerWidth);
                horizontal.setAttribute('y1', gridY);
                horizontal.setAttribute('y2', gridY);
                horizontal.setAttribute('class', 'analytics-chart-grid');
                svg.appendChild(horizontal);
                svg.appendChild(createSvgText(margin.left - 10, gridY + 4, String(tick), 'analytics-chart-text', 'end'));
            });

            var axis = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            axis.setAttribute('d', 'M' + margin.left + ' ' + (margin.top + innerHeight) + 'H' + (margin.left + innerWidth) + 'M' + margin.left + ' ' + margin.top + 'V' + (margin.top + innerHeight));
            axis.setAttribute('class', 'analytics-chart-axis');
            svg.appendChild(axis);

            if (data.reference_lines && data.reference_lines.show_balance_diagonal) {
                var diagonal = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                diagonal.setAttribute('x1', margin.left);
                diagonal.setAttribute('x2', margin.left + innerWidth);
                diagonal.setAttribute('y1', margin.top + innerHeight);
                diagonal.setAttribute('y2', margin.top);
                diagonal.setAttribute('stroke', 'rgba(71, 85, 105, 0.45)');
                diagonal.setAttribute('stroke-width', '1.6');
                diagonal.setAttribute('stroke-dasharray', '5 5');
                svg.appendChild(diagonal);
                hoverTargets.push({
                    x1: margin.left,
                    y1: margin.top + innerHeight,
                    x2: margin.left + innerWidth,
                    y2: margin.top,
                    html: tooltipText('Balance line', [
                        'x = y.',
                        'Points near this line have similar static and functional attainment.',
                        'Points far from it are imbalanced across code quality and observed behaviour.'
                    ])
                });
            }

            if (data.reference_lines && data.reference_lines.show_mean_lines) {
                var meanX = data.reference_lines.static_mean_percent;
                var meanY = data.reference_lines.behavioural_mean_percent;
                if (meanX !== null && meanX !== undefined) {
                    var verticalMean = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                    verticalMean.setAttribute('x1', margin.left + ((meanX / 100) * innerWidth));
                    verticalMean.setAttribute('x2', margin.left + ((meanX / 100) * innerWidth));
                    verticalMean.setAttribute('y1', margin.top);
                    verticalMean.setAttribute('y2', margin.top + innerHeight);
                    verticalMean.setAttribute('class', 'analytics-chart-reference mean');
                    verticalMean.setAttribute('stroke-width', '1.15');
                    verticalMean.setAttribute('stroke-dasharray', '3 6');
                    svg.appendChild(verticalMean);
                    hoverTargets.push({
                        x1: margin.left + ((meanX / 100) * innerWidth),
                        y1: margin.top,
                        x2: margin.left + ((meanX / 100) * innerWidth),
                        y2: margin.top + innerHeight,
                        html: tooltipText('Cohort mean: static / code quality', [
                            'Average static / code quality score: ' + formatPercent(meanX),
                            'Submissions to the right of this line are above the cohort mean on static evidence.'
                        ])
                    });
                }
                if (meanY !== null && meanY !== undefined) {
                    var horizontalMean = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                    horizontalMean.setAttribute('x1', margin.left);
                    horizontalMean.setAttribute('x2', margin.left + innerWidth);
                    horizontalMean.setAttribute('y1', margin.top + innerHeight - ((meanY / 100) * innerHeight));
                    horizontalMean.setAttribute('y2', margin.top + innerHeight - ((meanY / 100) * innerHeight));
                    horizontalMean.setAttribute('class', 'analytics-chart-reference median');
                    horizontalMean.setAttribute('stroke-width', '1.15');
                    horizontalMean.setAttribute('stroke-dasharray', '3 6');
                    svg.appendChild(horizontalMean);
                    hoverTargets.push({
                        x1: margin.left,
                        y1: margin.top + innerHeight - ((meanY / 100) * innerHeight),
                        x2: margin.left + innerWidth,
                        y2: margin.top + innerHeight - ((meanY / 100) * innerHeight),
                        html: tooltipText('Cohort mean: behavioural / functional', [
                            'Average behavioural / functional score: ' + formatPercent(meanY),
                            'Points above this line are above the cohort mean on runtime and browser evidence.'
                        ])
                    });
                }

                if (meanX !== null && meanX !== undefined && meanY !== null && meanY !== undefined) {
                    var quadrantX = margin.left + ((meanX / 100) * innerWidth);
                    var quadrantY = margin.top + innerHeight - ((meanY / 100) * innerHeight);
                    [
                        {
                            x: margin.left,
                            y: margin.top,
                            width: quadrantX - margin.left,
                            height: quadrantY - margin.top,
                            title: 'High Function / Low Quality',
                            lines: [
                                'Behavioural / functional attainment is above the cohort mean.',
                                'Static / code quality attainment is below the cohort mean.',
                                'These submissions work relatively well but rely on weaker structural or code-quality evidence.'
                            ]
                        },
                        {
                            x: quadrantX,
                            y: margin.top,
                            width: (margin.left + innerWidth) - quadrantX,
                            height: quadrantY - margin.top,
                            title: 'Strong Overall',
                            lines: [
                                'Both static / code quality and behavioural / functional attainment are above the cohort means.',
                                'These submissions are strong on both structural evidence and observed behaviour.'
                            ]
                        },
                        {
                            x: margin.left,
                            y: quadrantY,
                            width: quadrantX - margin.left,
                            height: (margin.top + innerHeight) - quadrantY,
                            title: 'Weak Overall',
                            lines: [
                                'Both static / code quality and behavioural / functional attainment are below the cohort means.',
                                'These submissions are weaker across both structural evidence and observed behaviour.'
                            ]
                        },
                        {
                            x: quadrantX,
                            y: quadrantY,
                            width: (margin.left + innerWidth) - quadrantX,
                            height: (margin.top + innerHeight) - quadrantY,
                            title: 'High Quality / Low Function',
                            lines: [
                                'Static / code quality attainment is above the cohort mean.',
                                'Behavioural / functional attainment is below the cohort mean.',
                                'These submissions show stronger structure than observed runtime or interactive behaviour.'
                            ]
                        }
                    ].forEach(function (region) {
                        appendQuadrantHoverRegion(svg, region);
                    });
                }
            }

            hoverTargets.forEach(function (target) {
                appendReferenceHoverLine(svg, target.x1, target.y1, target.x2, target.y2, target.html);
            });

            data.points.forEach(function (point) {
                var cx = margin.left + (((point.static_score_percent == null ? 0 : point.static_score_percent) / 100) * innerWidth);
                var cy = margin.top + innerHeight - (((point.behavioural_score_percent == null ? 0 : point.behavioural_score_percent) / 100) * innerHeight);
                var circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                circle.setAttribute('cx', cx);
                circle.setAttribute('cy', cy);
                circle.setAttribute('r', point.manual_review_recommended ? '8' : '6');
                circle.setAttribute('fill', point.confidence === 'low' ? graphColor('danger') : (point.confidence === 'medium' ? graphColor('warning') : graphColor('success')));
                circle.setAttribute('fill-opacity', point.functional_evidence_limited ? '0.45' : (point.manual_review_recommended ? '0.94' : '0.78'));
                circle.setAttribute('stroke', point.manual_review_recommended ? graphColor('ink') : '#ffffff');
                circle.setAttribute('stroke-width', point.manual_review_recommended ? '2.5' : '1.6');
                if (point.functional_evidence_limited) {
                    circle.setAttribute('stroke-dasharray', '4 3');
                }
                circle.setAttribute('class', 'analytics-chart-point');
                circle.setAttribute('tabindex', '0');
                bindTooltip(circle, tooltipText(studentDisplayName(point), [
                    'Overall mark: ' + formatPercent(point.overall_mark_percent),
                    'Static / code quality: ' + formatPercent(point.static_score_percent),
                    'Behavioural / functional: ' + formatPercent(point.behavioural_score_percent),
                    'Confidence: ' + String(point.confidence || 'n/a'),
                    point.primary_issue ? ('Primary issue: ' + point.primary_issue) : '',
                    point.functional_evidence_limited ? 'Behavioural evidence is limited for this submission.' : ''
                ]));
                circle.addEventListener('click', function () {
                    setSelectedGraphNode(circle);
                    openStudentDrawer({
                        label: 'Submission focus: ' + studentDisplayName(point),
                        subtitle: 'Latest active submission detail for this static-versus-functional comparison point.',
                        studentIds: point.student_ids || [point.student_id],
                        badgeText: 'Overall ' + formatPercent(point.overall_mark_percent)
                    });
                    filterReviewQueueToStudents(point.student_ids || [point.student_id], 'Scatter selection: ' + studentDisplayName(point));
                });
                svg.appendChild(circle);
            });

            svg.appendChild(createSvgText(margin.left + (innerWidth / 2), height - 18, 'Static / Code Quality Score', 'analytics-chart-text axis-title', 'middle'));
            svg.appendChild(createSvgText(24, margin.top + (innerHeight / 2), 'Behavioural / Functional Score', 'analytics-chart-text axis-title', 'middle', -90));

            container.innerHTML = '';
            container.appendChild(svg);
        }

        function renderInteractiveGraphs() {
            drawMarkDistribution();
            drawStaticFunctionalScatter();
        }

        function clearAttentionFilters() {
            Object.keys(attentionControls).forEach(function (key) {
                if (attentionControls[key]) attentionControls[key].value = '';
            });
            if (attentionControls.sort) attentionControls.sort.value = 'severity';
            attentionState.signalLabel = '';
            attentionState.signalRules = [];
            attentionState.signalStudents = [];
            renderAttention();
        }

        function clearRuleFilters() {
            Object.keys(ruleControls).forEach(function (key) {
                if (ruleControls[key]) ruleControls[key].value = '';
            });
            renderRuleRows();
        }

        if (studentDrawerFilterQueue) {
            studentDrawerFilterQueue.addEventListener('click', function () {
                if (!currentDrawerStudentIds.length) return;
                filterReviewQueueToStudents(currentDrawerStudentIds, currentDrawerLabel);
            });
        }
        if (closeStudentDrawer) closeStudentDrawer.addEventListener('click', closeDrawer);
        if (clearGraphSelectionButton) clearGraphSelectionButton.addEventListener('click', clearGraphSelection);
        Array.prototype.slice.call(document.querySelectorAll('[data-drawer-close="true"]')).forEach(function (element) {
            element.addEventListener('click', closeDrawer);
        });
        if (clearRequirementFocusButton) clearRequirementFocusButton.addEventListener('click', clearRequirementFocus);

        Object.keys(attentionControls).forEach(function (key) {
            var control = attentionControls[key];
            if (!control) return;
            var eventName = key === 'student' || key === 'rule' ? 'input' : 'change';
            control.addEventListener(eventName, renderAttention);
        });

        Object.keys(ruleControls).forEach(function (key) {
            var control = ruleControls[key];
            if (control) control.addEventListener('change', renderRuleRows);
        });

        var clearAttentionButton = document.getElementById('clear-attention-filters');
        if (clearAttentionButton) clearAttentionButton.addEventListener('click', clearAttentionFilters);
        var clearRulesButton = document.getElementById('clear-rule-filters');
        if (clearRulesButton) clearRulesButton.addEventListener('click', clearRuleFilters);
        var clearDrilldown = document.getElementById('clear-drilldown');
        if (clearDrilldown) {
            clearDrilldown.addEventListener('click', function () {
                attentionState.signalLabel = '';
                attentionState.signalRules = [];
                attentionState.signalStudents = [];
                renderAttention();
            });
        }

        jumpControls.forEach(function (control) {
            control.addEventListener('click', function (event) {
                var targetId = control.dataset.analyticsScrollTarget || control.dataset.analyticsTarget;
                if (!targetId) return;
                event.preventDefault();
                navigateToSection(targetId);
            });
        });

        Array.prototype.slice.call(document.querySelectorAll('.js-rule-filter')).forEach(function (button) {
            button.addEventListener('click', function () {
                if (attentionControls.rule) attentionControls.rule.value = button.dataset.rule || '';
                navigateToSection('needs-attention');
                renderAttention();
            });
        });

        Array.prototype.slice.call(document.querySelectorAll('.js-student-filter')).forEach(function (button) {
            button.addEventListener('click', function () {
                if (attentionControls.student) attentionControls.student.value = button.dataset.student || '';
                navigateToSection('needs-attention');
                renderAttention();
            });
        });

        Array.prototype.slice.call(document.querySelectorAll('.js-signal-filter')).forEach(function (button) {
            button.addEventListener('click', function () {
                attentionState.signalLabel = button.dataset.label || 'Cohort issue filter';
                attentionState.signalRules = splitList(button.dataset.rules);
                attentionState.signalStudents = splitList(button.dataset.students).map(function (item) { return item.toLowerCase(); });
                navigateToSection('needs-attention');
                renderAttention();
            });
        });

        Array.prototype.slice.call(document.querySelectorAll('.js-subset-button')).forEach(function (button) {
            button.addEventListener('click', function () {
                var studentIds = splitList(button.dataset.students);
                var label = button.dataset.label || 'Student subset';
                var badgeText = button.dataset.badge || '';
                var component = button.dataset.component || '';
                var focusLabel = button.dataset.focusLabel || label;
                var shouldFilter = button.dataset.filter !== 'false';
                setSelectedGraphNode(button);
                if (component) {
                    focusRequirement(component, focusLabel);
                }
                if (!studentIds.length) return;
                openStudentDrawer({
                    label: label,
                    studentIds: studentIds,
                    badgeText: badgeText
                });
                if (shouldFilter) {
                    filterReviewQueueToStudents(studentIds, label);
                }
            });
        });

        Array.prototype.slice.call(document.querySelectorAll('.js-confidence-filter')).forEach(function (button) {
            button.addEventListener('click', function () {
                if (attentionControls.confidence) attentionControls.confidence.value = button.dataset.confidence || '';
                navigateToSection('needs-attention');
                renderAttention();
            });
        });

        if (toggleRules) {
            toggleRules.addEventListener('click', function () {
                showAllRules = !showAllRules;
                renderRuleRows();
            });
        }

        if (toggleSignals) {
            toggleSignals.addEventListener('click', function () {
                showAllSignals = !showAllSignals;
                renderSignalCards();
            });
        }

        if (teachingSummaryButton) {
            teachingSummaryButton.addEventListener('click', updateTeachingSummaryFromLlm);
        }

        collapsibleSections.forEach(function (section) {
            section.addEventListener('toggle', function () {
                setAnalyticsOffsets();
                requestActiveNavSync();
            });
        });

        window.addEventListener('scroll', requestActiveNavSync, { passive: true });
        window.addEventListener('resize', function () {
            setAnalyticsOffsets();
            requestActiveNavSync();
            hideTooltip();
        });
        window.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                closeDrawer();
                hideTooltip();
                // Close any open export dropdowns
                document.querySelectorAll('.export-dropdown.is-open').forEach(function(d) {
                    d.classList.remove('is-open');
                    var trigger = d.querySelector('.export-dropdown-trigger');
                    if (trigger) trigger.setAttribute('aria-expanded', 'false');
                });
            }
        });

        // Export dropdown click handling
        document.body.addEventListener('click', function (e) {
            var trigger = e.target.closest('.export-dropdown-trigger');
            if (trigger) {
                e.preventDefault();
                var dropdown = trigger.closest('.export-dropdown');
                var isOpen = dropdown.classList.contains('is-open');
                // Close all other dropdowns first
                document.querySelectorAll('.export-dropdown.is-open').forEach(function(d) {
                    d.classList.remove('is-open');
                    var t = d.querySelector('.export-dropdown-trigger');
                    if (t) t.setAttribute('aria-expanded', 'false');
                });
                // Toggle current dropdown
                if (!isOpen) {
                    dropdown.classList.add('is-open');
                    trigger.setAttribute('aria-expanded', 'true');
                }
                return;
            }
            // Close dropdowns when clicking outside
            if (!e.target.closest('.export-dropdown')) {
                document.querySelectorAll('.export-dropdown.is-open').forEach(function(d) {
                    d.classList.remove('is-open');
                    var t = d.querySelector('.export-dropdown-trigger');
                    if (t) t.setAttribute('aria-expanded', 'false');
                });
            }
        });

        renderAttention();
        renderRuleRows();
        renderSignalCards();
        renderInteractiveGraphs();
        updateRulesExportLink();
        setAnalyticsOffsets();
        if (window.location.hash) {
            var initialTargetId = window.location.hash.slice(1);
            if (document.getElementById(initialTargetId)) {
                navigateToSection(initialTargetId, { instant: true, updateHash: false });
            } else {
                syncActiveNav();
            }
        } else {
            syncActiveNav();
        }
    }());
