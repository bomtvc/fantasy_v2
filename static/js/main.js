/**
 * Main JavaScript
 * Core functionality and utilities
 */

// === Global State ===
const AppState = {
    currentLeagueId: null,
    currentPhase: 1,
    isLoading: false,
};

// === Data Cache Manager ===
const DataCache = {
    // Cache storage
    _cache: {},
    _cacheTime: {},
    _cacheTTL: 5 * 60 * 1000, // 5 minutes default TTL

    // Get cache key based on filters
    // Phai gom MOI filter anh huong toi ket qua: truoc day key chi co
    // league_id va phase nen doi gw range hay max_entries van tra ve du lieu cu.
    getCacheKey(dataType, filters = null) {
        if (!filters) {
            filters = typeof getCurrentFilters === 'function' ? getCurrentFilters() : {};
        }
        const parts = [
            dataType,
            filters.league_id,
            filters.phase,
            filters.gw_start,
            filters.gw_end,
            filters.max_entries,
            filters.month_mapping,
        ];
        return parts.join('_');
    },

    // Check if cache is valid
    isValid(key) {
        if (!this._cache[key]) return false;
        const cacheTime = this._cacheTime[key] || 0;
        return (Date.now() - cacheTime) < this._cacheTTL;
    },

    // Get cached data
    get(dataType, filters = null) {
        const key = this.getCacheKey(dataType, filters);
        if (this.isValid(key)) {
            console.log(`[Cache] HIT: ${key}`);
            return this._cache[key];
        }
        console.log(`[Cache] MISS: ${key}`);
        return null;
    },

    // Set cache data
    set(dataType, data, filters = null) {
        const key = this.getCacheKey(dataType, filters);
        this._cache[key] = data;
        this._cacheTime[key] = Date.now();
        console.log(`[Cache] SET: ${key}`);
    },

    // Clear specific cache
    clear(dataType = null, filters = null) {
        if (dataType) {
            const key = this.getCacheKey(dataType, filters);
            delete this._cache[key];
            delete this._cacheTime[key];
            console.log(`[Cache] CLEAR: ${key}`);
        } else {
            // Clear all cache
            this._cache = {};
            this._cacheTime = {};
            console.log('[Cache] CLEAR ALL');
        }
    },

    // Get cache stats
    getStats() {
        const keys = Object.keys(this._cache);
        return {
            count: keys.length,
            keys: keys,
            sizes: keys.map(k => ({ key: k, size: JSON.stringify(this._cache[k]).length }))
        };
    }
};

// === Cached Fetch Helper ===
async function cachedFetch(dataType, url, forceRefresh = false) {
    // Check cache first
    if (!forceRefresh) {
        const cached = DataCache.get(dataType);
        if (cached) {
            return cached;
        }
    }

    // Fetch from API
    const response = await fetch(url);
    const data = await response.json();

    if (data.success) {
        DataCache.set(dataType, data);
    }

    return data;
}

// === DOM Ready ===
document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
    setupEventListeners();
    setupMobileMenu();
});

// === Initialize App ===
function initializeApp() {
    // Load saved preferences from localStorage
    loadPreferences();

    // Set active nav link
    setActiveNavLink();

    // Initialize tooltips, if any
    initializeTooltips();

    // Fetch and set current GW from API (important for correct data range)
    fetchCurrentGW();
}

// === Fetch Current GW from API ===
async function fetchCurrentGW() {
    try {
        const response = await fetch('/api/current-gw');
        const data = await response.json();

        if (data.success && data.current_gw) {
            const gwEndInput = document.getElementById('gw-end');
            if (gwEndInput) {
                gwEndInput.value = data.current_gw;
                console.log(`[App] Current GW set to: ${data.current_gw}`);
            }
        }
    } catch (error) {
        console.warn('[App] Could not fetch current GW:', error);
        // Keep default value, don't break the app
    }
}

// === Event Listeners ===
function setupEventListeners() {
    // Filters form submission
    const filtersForm = document.getElementById('filters-form');
    if (filtersForm) {
        filtersForm.addEventListener('submit', handleFiltersSubmit);
    }
    
    // Clear cache button
    const clearCacheBtn = document.getElementById('clear-cache-btn');
    if (clearCacheBtn) {
        clearCacheBtn.addEventListener('click', handleClearCache);
    }
    
    // Save filter values to localStorage on change
    const filterInputs = document.querySelectorAll('#filters-form input, #filters-form select');
    filterInputs.forEach(input => {
        input.addEventListener('change', savePreferences);
    });
}

// === Mobile Menu ===
function setupMobileMenu() {
    const navbarToggle = document.getElementById('navbarToggle');
    const sidebar = document.getElementById('sidebar');
    const navbarNav = document.querySelector('.navbar-nav');

    if (navbarToggle) {
        navbarToggle.addEventListener('click', () => {
            // Toggle navbar menu on mobile
            if (navbarNav) {
                navbarNav.classList.toggle('active');
                navbarToggle.classList.toggle('active');
            }
            // Also toggle sidebar if exists
            if (sidebar) {
                sidebar.classList.toggle('active');
            }
        });

        // Close menus when clicking outside
        document.addEventListener('click', (e) => {
            if (window.innerWidth <= 1024) {
                const isClickInsideNav = navbarNav && navbarNav.contains(e.target);
                const isClickInsideSidebar = sidebar && sidebar.contains(e.target);
                const isClickOnToggle = navbarToggle.contains(e.target);

                if (!isClickInsideNav && !isClickInsideSidebar && !isClickOnToggle) {
                    if (navbarNav) navbarNav.classList.remove('active');
                    if (sidebar) sidebar.classList.remove('active');
                    navbarToggle.classList.remove('active');
                }
            }
        });

        // Close menu when clicking a nav link
        if (navbarNav) {
            navbarNav.querySelectorAll('.nav-link').forEach(link => {
                link.addEventListener('click', () => {
                    navbarNav.classList.remove('active');
                    navbarToggle.classList.remove('active');
                });
            });
        }
    }

    // Setup sidebar collapse toggle
    setupSidebarCollapse();
}

// === Sidebar Collapse ===
function setupSidebarCollapse() {
    const collapseBtn = document.getElementById('sidebar-collapse-btn');
    const sidebar = document.getElementById('sidebar');
    const mainContent = document.querySelector('.main-content');

    if (collapseBtn && sidebar) {
        // Load saved state
        const isCollapsed = localStorage.getItem('sidebar_collapsed') === 'true';
        if (isCollapsed) {
            sidebar.classList.add('collapsed');
            if (mainContent) mainContent.classList.add('sidebar-collapsed');
        }

        collapseBtn.addEventListener('click', () => {
            sidebar.classList.toggle('collapsed');
            if (mainContent) mainContent.classList.toggle('sidebar-collapsed');

            // Save state
            localStorage.setItem('sidebar_collapsed', sidebar.classList.contains('collapsed'));
        });
    }
}

// === Handle Filters Submit ===
async function handleFiltersSubmit(e) {
    e.preventDefault();

    const formData = new FormData(e.target);
    const filters = Object.fromEntries(formData.entries());

    // Update app state
    AppState.currentLeagueId = filters.league_id;
    AppState.currentPhase = filters.phase;

    // Save to localStorage
    savePreferences();

    try {
        showLoading('Applying filters...');

        // Chi don cache phia trinh duyet.
        //
        // Cache server KHONG bi xoa: cache key cua no da gom
        // league_id/phase/gw_range/max_entries nen doi filter tu khac la cache
        // miss. Xoa sach se vut luon ca du lieu bat bien (picks va diem cua GW
        // da chot, bootstrap-static) von khong he phu thuoc filter, khien lan
        // load sau phai crawl lai tu dau.
        //
        // Muon ep lay du lieu moi ngay thi dung nut Clear Cache.
        DataCache.clear();

        // Re-fetch current GW
        await fetchCurrentGW();

        // Trigger page-specific data reload with force refresh
        if (typeof reloadPageData === 'function') {
            reloadPageData(filters);
        }

        showToast('Filters applied', 'success');
    } catch (error) {
        console.error('Error applying filters:', error);
        showToast('Error reloading data', 'error');
    } finally {
        hideLoading();
    }
}

// === Handle Clear Cache ===
async function handleClearCache(event) {
    // Prevent form submission if button is inside form
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }

    try {
        showLoading('Clearing cache...');

        // Clear client-side cache
        DataCache.clear();

        // Clear server-side cache
        const response = await fetch('/api/cache/clear', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (data.success) {
            showToast('Cache cleared successfully. Refreshing current GW...', 'success');

            // Re-fetch current GW to ensure correct value after cache clear
            await fetchCurrentGW();

            // Reload current page data
            if (typeof reloadPageData === 'function') {
                const filters = getCurrentFilters();
                reloadPageData(filters);
            }
        } else {
            showToast('Failed to clear cache', 'error');
        }
    } catch (error) {
        console.error('Error clearing cache:', error);
        showToast('Error clearing cache', 'error');
    } finally {
        hideLoading();
    }
}

// === Loading Overlay ===
function showLoading(message = 'Loading...') {
    AppState.isLoading = true;
    const overlay = document.getElementById('loading-overlay');
    const loadingText = overlay.querySelector('.loading-text');
    
    if (loadingText) {
        loadingText.textContent = message;
    }
    
    overlay.classList.remove('hidden');
}

function hideLoading() {
    AppState.isLoading = false;
    const overlay = document.getElementById('loading-overlay');
    overlay.classList.add('hidden');
}

function updateProgress(current, total) {
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');
    
    const percentage = Math.round((current / total) * 100);
    
    if (progressFill) {
        progressFill.style.width = `${percentage}%`;
    }
    
    if (progressText) {
        progressText.textContent = `${percentage}%`;
    }
}

// === Toast Notifications ===
function showToast(message, type = 'info', duration = 3000) {
    const container = document.getElementById('toast-container');
    
    const toast = document.createElement('div');
    toast.className = `toast toast-${type} animate-slide-in`;
    toast.textContent = message;
    
    container.appendChild(toast);
    
    // Auto-remove after duration
    setTimeout(() => {
        toast.style.animation = 'slideOut var(--transition-base) ease-out';
        setTimeout(() => {
            container.removeChild(toast);
        }, 250);
    }, duration);
}

// === Local Storage ===
function savePreferences() {
    const preferences = {
        league_id: document.getElementById('league-id')?.value,
        phase: document.getElementById('phase')?.value,
        gw_start: document.getElementById('gw-start')?.value,
        gw_end: document.getElementById('gw-end')?.value,
        month_mapping: document.getElementById('month-mapping')?.value,
        max_entries: document.getElementById('max-entries')?.value,
    };
    
    localStorage.setItem('fpl_preferences', JSON.stringify(preferences));
}

function loadPreferences() {
    const saved = localStorage.getItem('fpl_preferences');
    
    if (saved) {
        try {
            const preferences = JSON.parse(saved);
            
            // Apply saved values
            Object.keys(preferences).forEach(key => {
                const element = document.getElementById(key.replace('_', '-'));
                if (element && preferences[key]) {
                    element.value = preferences[key];
                }
            });
            
            // Update app state
            AppState.currentLeagueId = preferences.league_id;
            AppState.currentPhase = preferences.phase;
        } catch (error) {
            console.error('Error loading preferences:', error);
        }
    }
}

// === Navigation ===
function setActiveNavLink() {
    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll('.nav-link');
    
    navLinks.forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        } else {
            link.classList.remove('active');
        }
    });
}

// === Tooltips (placeholder) ===
function initializeTooltips() {
    // Implement tooltip functionality if needed
}

// === Export CSV ===
async function exportToCSV(data, filename = 'export.csv') {
    try {
        const response = await fetch('/api/export/csv', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ data, filename })
        });
        
        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            
            showToast('Export successful', 'success');
        } else {
            throw new Error('Export failed');
        }
    } catch (error) {
        console.error('Error exporting:', error);
        showToast('Export failed', 'error');
    }
}

// === Utility Functions ===
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function formatNumber(num) {
    return new Intl.NumberFormat('en-US').format(num);
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return new Intl.DateTimeFormat('en-GB').format(date);
}

// === Error Handling ===
window.addEventListener('error', function(e) {
    console.error('Global error:', e.error);
});

window.addEventListener('unhandledrejection', function(e) {
    console.error('Unhandled promise rejection:', e.reason);
});
