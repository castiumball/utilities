// Theme Toggler logic
document.addEventListener('DOMContentLoaded', () => {
    // 1. Theme Logic
    const toggleButton = document.getElementById('theme-toggle');
    const prefersDarkScheme = window.matchMedia('(prefers-color-scheme: dark)');
    const currentTheme = localStorage.getItem('theme');
    
    if (currentTheme === 'dark') {
        document.body.setAttribute('data-theme', 'dark');
    } else if (currentTheme === 'light') {
        document.body.setAttribute('data-theme', 'light');
    } else if (prefersDarkScheme.matches) {
        document.body.setAttribute('data-theme', 'dark');
    }

    if (toggleButton) {
        toggleButton.addEventListener('click', () => {
            let theme = document.body.getAttribute('data-theme');
            if (theme === 'dark') {
                document.body.setAttribute('data-theme', 'light');
                localStorage.setItem('theme', 'light');
            } else {
                document.body.setAttribute('data-theme', 'dark');
                localStorage.setItem('theme', 'dark');
            }
        });
    }

    // 2. Auto-Tag Status Columns for Styling
    const statusColumnIndices = [2, 10, 14, 17, 21]; // 0-based indices for columns 3, 11, 15, 18, 22
    const rows = document.querySelectorAll('tbody tr');

    rows.forEach(row => {
        const cells = row.cells;
        statusColumnIndices.forEach(index => {
            if (cells[index]) {
                const text = cells[index].textContent.trim().toLowerCase().replace(/\s+/g, '-');
                if (text) {
                    cells[index].setAttribute('data-status', text);
                }
            }
        });
    });
});
