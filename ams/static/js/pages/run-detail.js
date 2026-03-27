(function() {
    // Export dropdown toggle functionality
    document.addEventListener('click', function(e) {
        var trigger = e.target.closest('.export-dropdown-trigger');
        if (trigger) {
            e.preventDefault();
            var dropdown = trigger.closest('.export-dropdown');
            var isOpen = dropdown.classList.contains('is-open');

            // Close all other dropdowns
            document.querySelectorAll('.export-dropdown.is-open').forEach(function(d) {
                d.classList.remove('is-open');
                d.querySelector('.export-dropdown-trigger').setAttribute('aria-expanded', 'false');
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
                d.querySelector('.export-dropdown-trigger').setAttribute('aria-expanded', 'false');
            });
        }
    });

    // Handle keyboard navigation
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            document.querySelectorAll('.export-dropdown.is-open').forEach(function(d) {
                d.classList.remove('is-open');
                d.querySelector('.export-dropdown-trigger').setAttribute('aria-expanded', 'false');
                d.querySelector('.export-dropdown-trigger').focus();
            });
        }
    });
})();
