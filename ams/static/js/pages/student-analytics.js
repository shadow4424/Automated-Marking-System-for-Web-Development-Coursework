    (function () {
        var graphData = window.AMS_STUDENT_DATA.graphs || {};
        var histogramContainer = document.getElementById('student-histogram');
        var histogramMeta = document.getElementById('student-histogram-meta');
        var scatterContainer = document.getElementById('student-scatter');
        var scatterQuadrantNote = document.getElementById('student-scatter-quadrant-note');
        var tooltip = document.getElementById('analytics-tooltip');
        var root = document.documentElement;
        var navbar = document.querySelector('.navbar');
        var localNav = document.getElementById('student-analytics-nav');
        var navLinks = Array.prototype.slice.call(document.querySelectorAll('[data-student-analytics-target]'));
        var sections = navLinks.map(function (link) { return document.getElementById(link.getAttribute('href').slice(1)); }).filter(Boolean);

        function escapeHtml(value) {
            return String(value || '')
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
            var targetRect = event && event.currentTarget && event.currentTarget.getBoundingClientRect
                ? event.currentTarget.getBoundingClientRect()
                : null;
            var clientX = event && typeof event.clientX === 'number' && event.clientX > 0
                ? event.clientX
                : (targetRect ? targetRect.left : 0);
            var clientY = event && typeof event.clientY === 'number' && event.clientY > 0
                ? event.clientY
                : (targetRect ? targetRect.top : 0);
            var x = clientX + 16;
            var y = clientY + 16;
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

        function setOffsets() {
            var navbarHeight = navbar ? navbar.offsetHeight : 72;
            root.style.setProperty('--analytics-local-nav-top', (navbarHeight + 12) + 'px');
            root.style.setProperty('--analytics-anchor-offset', (navbarHeight + (localNav ? localNav.offsetHeight : 56) + 22) + 'px');
        }

        function renderHistogram() {
            if (!histogramContainer) return;
            var data = graphData.histogram || {};
            if (!data.bins || !data.bins.length) {
                histogramContainer.innerHTML = chartEmptyState(
                    'Histogram unavailable',
                    'Histogram data will appear once active assignment scores are available.'
                );
                if (histogramMeta) histogramMeta.innerHTML = '';
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
                value: data.mean_percent,
                detail: 'Cohort mean mark across the active submissions in scope.'
            };
            var summaryStats = data.summary_stats || {
                mean_percent: data.mean_percent,
                median_percent: data.median_percent,
                pass_threshold_percent: data.pass_threshold_percent
            };
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
                if (bin.count > 0) {
                    var barX = margin.left + ((rangeMin / 100) * innerWidth) + 0.5;
                    var barWidth = Math.max((((Math.min(rangeMax, 100) - rangeMin) / 100) * innerWidth) - 1, 1);
                    var rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                    rect.setAttribute('x', barX);
                    rect.setAttribute('y', margin.top + innerHeight - barHeight);
                    rect.setAttribute('width', barWidth);
                    rect.setAttribute('height', barHeight);
                    rect.setAttribute('fill', bin.is_current_student ? graphColor('info') : 'rgba(20, 184, 166, 0.92)');
                    rect.setAttribute('stroke', bin.is_current_student ? 'rgba(29, 78, 216, 0.92)' : 'rgba(15, 23, 42, 0.55)');
                    rect.setAttribute('stroke-width', bin.is_current_student ? '1.15' : '0.75');
                    rect.setAttribute('class', 'analytics-chart-bar' + (bin.is_current_student ? ' is-selected' : ''));
                    rect.setAttribute('tabindex', '0');
                    bindTooltip(rect, tooltipText(bin.is_current_student ? 'Your mark band' : bin.label, [
                        bin.count + ' student' + (bin.count === 1 ? '' : 's'),
                        Math.round(bin.percent || 0) + '% of cohort',
                        bin.is_current_student ? 'Your current result sits in this band.' : 'Your current result sits in a different band.'
                    ]));
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

            histogramContainer.innerHTML = '';
            histogramContainer.appendChild(svg);
            if (histogramMeta) {
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
                histogramMeta.innerHTML = metaParts.join('');
            }
        }

        function renderScatter() {
            if (!scatterContainer) return;
            var data = graphData.scatter || {};
            renderScatterQuadrantNote(null);
            if (!data.supported) {
                scatterContainer.innerHTML = chartEmptyState(
                    'Static vs functional view unavailable',
                    data.unsupported_reason || 'The current assignment does not have enough behavioural evidence for a meaningful comparison.'
                );
                return;
            }
            if (!data.points || !data.points.length) {
                scatterContainer.innerHTML = chartEmptyState(
                    'Scatter plot unavailable',
                    'No cohort submissions are available to plot.'
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
                            'Points to the right of this line are above the cohort mean on static evidence.'
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
                var staticScore = point.static_score_percent;
                var behaviouralScore = point.behavioural_score_percent;
                if (typeof staticScore !== 'number' || typeof behaviouralScore !== 'number') return;
                var cx = margin.left + ((staticScore / 100) * innerWidth);
                var cy = margin.top + innerHeight - ((behaviouralScore / 100) * innerHeight);
                var confidenceFill = point.confidence === 'low'
                    ? graphColor('danger')
                    : (point.confidence === 'medium' ? graphColor('warning') : graphColor('success'));
                var circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                circle.setAttribute('cx', cx);
                circle.setAttribute('cy', cy);
                circle.setAttribute('r', point.is_current_student ? (point.manual_review_recommended ? '9' : '7.5') : (point.manual_review_recommended ? '8' : '6'));
                circle.setAttribute('fill', confidenceFill);
                circle.setAttribute('fill-opacity', point.is_current_student ? '1' : (point.functional_evidence_limited ? '0.45' : (point.manual_review_recommended ? '0.94' : '0.78')));
                circle.setAttribute('stroke', point.is_current_student ? graphColor('info') : (point.manual_review_recommended ? graphColor('ink') : '#ffffff'));
                circle.setAttribute('stroke-width', point.is_current_student ? '3' : (point.manual_review_recommended ? '2.5' : '1.6'));
                if (point.functional_evidence_limited) {
                    circle.setAttribute('stroke-dasharray', '4 3');
                }
                circle.setAttribute('class', 'analytics-chart-point' + (point.is_current_student ? ' is-selected' : ''));
                circle.setAttribute('tabindex', '0');
                bindTooltip(circle, tooltipText(point.is_current_student ? 'You' : 'Anonymous cohort submission', [
                    'Overall mark: ' + formatPercent(point.overall_mark_percent != null ? point.overall_mark_percent : point.overall_percent),
                    'Static / code quality: ' + formatPercent(staticScore),
                    'Behavioural / functional: ' + formatPercent(behaviouralScore),
                    'Confidence: ' + String(point.confidence || 'n/a'),
                    point.is_current_student ? 'This point shows your active assessed result.' : 'This point represents one anonymous cohort result.',
                    point.functional_evidence_limited ? 'Behavioural evidence is limited for this submission.' : '',
                    point.manual_review_recommended ? 'Manual review is recommended for this result.' : ''
                ]));
                svg.appendChild(circle);
            });

            svg.appendChild(createSvgText(margin.left + (innerWidth / 2), height - 18, 'Static / Code Quality Score', 'analytics-chart-text axis-title', 'middle'));
            svg.appendChild(createSvgText(24, margin.top + (innerHeight / 2), 'Behavioural / Functional Score', 'analytics-chart-text axis-title', 'middle', -90));

            scatterContainer.innerHTML = '';
            scatterContainer.appendChild(svg);
        }

        function activateCurrentNav() {
            if (!sections.length) return;
            var offset = (navbar ? navbar.offsetHeight : 72) + (localNav ? localNav.offsetHeight : 56) + 28;
            var activeId = sections[0].id;
            sections.forEach(function (section) {
                if (section.getBoundingClientRect().top - offset <= 0) activeId = section.id;
            });
            if ((window.innerHeight + window.scrollY) >= (document.documentElement.scrollHeight - 8)) {
                activeId = sections[sections.length - 1].id;
            }
            navLinks.forEach(function (link) {
                var isActive = link.getAttribute('href') === '#' + activeId;
                link.classList.toggle('is-active', isActive);
                if (isActive) {
                    link.setAttribute('aria-current', 'location');
                } else {
                    link.removeAttribute('aria-current');
                }
            });
        }

        navLinks.forEach(function (link) {
            link.addEventListener('click', function (event) {
                event.preventDefault();
                var target = document.querySelector(link.getAttribute('href'));
                if (!target) return;
                var offset = (navbar ? navbar.offsetHeight : 72) + (localNav ? localNav.offsetHeight : 56) + 16;
                window.scrollTo({ top: window.scrollY + target.getBoundingClientRect().top - offset, behavior: 'smooth' });
            });
        });

        renderHistogram();
        renderScatter();
        setOffsets();
        activateCurrentNav();
        window.addEventListener('resize', function () { setOffsets(); activateCurrentNav(); });
        window.addEventListener('scroll', activateCurrentNav, { passive: true });

        var feedbackButton = document.getElementById('generate-student-feedback');
        var feedbackPanel = document.getElementById('student-feedback-panel');
        var feedbackStatus = document.getElementById('student-feedback-status');
        var endpoint = window.AMS_STUDENT_DATA.feedbackUrl || '';

        function renderFeedbackPayload(payload) {
            if (!feedbackPanel) return;
            var items = Array.isArray(payload.feedback) ? payload.feedback : [];
            feedbackPanel.innerHTML =
                '<div style="display:grid; gap: var(--space-md);">' +
                    (payload.headline ? '<div class="student-analytics-card" style="padding:1rem;"><strong>' + escapeHtml(payload.headline) + '</strong>' + (payload.fallback_reason ? '<div class="student-analytics-tagline" style="margin-top:0.45rem;">' + escapeHtml(payload.fallback_reason) + '</div>' : '') + '</div>' : '') +
                    items.map(function (item) {
                        return '<article class="student-analytics-check-card"><strong>' + escapeHtml(item.title) + '</strong><div class="student-analytics-tagline" style="margin-top:0.55rem;">' + escapeHtml(item.text) + '</div></article>';
                    }).join('') +
                    (!payload.headline && !items.length ? '<div class="student-analytics-feedback-empty">Personalised feedback is not available for this submission yet.</div>' : '') +
                '</div>';
        }

        if (feedbackButton && feedbackPanel && endpoint) {
            feedbackButton.addEventListener('click', function () {
                feedbackButton.disabled = true;
                feedbackStatus.textContent = 'Generating...';
                fetch(endpoint, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                    .then(function (response) { return response.json(); })
                    .then(function (payload) {
                        feedbackStatus.textContent = payload.source === 'llm' ? 'LLM-enhanced wording' : 'Deterministic wording';
                        renderFeedbackPayload(payload);
                    })
                    .catch(function () {
                        feedbackStatus.textContent = 'Unavailable';
                    })
                    .finally(function () {
                        feedbackButton.disabled = false;
                    });
            });
        }
    })();
