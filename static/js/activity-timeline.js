/**
 * ActivityTimelineManager - Unified activity timeline for contact pages
 * Aggregates interactions, emails, tasks, files, and voice memos into a single view
 */
class ActivityTimelineManager {
    /**
     * @param {number} contactId - The contact ID to load activities for
     * @param {Object} config - Optional configuration
     */
    constructor(contactId, config = {}) {
        this.contactId = contactId;
        this.config = {
            containerId: config.containerId || 'activityTimelineContainer',
            filterContainerId: config.filterContainerId || 'activityTimelineFilters',
            loadMoreBtnId: config.loadMoreBtnId || 'timelineLoadMoreBtn',
            paginationInfoId: config.paginationInfoId || 'timelinePaginationInfo',
            ...config
        };

        this.activities = [];
        this.counts = {};
        this.currentFilter = 'all';
        this.currentPage = 1;
        this.perPage = 20;
        this.hasMore = false;
        this.isLoading = false;

        this.init();
    }

    /**
     * Initialize the timeline manager
     */
    init() {
        this.container = document.getElementById(this.config.containerId);
        this.filterContainer = document.getElementById(this.config.filterContainerId);
        this.loadMoreBtn = document.getElementById(this.config.loadMoreBtnId);
        this.paginationInfo = document.getElementById(this.config.paginationInfoId);

        this.bindEvents();
        this.loadActivities();
    }

    /**
     * Bind event listeners
     */
    bindEvents() {
        // Filter button clicks
        if (this.filterContainer) {
            this.filterContainer.addEventListener('click', (e) => {
                const filterBtn = e.target.closest('[data-filter]');
                if (filterBtn) {
                    this.setFilter(filterBtn.dataset.filter);
                }
            });
        }

        // Load more button
        if (this.loadMoreBtn) {
            this.loadMoreBtn.addEventListener('click', () => {
                this.loadMore();
            });
        }
    }

    /**
     * Load activities from the API
     * @param {boolean} append - Whether to append to existing activities
     */
    async loadActivities(append = false) {
        if (this.isLoading) return;

        this.isLoading = true;
        this.showLoading();

        try {
            const params = new URLSearchParams({
                filter: this.currentFilter,
                page: this.currentPage,
                per_page: this.perPage
            });

            const response = await fetch(`/contact/${this.contactId}/timeline?${params}`);
            const data = await response.json();

            if (data.success) {
                if (append) {
                    this.activities = [...this.activities, ...data.activities];
                } else {
                    this.activities = data.activities;
                }

                this.counts = data.counts;
                this.hasMore = data.pagination.has_next;

                this.render();
                this.updateFilterCounts();
                this.updatePagination(data.pagination);
            } else {
                console.error('Failed to load timeline:', data.error);
                this.showError('Failed to load activities');
            }
        } catch (error) {
            console.error('Error loading timeline:', error);
            this.showError('Failed to load activities');
        } finally {
            this.isLoading = false;
            this.hideLoading();
        }
    }

    /**
     * Set the current filter and reload
     * @param {string} filter - Filter type: all, interaction, email, task, file, voice_memo
     */
    setFilter(filter) {
        if (filter === this.currentFilter) return;

        this.currentFilter = filter;
        this.currentPage = 1;
        this.activities = [];

        // Update filter button states
        if (this.filterContainer) {
            this.filterContainer.querySelectorAll('[data-filter]').forEach(btn => {
                if (btn.dataset.filter === filter) {
                    btn.classList.remove('bg-slate-100', 'text-slate-600', 'hover:bg-slate-200');
                    btn.classList.add('bg-slate-800', 'text-white');
                } else {
                    btn.classList.add('bg-slate-100', 'text-slate-600', 'hover:bg-slate-200');
                    btn.classList.remove('bg-slate-800', 'text-white');
                }
            });
        }

        this.loadActivities();
    }

    /**
     * Load more activities (pagination)
     */
    loadMore() {
        if (!this.hasMore || this.isLoading) return;
        this.currentPage++;
        this.loadActivities(true);
    }

    /**
     * Refresh the timeline (public method for external calls)
     */
    refresh() {
        this.currentPage = 1;
        this.activities = [];
        this.loadActivities();
    }

    /**
     * Render activities to the DOM
     */
    render() {
        if (!this.container) return;

        if (this.activities.length === 0) {
            this.showEmpty();
            return;
        }

        this.hideEmpty();

        // Get or create the timeline list
        let timelineList = this.container.querySelector('.timeline-list');
        if (!timelineList) {
            timelineList = document.createElement('div');
            timelineList.className = 'timeline-list divide-y divide-slate-100';
            this.container.appendChild(timelineList);
        }

        // Clear existing items if not appending (page 1)
        if (this.currentPage === 1) {
            timelineList.innerHTML = '';
        }

        // Render each activity
        this.activities.slice((this.currentPage - 1) * this.perPage).forEach((activity, index) => {
            const element = this.renderActivity(activity);
            element.style.opacity = '0';
            element.style.transform = 'translateY(10px)';
            timelineList.appendChild(element);

            // Animate in
            setTimeout(() => {
                element.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
                element.style.opacity = '1';
                element.style.transform = 'translateY(0)';
            }, index * 30);
        });
    }

    /**
     * Render a single activity item
     * @param {Object} activity - Activity data
     * @returns {HTMLElement}
     */
    renderActivity(activity) {
        const div = document.createElement('div');
        div.className = 'timeline-item p-3 hover:bg-slate-50 transition-colors';
        div.dataset.activityId = activity.id;

        const iconColor = this.getIconColor(activity.color);
        const formattedDate = this.formatDate(activity.timestamp);
        const metadata = this.renderMetadata(activity);
        const actions = this.renderActions(activity);

        div.innerHTML = `
            <div class="flex items-start gap-3">
                <!-- Simple colored icon -->
                <i class="fas ${activity.icon} ${iconColor} text-sm mt-0.5 flex-shrink-0 w-4 text-center"></i>

                <!-- Activity Content -->
                <div class="flex-1 min-w-0">
                    <div class="flex items-center justify-between gap-2">
                        <div class="flex items-center gap-2 min-w-0">
                            <span class="text-sm font-medium text-slate-800 truncate">${this.escapeHtml(activity.title)}</span>
                            ${metadata}
                        </div>
                        <span class="text-xs text-slate-400 whitespace-nowrap">${formattedDate}</span>
                    </div>
                    ${activity.description ? `<p class="mt-0.5 text-sm text-slate-500 line-clamp-2">${this.escapeHtml(activity.description)}</p>` : ''}
                    ${actions}
                </div>
            </div>
        `;

        return div;
    }

    /**
     * Render metadata badges for an activity
     * @param {Object} activity
     * @returns {string} HTML string
     */
    renderMetadata(activity) {
        let badges = '';

        if (activity.type === 'interaction' && activity.metadata?.follow_up_date) {
            badges += `<span class="text-xs text-amber-600"><i class="fas fa-clock text-[10px]"></i></span>`;
        }

        if (activity.type === 'task' && activity.metadata?.priority === 'high') {
            badges += `<span class="text-xs text-red-500"><i class="fas fa-flag text-[10px]"></i></span>`;
        }

        if (activity.type === 'email' && activity.metadata?.has_attachments) {
            badges += `<span class="text-xs text-slate-400"><i class="fas fa-paperclip text-[10px]"></i></span>`;
        }

        if (activity.type === 'voice_memo' && activity.metadata?.has_transcription) {
            badges += `<span class="text-xs text-green-500"><i class="fas fa-file-alt text-[10px]"></i></span>`;
        }

        return badges;
    }

    /**
     * Render action buttons for an activity
     * @param {Object} activity
     * @returns {string} HTML string
     */
    renderActions(activity) {
        let actions = '';

        if (activity.type === 'file') {
            actions = `
                <div class="mt-1">
                    <button onclick="downloadContactFile(${activity.metadata?.file_id})"
                        class="text-xs text-slate-400 hover:text-emerald-600 transition-colors">
                        <i class="fas fa-download mr-1"></i>Download
                    </button>
                </div>
            `;
        }

        if (activity.type === 'voice_memo') {
            actions = `
                <div class="mt-1">
                    <button onclick="openVoiceMemoDetail(voiceMemoData[${activity.metadata?.memo_id}])"
                        class="text-xs text-slate-400 hover:text-rose-600 transition-colors">
                        <i class="fas fa-play mr-1"></i>Play
                    </button>
                </div>
            `;
        }

        if (activity.type === 'email' && activity.metadata?.thread_id) {
            actions = `
                <div class="mt-1">
                    <button onclick="scrollToEmailThread('${activity.metadata?.thread_id}')"
                        class="text-xs text-slate-400 hover:text-blue-600 transition-colors">
                        <i class="fas fa-external-link-alt mr-1"></i>View Thread
                    </button>
                </div>
            `;
        }

        return actions;
    }

    /**
     * Get Tailwind text color class for icon
     * @param {string} color
     * @returns {string}
     */
    getIconColor(color) {
        const colorMap = {
            'blue': 'text-blue-500',
            'purple': 'text-purple-500',
            'indigo': 'text-indigo-500',
            'green': 'text-green-500',
            'emerald': 'text-emerald-500',
            'orange': 'text-orange-500',
            'amber': 'text-amber-500',
            'red': 'text-red-500',
            'pink': 'text-pink-500',
            'cyan': 'text-cyan-500',
            'slate': 'text-slate-400'
        };
        return colorMap[color] || colorMap['slate'];
    }

    /**
     * Format a timestamp for display
     * @param {string} timestamp - ISO timestamp
     * @returns {string}
     */
    formatDate(timestamp) {
        if (!timestamp) return '';

        const date = new Date(timestamp);
        const now = new Date();
        const diffMs = now - date;
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

        if (diffDays === 0) {
            return 'Today';
        } else if (diffDays === 1) {
            return 'Yesterday';
        } else if (diffDays < 7) {
            return `${diffDays}d ago`;
        } else {
            return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
        }
    }

    /**
     * Update filter button counts
     */
    updateFilterCounts() {
        if (!this.filterContainer) return;

        this.filterContainer.querySelectorAll('[data-filter]').forEach(btn => {
            const filter = btn.dataset.filter;
            const countEl = btn.querySelector('.filter-count');
            if (countEl && this.counts[filter] !== undefined) {
                countEl.textContent = this.counts[filter];
            }
        });
    }

    /**
     * Update pagination info
     * @param {Object} pagination
     */
    updatePagination(pagination) {
        if (this.paginationInfo) {
            const showing = Math.min(this.currentPage * this.perPage, pagination.total);
            this.paginationInfo.textContent = `Showing ${showing} of ${pagination.total}`;
        }

        if (this.loadMoreBtn) {
            if (pagination.has_next) {
                this.loadMoreBtn.classList.remove('hidden');
            } else {
                this.loadMoreBtn.classList.add('hidden');
            }
        }
    }

    /**
     * Show loading state
     */
    showLoading() {
        if (!this.container) return;

        let loadingEl = this.container.querySelector('.timeline-loading');
        if (!loadingEl) {
            loadingEl = document.createElement('div');
            loadingEl.className = 'timeline-loading flex items-center justify-center py-8';
            loadingEl.innerHTML = `
                <div class="animate-spin rounded-full h-6 w-6 border-2 border-orange-500 border-t-transparent mr-2"></div>
                <span class="text-sm text-slate-500">Loading activities...</span>
            `;
            this.container.appendChild(loadingEl);
        }
        loadingEl.classList.remove('hidden');
    }

    /**
     * Hide loading state
     */
    hideLoading() {
        const loadingEl = this.container?.querySelector('.timeline-loading');
        if (loadingEl) {
            loadingEl.classList.add('hidden');
        }
    }

    /**
     * Show empty state
     */
    showEmpty() {
        if (!this.container) return;

        let emptyEl = this.container.querySelector('.timeline-empty');
        if (!emptyEl) {
            emptyEl = document.createElement('div');
            emptyEl.className = 'timeline-empty px-4 py-8 text-center';
            emptyEl.innerHTML = `
                <div class="w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center mx-auto mb-3">
                    <i class="fas fa-stream text-slate-400 text-xl"></i>
                </div>
                <p class="text-slate-500 text-sm">No activities yet</p>
                <p class="text-slate-400 text-xs mt-1">Activities will appear here as you interact with this contact</p>
            `;
            this.container.appendChild(emptyEl);
        }
        emptyEl.classList.remove('hidden');

        // Hide load more button
        if (this.loadMoreBtn) {
            this.loadMoreBtn.classList.add('hidden');
        }
    }

    /**
     * Hide empty state
     */
    hideEmpty() {
        const emptyEl = this.container?.querySelector('.timeline-empty');
        if (emptyEl) {
            emptyEl.classList.add('hidden');
        }
    }

    /**
     * Show error state
     * @param {string} message
     */
    showError(message) {
        if (!this.container) return;

        this.container.innerHTML = `
            <div class="px-4 py-8 text-center">
                <div class="w-12 h-12 rounded-full bg-red-100 flex items-center justify-center mx-auto mb-3">
                    <i class="fas fa-exclamation-triangle text-red-400 text-xl"></i>
                </div>
                <p class="text-slate-600 text-sm">${message}</p>
                <button onclick="window.activityTimeline.refresh()" class="mt-3 text-sm text-orange-600 hover:text-orange-700 font-medium">
                    <i class="fas fa-redo mr-1"></i>Try Again
                </button>
            </div>
        `;
    }

    /**
     * Escape HTML entities
     * @param {string} str
     * @returns {string}
     */
    escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ActivityTimelineManager;
}
