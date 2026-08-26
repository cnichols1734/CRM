/**
 * B.O.B. AI Chat Panel - Breeze-style slide-in assistant
 * Business Optimization Buddy for Origen Realty CRM
 */

/**
 * Suggestions shown on the welcome screen, sampled at random each time it is
 * shown so the panel keeps advertising abilities the agent has not tried yet.
 *
 * Every entry must map to something the tool layer can actually do; see
 * services/bob_tools/registry.py. Do not add aspirational examples here,
 * because a starter that fails is worse than one that never appeared.
 *
 * mode 'send'  - self-contained, so it runs on click.
 * mode 'draft' - needs a name or detail from the agent, so it only fills the
 *                composer. A prompt ending in '@' opens the contact picker.
 */
const BOB_STARTERS = [
    // ---- Reads: answer a question about the book of business ----
    { group: 'ask', mode: 'send', icon: 'fa-calendar-day',
      label: "What's due today?",
      prompt: "What tasks are due today?" },
    { group: 'ask', mode: 'send', icon: 'fa-triangle-exclamation',
      label: 'What have I let slip?',
      prompt: 'What tasks am I overdue on?' },
    { group: 'ask', mode: 'send', icon: 'fa-calendar-week',
      label: "What's coming up this week?",
      prompt: 'What tasks do I have due over the next 7 days?' },
    { group: 'ask', mode: 'send', icon: 'fa-chart-column',
      label: 'How many contacts in each city?',
      prompt: 'How many contacts do I have in each city?' },
    { group: 'ask', mode: 'send', icon: 'fa-map-location-dot',
      label: 'Break my book down by ZIP',
      prompt: 'How many contacts do I have in each ZIP code?' },
    { group: 'ask', mode: 'send', icon: 'fa-layer-group',
      label: "What's in each of my groups?",
      prompt: 'How many contacts are in each of my contact groups?' },
    { group: 'ask', mode: 'send', icon: 'fa-address-book',
      label: 'How big is my book?',
      prompt: 'How many contacts do I have in total?' },
    { group: 'ask', mode: 'send', icon: 'fa-list-check',
      label: "What's on my personal list?",
      prompt: "What's on my personal to-do list?" },
    { group: 'ask', mode: 'send', icon: 'fa-circle-check',
      label: 'What did I finish this week?',
      prompt: 'Which tasks did I complete in the last 7 days?' },
    { group: 'ask', mode: 'draft', icon: 'fa-id-card',
      label: 'Catch me up on',
      prompt: 'Catch me up on @' },
    { group: 'ask', mode: 'draft', icon: 'fa-clipboard-list',
      label: 'What is open for',
      prompt: 'What tasks are still open for @' },

    // ---- Writes: do the work ----
    { group: 'do', mode: 'draft', icon: 'fa-phone',
      label: 'Log a call with',
      prompt: 'Log a call with @' },
    { group: 'do', mode: 'draft', icon: 'fa-comment-sms',
      label: 'Log a text to',
      prompt: 'Log a text message to @' },
    { group: 'do', mode: 'draft', icon: 'fa-clock',
      label: 'Set a follow-up for',
      prompt: 'Create a follow-up task for @' },
    { group: 'do', mode: 'draft', icon: 'fa-note-sticky',
      label: 'Add a note to',
      prompt: 'Add a note to @' },
    { group: 'do', mode: 'draft', icon: 'fa-user-plus',
      label: 'Add a new contact',
      prompt: 'Add a new contact: ' },
    { group: 'do', mode: 'draft', icon: 'fa-house-chimney',
      label: 'Book a showing for',
      prompt: 'Create a showing task for @' },
    { group: 'do', mode: 'draft', icon: 'fa-right-left',
      label: 'Move someone to a new group',
      prompt: 'Move @',
      suffix: 'into a different contact group' },
    { group: 'do', mode: 'draft', icon: 'fa-pen',
      label: 'Update details for',
      prompt: 'Update the phone number for @' },
    { group: 'do', mode: 'draft', icon: 'fa-thumbtack',
      label: 'Add to my personal list',
      prompt: 'Add this to my personal list: ' },
    { group: 'do', mode: 'draft', icon: 'fa-check-double',
      label: 'Check a task off',
      prompt: 'Mark this task as done: ' },

    // ---- Writes that chain several tools in one sentence ----
    { group: 'do', mode: 'draft', icon: 'fa-wand-magic-sparkles',
      label: 'Log a call and set the follow-up',
      prompt: 'Log a call with @',
      suffix: 'and set a follow-up task for Friday' },
    { group: 'do', mode: 'draft', icon: 'fa-wand-magic-sparkles',
      label: 'Note it and remind me later',
      prompt: 'Add a note to @',
      suffix: 'and remind me to call them next Tuesday' },
    { group: 'do', mode: 'draft', icon: 'fa-wand-magic-sparkles',
      label: 'Log a showing and move them along',
      prompt: 'Log a showing with @',
      suffix: 'and move them into my under-contract group' },
    { group: 'do', mode: 'draft', icon: 'fa-wand-magic-sparkles',
      label: 'Add a contact and a first follow-up',
      prompt: 'Add a new contact and set a follow-up task for next week: ' },
];

// Kept small on purpose: a wall of suggestions is as unhelpful as none. The
// rest are a click away behind "more".
const BOB_STARTER_COUNTS = { ask: 2, do: 3 };
const BOB_STARTER_STEP = 4;
const BOB_ACTIVE_CONV_KEY = 'bob.activeConversationId';
const BOB_ACTIVE_TX_KEY = 'bob.activeTransactionId';

class BOBChatPanel {
    constructor() {
        this.state = 'closed'; // 'closed' | 'side' | 'modal' | 'sheet'
        this._onViewport = null;
        this._mq = null;
        this.isTyping = false;
        this.messages = [];
        this.mentionedContacts = [];
        this.attachedImage = null;
        this.attachedFile = null;
        this.attachedImageFile = null;
        this.mentionSearchTimeout = null;
        this.selectedMentionIndex = 0;
        
        // Conversation state
        this.currentConversationId = null;
        this.conversations = [];
        this.conversationsLoaded = false;
        this._ensureInflight = null;
        
        this.init();
    }

    isNarrow() {
        return window.matchMedia('(max-width: 768px)').matches;
    }

    openForViewport({ skipEnsure = false } = {}) {
        if (this.isNarrow()) {
            this.openSheet({ skipEnsure });
        } else {
            this.openSide({ skipEnsure });
        }
    }

    clearSheetLayout() {
        const panel = document.getElementById('bob-panel');
        if (panel) {
            panel.style.top = '';
            panel.style.height = '';
            panel.style.left = '';
            panel.style.right = '';
            panel.style.width = '';
            panel.style.maxWidth = '';
        }
        this.unbindSheetViewport();
        document.body.classList.remove('bob-sheet-open');
        if (this.state !== 'sheet' && this.state !== 'modal') {
            document.body.style.overflow = '';
        }
    }

    bindSheetViewport() {
        this.unbindSheetViewport();
        this._onViewport = () => this.syncSheetViewport();
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', this._onViewport);
            window.visualViewport.addEventListener('scroll', this._onViewport);
        }
        window.addEventListener('resize', this._onViewport);
        this.syncSheetViewport();
    }

    unbindSheetViewport() {
        if (!this._onViewport) return;
        if (window.visualViewport) {
            window.visualViewport.removeEventListener('resize', this._onViewport);
            window.visualViewport.removeEventListener('scroll', this._onViewport);
        }
        window.removeEventListener('resize', this._onViewport);
        this._onViewport = null;
    }

    syncSheetViewport() {
        const panel = document.getElementById('bob-panel');
        if (!panel || this.state !== 'sheet') return;
        if (!this.isNarrow()) {
            this.openSide({ skipEnsure: true });
            return;
        }
        const vv = window.visualViewport;
        const height = vv ? vv.height : window.innerHeight;
        const top = vv ? vv.offsetTop : 0;
        panel.style.top = `${Math.round(top)}px`;
        panel.style.height = `${Math.round(height)}px`;
        panel.style.left = '0';
        panel.style.right = '0';
        panel.style.width = '100%';
        panel.style.maxWidth = '100%';
    }
    
    init() {
        this.createPanel();
        this.renderStarters();
        this.bindEvents();
        this._mq = window.matchMedia('(max-width: 768px)');
        this._onMq = () => {
            if (this.state === 'closed') return;
            if (this._mq.matches && this.state !== 'sheet') {
                this.openSheet({ skipEnsure: true });
            } else if (!this._mq.matches && this.state === 'sheet') {
                this.openSide({ skipEnsure: true });
            }
        };
        if (this._mq.addEventListener) {
            this._mq.addEventListener('change', this._onMq);
        } else if (this._mq.addListener) {
            this._mq.addListener(this._onMq);
        }
        // Restore sticky thread after refresh without auto-opening the panel.
        this.restoreStickyConversation({ openPanel: false });
    }

    persistStickyConversation() {
        try {
            if (this.currentConversationId) {
                sessionStorage.setItem(
                    BOB_ACTIVE_CONV_KEY,
                    String(this.currentConversationId),
                );
            } else {
                sessionStorage.removeItem(BOB_ACTIVE_CONV_KEY);
            }
            const pageEntity = this.resolvePageEntity();
            if (pageEntity.entityType === 'transaction' && pageEntity.entityId) {
                sessionStorage.setItem(
                    BOB_ACTIVE_TX_KEY,
                    String(pageEntity.entityId),
                );
            }
        } catch (e) {
            /* sessionStorage may be unavailable */
        }
    }

    async restoreStickyConversation({ openPanel = false } = {}) {
        let storedId = null;
        try {
            storedId = sessionStorage.getItem(BOB_ACTIVE_CONV_KEY);
        } catch (e) {
            return;
        }
        if (!storedId) return;
        const id = parseInt(storedId, 10);
        if (!id) return;
        try {
            await this.loadConversation(id);
            if (openPanel && this.state === 'closed') {
                this.openForViewport({ skipEnsure: true });
            }
        } catch (e) {
            try {
                sessionStorage.removeItem(BOB_ACTIVE_CONV_KEY);
            } catch (err) { /* ignore */ }
        }
    }

    resolvePageEntity() {
        try {
            const root = document.querySelector('[data-bob-entity-type][data-bob-entity-id]');
            if (root) {
                return {
                    entityType: root.getAttribute('data-bob-entity-type'),
                    entityId: parseInt(root.getAttribute('data-bob-entity-id'), 10) || null,
                };
            }
            const path = window.location.pathname || '';
            let m = path.match(/\/transactions\/(\d+)/);
            if (m) return { entityType: 'transaction', entityId: parseInt(m[1], 10) };
            m = path.match(/\/contacts\/(\d+)/);
            if (m) return { entityType: 'contact', entityId: parseInt(m[1], 10) };
        } catch (e) { /* ignore */ }
        return { entityType: null, entityId: null };
    }
    
    createPanel() {
        // Create overlay for modal mode
        const overlay = document.createElement('div');
        overlay.className = 'bob-overlay';
        overlay.id = 'bob-overlay';
        document.body.appendChild(overlay);
        
        // Sparkle icon SVG for Breeze-style branding
        const sparkleIconSVG = `<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
            <defs>
                <linearGradient id="bobGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" style="stop-color:#f97316"/>
                    <stop offset="50%" style="stop-color:#fb7185"/>
                    <stop offset="100%" style="stop-color:#f43f5e"/>
                </linearGradient>
            </defs>
            <path d="M16 2L18.5 12L28 14L18.5 16.5L16 28L13.5 16.5L4 14L13.5 12L16 2Z" fill="url(#bobGradient)"/>
            <path d="M8 6L9 9L12 10L9 11L8 14L7 11L4 10L7 9L8 6Z" fill="url(#bobGradient)" opacity="0.7"/>
            <path d="M24 20L25 23L28 24L25 25L24 28L23 25L20 24L23 23L24 20Z" fill="url(#bobGradient)" opacity="0.7"/>
        </svg>`;
        
        // Create main panel
        const panel = document.createElement('div');
        panel.className = 'bob-panel';
        panel.id = 'bob-panel';
        panel.innerHTML = `
            <!-- History Sidebar (for modal view) -->
            <div class="bob-history-sidebar" id="bob-history-sidebar">
                <div class="bob-history-header">
                    <button class="bob-new-chat-btn" id="bob-new-chat-btn">
                        <i class="fas fa-plus"></i>
                        <span>New Chat</span>
                    </button>
                </div>
                <div class="bob-history-list" id="bob-history-list">
                    <!-- Conversation items will be inserted here -->
                </div>
            </div>
            
            <!-- Main Chat Area -->
            <div class="bob-main-area">
                <!-- Header -->
                <div class="bob-header">
                    <div class="bob-header-brand">
                        <div class="bob-header-icon">${sparkleIconSVG}</div>
                        <div class="bob-header-title-group">
                            <span class="bob-header-title">B.O.B.</span>
                            <span class="bob-header-subtitle">AI Assistant</span>
                        </div>
                    </div>
                    <div class="bob-header-actions">
                        <!-- History dropdown for side panel -->
                        <div class="bob-history-dropdown-container" id="bob-history-dropdown-container">
                            <button class="bob-header-btn" id="bob-history-btn" title="Chat History">
                                <i class="fas fa-history"></i>
                            </button>
                            <div class="bob-history-dropdown" id="bob-history-dropdown">
                                <div class="bob-history-dropdown-header">
                                    <span>Recent Chats</span>
                                    <button class="bob-new-chat-btn-small" id="bob-new-chat-btn-dropdown">
                                        <i class="fas fa-plus"></i> New
                                    </button>
                                </div>
                                <div class="bob-history-dropdown-list" id="bob-history-dropdown-list">
                                    <!-- Recent conversations -->
                                </div>
                            </div>
                        </div>
                        <button class="bob-header-btn" id="bob-expand-btn" title="Expand to full screen">
                            <i class="fas fa-expand-alt"></i>
                        </button>
                        <button class="bob-header-btn close" id="bob-close-btn" title="Close">
                            <i class="fas fa-times"></i>
                        </button>
                    </div>
                </div>
            
            <!-- Content Area -->
            <div class="bob-content">
                <!-- Welcome State -->
                <div class="bob-welcome" id="bob-welcome">
                    <div class="bob-logo">
                        <div class="bob-logo-glow"></div>
                        ${sparkleIconSVG}
                    </div>
                    <div class="bob-title">B.O.B.</div>
                    <div class="bob-subtitle">Business Optimization Buddy</div>
                    <div class="bob-tagline">Tell me what you need done in your CRM.</div>

                    <div class="bob-starters" id="bob-starters"></div>
                </div>

                <!-- Messages Container -->
                <div class="bob-messages" id="bob-messages"></div>
            </div>

            <!-- Input Area -->
            <div class="bob-input-area">
                <!-- Image Preview -->
                <div class="bob-image-preview" id="bob-image-preview">
                    <img id="bob-image-preview-img" src="" alt="Attached image">
                    <button class="bob-image-preview-remove" id="bob-remove-image">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                
                <!-- File Preview (for non-image files) -->
                <div class="bob-file-preview" id="bob-file-preview">
                    <i id="bob-file-preview-icon" class="fas fa-file"></i>
                    <div class="bob-file-preview-info">
                        <span id="bob-file-preview-name"></span>
                        <span id="bob-file-preview-size"></span>
                    </div>
                    <button class="bob-file-preview-remove" id="bob-remove-file">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                
                <!-- Input Container -->
                <div class="bob-input-container">
                    <!-- Mentions Dropdown - inside container for proper positioning -->
                    <div class="bob-mentions-dropdown" id="bob-mentions-dropdown"></div>
                    <div class="bob-input-row">
                        <div class="bob-toolbar">
                            <button class="bob-tool-btn" id="bob-attach-btn" title="Attach file">
                                <i class="fas fa-paperclip"></i>
                            </button>
                            <button class="bob-tool-btn" id="bob-mention-btn" title="Mention contact">
                                <i class="fas fa-at"></i>
                            </button>
                        </div>
                        <textarea class="bob-textarea" id="bob-textarea" 
                            placeholder="Ask anything... Type @ to mention a contact" rows="1"></textarea>
                        <button class="bob-send-btn" id="bob-send-btn" title="Send">
                            <i class="fas fa-paper-plane"></i>
                        </button>
                    </div>
                </div>
                
                <!-- Hidden file input -->
                <input type="file" class="bob-file-input" id="bob-file-input" 
                    accept="image/jpeg,image/png,image/gif,image/webp,.csv,.pdf,.txt,.vcf,.docx,.xls,.xlsx,text/csv,text/vcard,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet">
            </div>
            </div><!-- end bob-main-area -->
        `;
        document.body.appendChild(panel);
    }
    
    bindEvents() {
        // Header buttons from base.html
        const desktopToggle = document.getElementById('bob-toggle-desktop');
        const mobileToggle = document.getElementById('bob-toggle-mobile');
        
        if (desktopToggle) {
            desktopToggle.addEventListener('click', () => this.toggle());
        }
        if (mobileToggle) {
            mobileToggle.addEventListener('click', () => this.toggle());
        }
        
        // Panel controls
        document.getElementById('bob-close-btn').addEventListener('click', () => this.close());
        document.getElementById('bob-expand-btn').addEventListener('click', () => this.toggleExpand());
        document.getElementById('bob-overlay').addEventListener('click', () => this.close());
        
        // Send message
        document.getElementById('bob-send-btn').addEventListener('click', () => this.sendMessage());
        
        // Textarea events
        const textarea = document.getElementById('bob-textarea');
        textarea.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        textarea.addEventListener('input', () => {
            this.autoResizeTextarea();
            this.handleMentionTrigger();
        });
        textarea.addEventListener('blur', (e) => {
            // Delay hiding mentions dropdown to allow click on items
            setTimeout(() => {
                // Only hide if not clicking on a mention item
                if (!document.querySelector('.bob-mention-item:hover')) {
                    this.hideMentionsDropdown();
                }
            }, 200);
        });
        
        // Attachment
        document.getElementById('bob-attach-btn').addEventListener('click', () => {
            document.getElementById('bob-file-input').click();
        });
        document.getElementById('bob-file-input').addEventListener('change', (e) => {
            this.handleFileSelect(e);
        });
        document.getElementById('bob-remove-image').addEventListener('click', () => {
            this.removeAttachment();
        });
        document.getElementById('bob-remove-file').addEventListener('click', () => {
            this.removeAttachment();
        });
        
        // Mention button
        document.getElementById('bob-mention-btn').addEventListener('click', () => {
            const textarea = document.getElementById('bob-textarea');
            textarea.value += '@';
            textarea.focus();
            this.handleMentionTrigger();
        });
        
        // Starter prompts. Delegated because the list is re-sampled on every
        // welcome screen. These go through the normal message path so the agent
        // sees the real tool activity, not a canned summary.
        document.getElementById('bob-starters').addEventListener('click', (e) => {
            const more = e.target.closest('.bob-starters-more');
            if (more) {
                this.showMoreStarters(more.dataset.more);
                return;
            }
            const btn = e.target.closest('.bob-starter');
            if (btn) this.useStarter(btn.dataset.starter);
        });
        
        // New Chat buttons
        document.getElementById('bob-new-chat-btn').addEventListener('click', () => {
            this.startNewChat();
        });
        document.getElementById('bob-new-chat-btn-dropdown').addEventListener('click', () => {
            this.startNewChat();
            this.hideHistoryDropdown();
        });
        
        // History dropdown toggle
        document.getElementById('bob-history-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleHistoryDropdown();
        });
        
        // Close dropdown when clicking outside
        document.addEventListener('click', (e) => {
            const dropdown = document.getElementById('bob-history-dropdown');
            const btn = document.getElementById('bob-history-btn');
            if (dropdown.classList.contains('visible') && 
                !dropdown.contains(e.target) && 
                e.target !== btn) {
                this.hideHistoryDropdown();
            }
        });
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            // Escape to close
            if (e.key === 'Escape' && this.state !== 'closed') {
                this.close();
            }
        });
    }
    
    toggle() {
        if (this.state === 'closed') {
            this.refreshStartersIfIdle();
            this.openForViewport();
        } else {
            this.close();
        }
    }
    
    openSide({ skipEnsure = false } = {}) {
        if (this.isNarrow()) {
            this.openSheet({ skipEnsure });
            return;
        }
        this.state = 'side';
        const panel = document.getElementById('bob-panel');
        const overlay = document.getElementById('bob-overlay');
        
        // Ensure clean state
        overlay.classList.remove('visible');
        panel.classList.remove('modal', 'sheet');
        this.clearSheetLayout();
        
        // Open panel
        panel.classList.add('open');
        
        this.updateExpandButton();

        // Load conversations for dropdown
        if (!this.conversationsLoaded) {
            this.loadConversations();
        }

        if (!skipEnsure) {
            // Seed briefing once when a deal has setup signal and none was sent yet.
            this.ensurePageConversation({ seedBriefing: true });
        }

        const textarea = document.getElementById('bob-textarea');
        if (textarea) textarea.focus();
    }

    openSheet({ skipEnsure = false } = {}) {
        this.state = 'sheet';
        const panel = document.getElementById('bob-panel');
        const overlay = document.getElementById('bob-overlay');

        overlay.classList.remove('visible');
        panel.classList.remove('modal');
        panel.classList.add('open', 'sheet');
        document.body.classList.add('bob-sheet-open');
        document.body.style.overflow = 'hidden';
        this.bindSheetViewport();
        this.updateExpandButton();

        if (!this.conversationsLoaded) {
            this.loadConversations();
        }

        if (!skipEnsure) {
            this.ensurePageConversation({ seedBriefing: true });
        }

        const textarea = document.getElementById('bob-textarea');
        if (textarea) textarea.focus();
    }

    async ensurePageConversation({ seedBriefing = false, forceBriefing = false } = {}) {
        const pageEntity = this.resolvePageEntity();
        if (pageEntity.entityType !== 'transaction' || !pageEntity.entityId) {
            if (!this.currentConversationId) {
                await this.restoreStickyConversation({ openPanel: false });
            }
            return null;
        }

        if (this._ensureInflight) {
            return this._ensureInflight;
        }

        this._ensureInflight = (async () => {
            const response = await fetch(
                `/api/ai-chat/transactions/${pageEntity.entityId}/ensure-conversation`,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        seed_briefing: !!seedBriefing,
                        force_briefing: !!forceBriefing,
                    }),
                },
            );
            if (!response.ok) {
                throw new Error('Failed to ensure transaction conversation');
            }
            const conversation = await response.json();
            await this.applyConversationPayload(conversation);
            return conversation;
        })()
            .catch((err) => {
                console.error('ensurePageConversation failed:', err);
                return null;
            })
            .finally(() => {
                this._ensureInflight = null;
            });

        return this._ensureInflight;
    }

    async openSetupBriefing({ force = false } = {}) {
        const pageEntity = this.resolvePageEntity();
        if (pageEntity.entityType !== 'transaction' || !pageEntity.entityId) {
            return null;
        }
        this.openForViewport({ skipEnsure: true });
        try {
            const response = await fetch(
                `/api/ai-chat/transactions/${pageEntity.entityId}/setup-briefing`,
                {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ force: !!force }),
                },
            );
            if (!response.ok) {
                await this.ensurePageConversation({ seedBriefing: true });
                return null;
            }
            const conversation = await response.json();
            await this.applyConversationPayload(conversation);
            return conversation;
        } catch (err) {
            console.error('openSetupBriefing failed:', err);
            return null;
        }
    }

    async applyConversationPayload(conversation) {
        if (!conversation || !conversation.id) return;
        this.currentConversationId = conversation.id;
        this.persistStickyConversation();

        const existing = this.conversations.find((c) => c.id === conversation.id);
        if (existing) {
            Object.assign(existing, conversation);
        } else {
            this.conversations.unshift(conversation);
        }

        this.messages = [];
        const messagesDiv = document.getElementById('bob-messages');
        if (!messagesDiv) return;
        messagesDiv.innerHTML = '';

        const msgs = conversation.messages || [];
        if (msgs.length > 0) {
            document.getElementById('bob-welcome').classList.add('hidden');
            messagesDiv.classList.add('active');
            for (const msg of msgs) {
                let attachment = null;
                if (msg.image_data) {
                    attachment = { imageData: msg.image_data };
                }
                if (msg.file_url) {
                    attachment = attachment || {};
                    attachment.file = {
                        url: msg.file_url,
                        name: msg.file_name,
                        type: msg.file_type,
                        size: msg.file_size,
                    };
                }
                this.addMessage(msg.role, msg.content, false, attachment);
            }
        } else {
            document.getElementById('bob-welcome').classList.remove('hidden');
            messagesDiv.classList.remove('active');
            this.renderStarters();
        }

        this.renderHistorySidebar();
        this.renderHistoryDropdown();
    }

    /** Re-sample suggestions on open, but never mid-conversation. */
    refreshStartersIfIdle() {
        const welcome = document.getElementById('bob-welcome');
        if (welcome && !welcome.classList.contains('hidden')) {
            this.renderStarters();
        }
    }
    
    openModal() {
        if (this.isNarrow()) {
            this.openSheet({ skipEnsure: true });
            return;
        }
        this.state = 'modal';
        const panel = document.getElementById('bob-panel');
        
        // Add modal class and open
        panel.classList.remove('sheet');
        this.clearSheetLayout();
        panel.classList.add('modal');
        panel.classList.add('open');
        
        this.updateExpandButton();
        
        // Load conversations for sidebar
        if (!this.conversationsLoaded) {
            this.loadConversations();
        }
    }
    
    updateExpandButton() {
        const btn = document.getElementById('bob-expand-btn');
        if (!btn) return;
        if (this.state === 'modal') {
            btn.innerHTML = '<i class="fas fa-compress-alt"></i>';
            btn.title = 'Collapse to sidebar';
        } else {
            btn.innerHTML = '<i class="fas fa-expand-alt"></i>';
            btn.title = 'Expand to full screen';
        }
    }
    
    toggleExpand() {
        if (this.isNarrow() || this.state === 'sheet') {
            return;
        }
        const panel = document.getElementById('bob-panel');
        
        if (this.state === 'side') {
            // Expand to fullscreen
            this.state = 'modal';
            panel.classList.add('modal');
            panel.classList.add('open');
            document.body.classList.add('bob-fullscreen-open');
            document.body.style.overflow = 'hidden';
            
        } else if (this.state === 'modal') {
            // Collapse to sidebar
            this.state = 'side';
            panel.classList.remove('modal');
            panel.classList.add('open');
            document.body.classList.remove('bob-fullscreen-open');
            document.body.style.overflow = '';
        }
        
        this.updateExpandButton();
    }
    
    async close() {
        const panel = document.getElementById('bob-panel');
        const overlay = document.getElementById('bob-overlay');
        
        // Animate out
        panel.classList.remove('open');
        overlay.classList.remove('visible');
        this.clearSheetLayout();
        
        // Remove body lock
        document.body.classList.remove('bob-fullscreen-open', 'bob-sheet-open');
        document.body.style.overflow = '';
        
        // Wait for animation to complete
        setTimeout(() => {
            panel.classList.remove('modal', 'sheet');
            this.state = 'closed';
            this.updateExpandButton();
        }, 300);

        // Sticky: keep the live thread + server session history. New Chat clears.
        this.persistStickyConversation();
    }
    
    clearMessages({ keepConversationId = false } = {}) {
        this.messages = [];
        if (!keepConversationId) {
            this.currentConversationId = null;
            try {
                sessionStorage.removeItem(BOB_ACTIVE_CONV_KEY);
            } catch (e) { /* ignore */ }
        }
        document.getElementById('bob-messages').innerHTML = '';
        document.getElementById('bob-messages').classList.remove('active');
        document.getElementById('bob-welcome').classList.remove('hidden');
        this.removeAttachment();
        this.mentionedContacts = [];

        // Fresh suggestions each time the welcome screen comes back.
        this.renderStarters();

        // Update active state in sidebar/dropdown
        this.renderHistorySidebar();
        this.renderHistoryDropdown();
    }
    
    autoResizeTextarea() {
        const textarea = document.getElementById('bob-textarea');
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
    }
    
    // ===== File Attachment =====
    handleFileSelect(e) {
        const file = e.target.files[0];
        if (!file) return;

        const name = (file.name || '').toLowerCase();
        if (name.endsWith('.doc') && !name.endsWith('.docx')) {
            alert('Legacy .doc files are not supported. Please upload a .docx instead.');
            return;
        }

        const allowedTypes = [
            'image/jpeg', 'image/png', 'image/gif', 'image/webp',
            'text/csv', 'application/pdf', 'text/plain', 'text/vcard', 'text/x-vcard',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ];
        const allowedExt = [
            '.csv', '.pdf', '.txt', '.vcf', '.docx', '.xls', '.xlsx',
            '.jpg', '.jpeg', '.png', '.gif', '.webp'
        ];
        const hasAllowedExt = allowedExt.some(ext => name.endsWith(ext));

        if (!allowedTypes.includes(file.type) && !hasAllowedExt) {
            alert('File type not supported. Please select an image, PDF, CSV, Excel, TXT, VCF, or DOCX file.');
            return;
        }
        
        if (file.size > 10 * 1024 * 1024) {
            alert('File size must be less than 10MB.');
            return;
        }
        
        if (file.type.startsWith('image/') || /\.(jpe?g|png|gif|webp)$/i.test(name)) {
            const reader = new FileReader();
            reader.onload = (event) => {
                this.attachedImage = event.target.result;
                this.attachedImageFile = file;
                this.attachedFile = null;
                document.getElementById('bob-image-preview-img').src = this.attachedImage;
                document.getElementById('bob-image-preview').classList.add('visible');
                document.getElementById('bob-file-preview').classList.remove('visible');
            };
            reader.readAsDataURL(file);
        } else {
            this.attachedFile = file;
            this.attachedImage = null;
            this.attachedImageFile = null;
            document.getElementById('bob-file-preview-icon').className = this.getFileIconClass(file.type);
            document.getElementById('bob-file-preview-name').textContent = file.name;
            document.getElementById('bob-file-preview-size').textContent = this.formatFileSize(file.size);
            document.getElementById('bob-file-preview').classList.add('visible');
            document.getElementById('bob-image-preview').classList.remove('visible');
        }
    }
    
    removeAttachment() {
        this.attachedImage = null;
        this.attachedImageFile = null;
        this.attachedFile = null;
        document.getElementById('bob-image-preview').classList.remove('visible');
        document.getElementById('bob-file-preview').classList.remove('visible');
        document.getElementById('bob-file-input').value = '';
    }
    
    // ===== Contact Mentions =====
    handleMentionTrigger() {
        const textarea = document.getElementById('bob-textarea');
        const text = textarea.value;
        const cursorPos = textarea.selectionStart;
        
        // Find @ before cursor (allow letters, numbers, and spaces after @)
        const textBeforeCursor = text.substring(0, cursorPos);
        const atMatch = textBeforeCursor.match(/@([a-zA-Z0-9 ]*)$/);
        
        if (atMatch !== null) {
            const query = atMatch[1];
            this.searchContacts(query);
        } else {
            this.hideMentionsDropdown();
        }
    }
    
    async searchContacts(query) {
        // Clear any pending search
        if (this.mentionSearchTimeout) {
            clearTimeout(this.mentionSearchTimeout);
        }
        
        // Debounce the search
        this.mentionSearchTimeout = setTimeout(async () => {
            try {
                const response = await fetch(`/api/ai-chat/search-contacts?q=${encodeURIComponent(query)}`);
                if (!response.ok) throw new Error('Search failed');
                
                const contacts = await response.json();
                
                // Only show if we still have an @ in the input
                const textarea = document.getElementById('bob-textarea');
                const cursorPos = textarea.selectionStart;
                const textBeforeCursor = textarea.value.substring(0, cursorPos);
                if (textBeforeCursor.match(/@([a-zA-Z0-9 ]*)$/)) {
                    this.showMentionsDropdown(contacts);
                }
            } catch (error) {
                console.error('Contact search error:', error);
                this.hideMentionsDropdown();
            }
        }, 200);
    }
    
    showMentionsDropdown(contacts) {
        const dropdown = document.getElementById('bob-mentions-dropdown');
        
        if (contacts.length === 0) {
            // Show "no results" message instead of hiding
            dropdown.innerHTML = `
                <div class="bob-mention-empty">
                    <span>No contacts found</span>
                </div>
            `;
            dropdown.classList.add('visible');
            return;
        }
        
        dropdown.innerHTML = contacts.map((contact, index) => `
            <div class="bob-mention-item ${index === 0 ? 'selected' : ''}" 
                 data-id="${contact.id}" 
                 data-name="${contact.name}">
                <div class="bob-mention-avatar">
                    ${contact.name.split(' ').map(n => n[0]).join('').substring(0, 2)}
                </div>
                <div class="bob-mention-info">
                    <div class="bob-mention-name">${contact.name}</div>
                    <div class="bob-mention-email">${contact.email || ''}</div>
                </div>
            </div>
        `).join('');
        
        dropdown.classList.add('visible');
        this.selectedMentionIndex = 0;
        
        // Bind click events
        dropdown.querySelectorAll('.bob-mention-item').forEach(item => {
            item.addEventListener('click', () => {
                this.selectMention(item.dataset.id, item.dataset.name);
            });
        });
    }
    
    hideMentionsDropdown() {
        document.getElementById('bob-mentions-dropdown').classList.remove('visible');
    }
    
    selectMention(contactId, contactName) {
        const textarea = document.getElementById('bob-textarea');
        const text = textarea.value;
        const cursorPos = textarea.selectionStart;
        
        // Find the @ position
        const textBeforeCursor = text.substring(0, cursorPos);
        const atMatch = textBeforeCursor.match(/@(\w*)$/);
        
        if (atMatch) {
            const atPos = cursorPos - atMatch[0].length;
            const newText = text.substring(0, atPos) + `@${contactName} ` + text.substring(cursorPos);
            textarea.value = newText;
            
            // Move cursor after the mention
            const newCursorPos = atPos + contactName.length + 2;
            textarea.setSelectionRange(newCursorPos, newCursorPos);
            
            // Track mentioned contact
            if (!this.mentionedContacts.find(c => c.id === contactId)) {
                this.mentionedContacts.push({ id: contactId, name: contactName });
            }
        }
        
        this.hideMentionsDropdown();
        textarea.focus();
    }
    
    // ===== Starter Prompts =====

    /** Indices into BOB_STARTERS for one group, in random order. */
    shuffledStarterIndices(group) {
        const indices = BOB_STARTERS
            .map((starter, index) => ({ starter, index }))
            .filter(entry => entry.starter.group === group)
            .map(entry => entry.index);

        for (let i = indices.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [indices[i], indices[j]] = [indices[j], indices[i]];
        }
        return indices;
    }

    /**
     * @param {boolean} reshuffle Re-sample the order and collapse back to the
     *   opening few. False when revealing more, so the rows already on screen
     *   stay put instead of shuffling under the cursor.
     */
    renderStarters(reshuffle = true) {
        const container = document.getElementById('bob-starters');
        if (!container) return;

        const groups = [
            { key: 'ask', label: 'Look something up' },
            { key: 'do', label: 'Get something done' },
        ];

        if (reshuffle || !this.starterState) {
            this.starterState = {};
            for (const { key } of groups) {
                this.starterState[key] = {
                    order: this.shuffledStarterIndices(key),
                    shown: BOB_STARTER_COUNTS[key],
                };
            }
        }

        container.innerHTML = groups.map(({ key, label }) => {
            const { order, shown } = this.starterState[key];

            const rows = order.slice(0, shown).map(index => {
                const starter = BOB_STARTERS[index];
                // Only trail an ellipsis when the blank the agent fills is at
                // the end of the sentence.
                const trailing = starter.mode === 'draft' && !starter.suffix
                    ? '<span class="bob-starter-fill">…</span>'
                    : '';
                return `
                    <button class="bob-starter" type="button" data-starter="${index}">
                        <i class="fas ${starter.icon}"></i>
                        <span>${this.escapeHtml(starter.label)}${trailing}</span>
                    </button>
                `;
            }).join('');

            const remaining = order.length - shown;
            const more = remaining > 0
                ? `<button class="bob-starters-more" type="button" data-more="${key}">
                       <i class="fas fa-plus"></i>
                       <span>${remaining} more</span>
                   </button>`
                : '';

            return `
                <div class="bob-starters-group">
                    <div class="bob-starters-label">${label}</div>
                    ${rows}${more}
                </div>
            `;
        }).join('') + `
            <p class="bob-starters-note">
                I'll show you a preview before editing or deleting anything.
            </p>
        `;
    }

    showMoreStarters(group) {
        const state = this.starterState?.[group];
        if (!state) return;

        state.shown = Math.min(state.shown + BOB_STARTER_STEP, state.order.length);
        this.renderStarters(false);
    }

    useStarter(index) {
        const starter = BOB_STARTERS[Number(index)];
        if (!starter || this.isTyping) return;

        const textarea = document.getElementById('bob-textarea');
        textarea.value = starter.prompt + (starter.suffix || '');
        this.autoResizeTextarea();

        if (starter.mode === 'send') {
            this.sendMessage();
            return;
        }

        // Drafts need a name the agent still has to choose. The caret goes at
        // the end of `prompt` rather than the end of the text, so a multi-step
        // suffix stays intact after the contact is inserted.
        textarea.focus();
        textarea.setSelectionRange(starter.prompt.length, starter.prompt.length);
        if (starter.prompt.endsWith('@')) {
            this.handleMentionTrigger();
        }
    }

    
    // ===== Message Handling =====
    addMessage(role, content, saveToArray = true, attachment = null) {
        const messagesDiv = document.getElementById('bob-messages');
        const messageEl = document.createElement('div');
        messageEl.className = `bob-message ${role}`;
        
        if (role === 'assistant') {
            messageEl.innerHTML = this.formatMessage(content);
        } else {
            // User message - may include attachments
            let html = '';
            
            // Handle image attachment
            if (attachment?.imageData) {
                const imgSrc = attachment.imageData.startsWith('data:') 
                    ? attachment.imageData 
                    : `data:image/jpeg;base64,${attachment.imageData}`;
                html += `
                    <div class="bob-message-attachment">
                        <img src="${imgSrc}" 
                             class="bob-message-image" 
                             alt="Attached image"
                             onclick="bobChat.showImageModal(this.src)">
                    </div>
                `;
            }
            
            // Handle file attachment
            if (attachment?.file) {
                const iconClass = this.getFileIconClass(attachment.file.type);
                const fileSize = this.formatFileSize(attachment.file.size);
                html += `
                    <div class="bob-file-card">
                        <i class="${iconClass} bob-file-icon"></i>
                        <div class="bob-file-info">
                            <span class="bob-file-name" title="${this.escapeHtml(attachment.file.name)}">${this.escapeHtml(attachment.file.name)}</span>
                            <span class="bob-file-size">${fileSize}</span>
                        </div>
                        ${attachment.file.url ? `<a href="${attachment.file.url}" class="bob-file-download" download="${this.escapeHtml(attachment.file.name)}" target="_blank"><i class="fas fa-download"></i></a>` : ''}
                    </div>
                `;
            }
            
            // Add text content if present
            if (content && content !== '[Image attached]' && content !== '[File attached]') {
                html += `<div class="bob-message-text">${this.escapeHtml(content)}</div>`;
            }
            
            // If we have HTML content from attachments, use it; otherwise use plain text
            if (html) {
                messageEl.innerHTML = html;
            } else {
                messageEl.textContent = content;
            }
        }
        
        messagesDiv.appendChild(messageEl);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
        
        if (saveToArray) {
            this.messages.push({ role, content, attachment });
        }
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    getFileIconClass(mimeType) {
        const iconMap = {
            'text/csv': 'fas fa-file-csv',
            'application/pdf': 'fas fa-file-pdf',
            'text/plain': 'fas fa-file-alt',
            'application/msword': 'fas fa-file-word',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'fas fa-file-word',
            'application/vnd.ms-excel': 'fas fa-file-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'fas fa-file-excel'
        };
        return iconMap[mimeType] || 'fas fa-file';
    }
    
    formatFileSize(bytes) {
        if (!bytes) return '';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }
    
    showImageModal(src) {
        // Create modal if it doesn't exist
        let modal = document.getElementById('bob-image-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'bob-image-modal';
            modal.className = 'bob-image-modal';
            modal.innerHTML = `
                <div class="bob-image-modal-backdrop"></div>
                <div class="bob-image-modal-content">
                    <img id="bob-image-modal-img" src="" alt="Full size image">
                    <button class="bob-image-modal-close"><i class="fas fa-times"></i></button>
                </div>
            `;
            document.body.appendChild(modal);
            
            // Close on backdrop click or close button
            modal.querySelector('.bob-image-modal-backdrop').addEventListener('click', () => this.hideImageModal());
            modal.querySelector('.bob-image-modal-close').addEventListener('click', () => this.hideImageModal());
            
            // Close on escape key
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && modal.classList.contains('visible')) {
                    this.hideImageModal();
                }
            });
        }
        
        modal.querySelector('#bob-image-modal-img').src = src;
        modal.classList.add('visible');
        document.body.style.overflow = 'hidden';
    }
    
    hideImageModal() {
        const modal = document.getElementById('bob-image-modal');
        if (modal) {
            modal.classList.remove('visible');
            document.body.style.overflow = '';
        }
    }
    
    showTyping() {
        this.hideTyping();
        const messagesDiv = document.getElementById('bob-messages');
        const typingEl = document.createElement('div');
        typingEl.className = 'bob-typing';
        typingEl.id = 'bob-typing-indicator';
        typingEl.innerHTML = `
            <span class="t-think" role="status">
                <span class="t-think-sizer" aria-hidden="true">Thinking...</span>
                <span class="t-think-text t-shimmer" data-text="Thinking...">Thinking...</span>
            </span>
        `;
        messagesDiv.appendChild(typingEl);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }
    
    hideTyping() {
        const typing = document.getElementById('bob-typing-indicator');
        if (typing) typing.remove();
    }
    
    async sendMessage() {
        const textarea = document.getElementById('bob-textarea');
        const message = textarea.value.trim();
        
        if (!message && !this.attachedImage && !this.attachedFile) return;
        if (this.isTyping) return;
        
        // Show messages area
        document.getElementById('bob-welcome').classList.add('hidden');
        document.getElementById('bob-messages').classList.add('active');
        
        // Create conversation if none exists (bind to transaction when on a deal page)
        if (!this.currentConversationId) {
            try {
                const pageEntity = this.resolvePageEntity();
                if (pageEntity.entityType === 'transaction' && pageEntity.entityId) {
                    await this.ensurePageConversation({ seedBriefing: false });
                } else {
                    const convResponse = await fetch('/api/ai-chat/conversations', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({}),
                    });
                    if (convResponse.ok) {
                        const conv = await convResponse.json();
                        this.currentConversationId = conv.id;
                        this.conversations.unshift(conv);
                        this.persistStickyConversation();
                    }
                }
            } catch (error) {
                console.error('Error creating conversation:', error);
            }
        }
        
        // Prepare attachment data (before clearing)
        const previewImage = this.attachedImage;
        const fileToUpload = this.attachedFile || this.attachedImageFile;
        
        // Build attachment for display
        let attachmentForDisplay = null;
        if (previewImage) {
            attachmentForDisplay = { imageData: previewImage };
        } else if (fileToUpload) {
            attachmentForDisplay = { 
                file: { 
                    name: fileToUpload.name, 
                    type: fileToUpload.type, 
                    size: fileToUpload.size,
                    url: null // Will be set after upload
                } 
            };
        }
        
        // Add user message with attachment preview
        this.addMessage('user', message, true, attachmentForDisplay);
        
        // Clear input and attachment
        textarea.value = '';
        this.autoResizeTextarea();
        this.removeAttachment();
        
        const messagesDiv = document.getElementById('bob-messages');
        this.isTyping = true;
        this.showTyping();
        document.getElementById('bob-send-btn').disabled = true;

        let aiMessageEl = null;
        const ensureStreamEl = () => {
            if (aiMessageEl) return aiMessageEl;
            this.hideTyping();
            aiMessageEl = document.createElement('div');
            aiMessageEl.className = 'bob-message assistant streaming';
            aiMessageEl.innerHTML = '<span class="bob-cursor">▌</span>';
            messagesDiv.appendChild(aiMessageEl);
            return aiMessageEl;
        };
        
        // Variables to track file upload result
        let fileUrl = null;
        let fileName = null;
        let fileType = null;
        let fileSize = null;
        let fileStoragePath = null;
        let attachmentRef = null;
        
        try {
            // Upload every attachment (including images) for a signed ref.
            if (fileToUpload) {
                const formData = new FormData();
                formData.append('file', fileToUpload);
                
                const uploadResponse = await fetch('/api/ai-chat/upload', {
                    method: 'POST',
                    body: formData
                });
                
                if (uploadResponse.ok) {
                    const uploadData = await uploadResponse.json();
                    fileUrl = uploadData.url;
                    fileName = uploadData.filename;
                    fileType = uploadData.type;
                    fileSize = uploadData.size;
                    fileStoragePath = uploadData.storage_path;
                    attachmentRef = uploadData.attachment_ref;
                    if (attachmentForDisplay?.file) {
                        attachmentForDisplay.file.url = fileUrl;
                    } else if (previewImage && fileUrl) {
                        // Prefer private signed URL for persisted image history.
                        attachmentForDisplay = {
                            file: {
                                name: fileName,
                                type: fileType,
                                size: fileSize,
                                url: fileUrl,
                            },
                            imageData: previewImage,
                        };
                    }
                } else {
                    const errorData = await uploadResponse.json();
                    throw new Error(errorData.error || 'File upload failed');
                }
            }
            
            const pageEntity = this.resolvePageEntity();

            const response = await fetch('/api/ai-chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: message,
                    // Untrusted garnish only — server hydrates entity from entityType/entityId.
                    pageContent: document.body.innerText.substring(0, 1500),
                    currentUrl: window.location.href,
                    entityType: pageEntity.entityType,
                    entityId: pageEntity.entityId,
                    clearHistory: false,
                    attachmentRef: attachmentRef,
                    conversationId: this.currentConversationId,
                    mentionedContactIds: this.mentionedContacts.map(c => c.id)
                })
            });
            
            if (!response.ok) throw new Error('Network response was not ok');
            
            // Read the stream
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullResponse = '';
            let buffer = '';

            // Action chips show what B.O.B. is doing in the CRM. Tool calls run
            // sequentially, so pairing each result with the oldest open chip is
            // correct.
            let activityEl = null;
            const ensureActivity = () => {
                ensureStreamEl();
                if (!activityEl) activityEl = this.createToolActivityStrip(messagesDiv, aiMessageEl);
                return activityEl;
            };
            const openChips = [];
            const pendingConfirms = [];

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                
                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        
                        if (data === '[DONE]') continue;
                        if (data.startsWith('[FULL_RESPONSE]') || data.includes('[FULL_RESPONSE]')) {
                            // Trailer only — never overwrite/paint the streamed bubble.
                            // Streamed chunks already built fullResponse.
                            continue;
                        }

                        if (data.startsWith('[BOB_TOOL_START]')) {
                            const payload = this.parseToolEvent(data, '[BOB_TOOL_START]');
                            if (payload) {
                                const label = payload.label || payload.name;
                                const think = document.querySelector('#bob-typing-indicator .t-think');
                                if (think && label && window.TMotion) TMotion.setThink(think, label);
                                openChips.push(this.addToolChip(ensureActivity(), payload));
                            }
                            messagesDiv.scrollTop = messagesDiv.scrollHeight;
                            continue;
                        }
                        if (data.startsWith('[BOB_TOOL_RESULT]')) {
                            const payload = this.parseToolEvent(data, '[BOB_TOOL_RESULT]');
                            if (payload) this.resolveToolChip(openChips.shift(), payload);
                            continue;
                        }
                        if (data.startsWith('[BOB_CONFIRM]')) {
                            const payload = this.parseToolEvent(data, '[BOB_CONFIRM]');
                            if (payload) {
                                pendingConfirms.push(payload);
                                this.resolveToolChip(openChips.shift(), payload);
                            }
                            continue;
                        }
                        
                        const unescaped = data
                            .replace(/\\n/g, '\n')
                            .replace(/\\r/g, '\r');
                        fullResponse += unescaped;
                        // Collapse any leftover literal \n from a double-escape
                        const display = fullResponse
                            .replace(/\\n/g, '\n')
                            .replace(/\\r/g, '\r');
                        ensureStreamEl();
                        aiMessageEl.innerHTML = this.formatMessage(display) + '<span class="bob-cursor">▌</span>';
                        messagesDiv.scrollTop = messagesDiv.scrollHeight;
                    }
                }
            }
            
            // Finalize message
            fullResponse = fullResponse
                .replace(/\\n/g, '\n')
                .replace(/\\r/g, '\r');
            if (fullResponse || pendingConfirms.length) ensureStreamEl();
            this.hideTyping();
            if (aiMessageEl) {
                aiMessageEl.innerHTML = this.formatMessage(fullResponse);
                aiMessageEl.classList.remove('streaming');
            }

            // Approval cards sit after B.O.B.'s explanation so the agent reads
            // the reasoning before deciding.
            pendingConfirms.forEach(payload => {
                this.addConfirmCard(messagesDiv, payload);
            });
            if (activityEl && !activityEl.childElementCount) activityEl.remove();
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
            
            // Save to history (both session and database)
            if (fullResponse) {
                const historyResponse = await fetch('/api/ai-chat/history', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        userMessage: message,
                        assistantResponse: fullResponse,
                        conversationId: this.currentConversationId,
                        // Keep a display thumbnail for images; durable bytes live in storage.
                        imageData: previewImage ? previewImage.split(',')[1] : null,
                        mentionedContactIds: this.mentionedContacts.map(c => c.id),
                        fileUrl: fileUrl,
                        fileName: fileName,
                        fileType: fileType,
                        fileSize: fileSize,
                        fileStoragePath: fileStoragePath
                    })
                });
                
                // Check if title was generated
                if (historyResponse.ok) {
                    const historyData = await historyResponse.json();
                    if (historyData.title) {
                        // Update local conversation with new title
                        const conv = this.conversations.find(c => c.id === this.currentConversationId);
                        if (conv) {
                            conv.title = historyData.title;
                            this.renderHistorySidebar();
                            this.renderHistoryDropdown();
                        }
                    }
                }
            }
            
        } catch (error) {
            console.error('Error:', error);
            ensureStreamEl();
            if (aiMessageEl) {
                aiMessageEl.innerHTML = this.formatMessage('Sorry, I encountered an error. Please try again.');
                aiMessageEl.classList.remove('streaming');
            }
        } finally {
            this.isTyping = false;
            this.hideTyping();
            document.getElementById('bob-send-btn').disabled = false;
            this.mentionedContacts = [];
            document.getElementById('bob-textarea').focus();
        }
    }
    
    // ===== Conversation History Management =====
    
    async loadConversations() {
        try {
            const response = await fetch('/api/ai-chat/conversations');
            if (!response.ok) throw new Error('Failed to load conversations');
            
            const data = await response.json();
            this.conversations = data.conversations || [];
            this.conversationsLoaded = true;
            
            this.renderHistorySidebar();
            this.renderHistoryDropdown();
        } catch (error) {
            console.error('Error loading conversations:', error);
        }
    }
    
    renderHistorySidebar() {
        const list = document.getElementById('bob-history-list');
        if (!list) return;
        
        if (this.conversations.length === 0) {
            list.innerHTML = `
                <div class="bob-history-empty">
                    <i class="fas fa-comments"></i>
                    <p>No conversations yet</p>
                    <p class="bob-history-empty-hint">Start chatting to save your conversations</p>
                </div>
            `;
            return;
        }
        
        // Group conversations by date
        const grouped = this.groupConversationsByDate(this.conversations);
        
        let html = '';
        for (const [label, convos] of Object.entries(grouped)) {
            if (convos.length === 0) continue;
            
            html += `<div class="bob-history-group">
                <div class="bob-history-group-label">${label}</div>
                ${convos.map(c => this.renderConversationItem(c)).join('')}
            </div>`;
        }
        
        list.innerHTML = html;
        
        // Bind click events
        list.querySelectorAll('.bob-history-item').forEach(item => {
            item.addEventListener('click', () => {
                this.loadConversation(parseInt(item.dataset.id));
            });
        });
        
        // Bind delete events
        list.querySelectorAll('.bob-history-delete').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.deleteConversation(parseInt(btn.dataset.id));
            });
        });
    }
    
    renderHistoryDropdown() {
        const list = document.getElementById('bob-history-dropdown-list');
        if (!list) return;
        
        // Show only recent 10 conversations in dropdown
        const recent = this.conversations.slice(0, 10);
        
        if (recent.length === 0) {
            list.innerHTML = `
                <div class="bob-history-empty-small">
                    <span>No recent chats</span>
                </div>
            `;
            return;
        }
        
        list.innerHTML = recent.map(c => `
            <div class="bob-history-dropdown-item ${this.currentConversationId === c.id ? 'active' : ''}" data-id="${c.id}">
                <div class="bob-history-dropdown-item-content">
                    <div class="bob-history-dropdown-title">${this.escapeHtml(c.title || 'Untitled Chat')}</div>
                    <div class="bob-history-dropdown-date">${this.formatRelativeDate(c.updated_at)}</div>
                </div>
                <button class="bob-history-dropdown-delete" data-id="${c.id}" title="Delete">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        `).join('');
        
        // Bind click events for loading conversation
        list.querySelectorAll('.bob-history-dropdown-item-content').forEach(item => {
            item.addEventListener('click', () => {
                const id = item.parentElement.dataset.id;
                this.loadConversation(parseInt(id));
                this.hideHistoryDropdown();
            });
        });
        
        // Bind delete events
        list.querySelectorAll('.bob-history-dropdown-delete').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.deleteConversation(parseInt(btn.dataset.id));
            });
        });
    }
    
    renderConversationItem(conversation) {
        const isActive = this.currentConversationId === conversation.id;
        return `
            <div class="bob-history-item ${isActive ? 'active' : ''}" data-id="${conversation.id}">
                <div class="bob-history-item-content">
                    <div class="bob-history-item-title">${this.escapeHtml(conversation.title || 'Untitled Chat')}</div>
                    <div class="bob-history-item-date">${this.formatRelativeDate(conversation.updated_at)}</div>
                </div>
                <button class="bob-history-delete" data-id="${conversation.id}" title="Delete conversation">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        `;
    }
    
    groupConversationsByDate(conversations) {
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const yesterday = new Date(today.getTime() - 86400000);
        const weekAgo = new Date(today.getTime() - 7 * 86400000);
        const monthAgo = new Date(today.getTime() - 30 * 86400000);
        
        const groups = {
            'Today': [],
            'Yesterday': [],
            'Previous 7 Days': [],
            'Previous 30 Days': [],
            'Older': []
        };
        
        for (const c of conversations) {
            const date = new Date(c.updated_at);
            if (date >= today) {
                groups['Today'].push(c);
            } else if (date >= yesterday) {
                groups['Yesterday'].push(c);
            } else if (date >= weekAgo) {
                groups['Previous 7 Days'].push(c);
            } else if (date >= monthAgo) {
                groups['Previous 30 Days'].push(c);
            } else {
                groups['Older'].push(c);
            }
        }
        
        return groups;
    }
    
    formatRelativeDate(dateStr) {
        if (!dateStr) return '';
        
        const date = new Date(dateStr);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);
        
        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        if (diffHours < 24) return `${diffHours}h ago`;
        if (diffDays < 7) return `${diffDays}d ago`;
        
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // =========================================================================
    // CRM ACTIONS (tool chips, approval cards, undo)
    // =========================================================================

    parseToolEvent(data, prefix) {
        try {
            return JSON.parse(data.slice(prefix.length));
        } catch (error) {
            console.error('Could not parse B.O.B. tool event', error);
            return null;
        }
    }

    createToolActivityStrip(messagesDiv, beforeEl) {
        const strip = document.createElement('div');
        strip.className = 'bob-tool-activity';
        messagesDiv.insertBefore(strip, beforeEl);
        return strip;
    }

    addToolChip(activityEl, payload) {
        const chip = document.createElement('div');
        chip.className = 'bob-tool-chip running';
        chip.innerHTML = `
            <span class="bob-tool-chip-spinner"></span>
            <span class="bob-tool-chip-label">${this.escapeHtml(payload.label || payload.name)}</span>
        `;
        activityEl.appendChild(chip);
        return chip;
    }

    resolveToolChip(chip, payload) {
        if (!chip) return;
        chip.classList.remove('running');

        let icon = 'fa-check';
        let state = 'done';
        if (!payload.ok) {
            icon = 'fa-triangle-exclamation';
            state = 'failed';
        } else if (payload.requires_confirmation) {
            icon = 'fa-hourglass-half';
            state = 'waiting';
        }
        chip.classList.add(state);

        const text = payload.summary || payload.label || payload.name;
        chip.innerHTML = `
            <i class="fas ${icon}"></i>
            <span class="bob-tool-chip-label">${this.escapeHtml(text)}</span>
        `;

        if (payload.ok && payload.undoable && payload.action_id) {
            const undo = document.createElement('button');
            undo.type = 'button';
            undo.className = 'bob-tool-chip-undo';
            undo.textContent = 'Undo';
            undo.addEventListener('click', () => this.undoAction(payload.action_id, chip, undo));
            chip.appendChild(undo);
        }
    }

    addConfirmCard(messagesDiv, payload) {
        const preview = payload.preview || {};
        const card = document.createElement('div');
        card.className = 'bob-confirm-card';
        if (preview.irreversible) card.classList.add('destructive');

        const isImport = preview.kind === 'contact_import' || payload.name === 'import_contacts';
        const title = payload.summary || payload.label || 'Confirm this change';
        const approveLabel = preview.irreversible
            ? 'Delete'
            : (isImport ? 'Import contacts' : 'Apply');
        card.innerHTML = `
            <div class="bob-confirm-header">
                <i class="fas ${preview.irreversible ? 'fa-triangle-exclamation' : 'fa-pen-to-square'}"></i>
                <span>${this.escapeHtml(title)}</span>
            </div>
            <div class="bob-confirm-body">${this.renderConfirmDetail(preview)}</div>
            <div class="bob-confirm-actions">
                <button type="button" class="bob-confirm-cancel">Cancel</button>
                <button type="button" class="bob-confirm-approve">
                    ${approveLabel}
                </button>
            </div>
            <div class="bob-confirm-status" hidden></div>
        `;
        messagesDiv.appendChild(card);

        card.querySelector('.bob-confirm-approve')
            .addEventListener('click', () => this.resolveConfirm(card, payload.action_id, true));
        card.querySelector('.bob-confirm-cancel')
            .addEventListener('click', () => this.resolveConfirm(card, payload.action_id, false));
        return card;
    }

    renderConfirmDetail(preview) {
        if (preview.kind === 'contact_import') {
            const sample = (preview.sample || []).map(row => {
                const bits = [row.name, row.email, row.phone].filter(Boolean).join(' · ');
                return `<li>${this.escapeHtml(bits)}</li>`;
            }).join('');
            const warnings = (preview.warnings || []).map(w =>
                `<li>${this.escapeHtml(w)}</li>`
            ).join('');
            return `
                <ul class="bob-confirm-changes">
                    <li><span class="bob-confirm-field">New contacts</span>
                        <span class="bob-confirm-to">${preview.create_count || 0}</span></li>
                    <li><span class="bob-confirm-field">Duplicates skipped</span>
                        <span class="bob-confirm-to">${preview.duplicate_count || 0}</span></li>
                    <li><span class="bob-confirm-field">Invalid rows</span>
                        <span class="bob-confirm-to">${preview.invalid_count || 0}</span></li>
                </ul>
                ${sample ? `<ul class="bob-confirm-changes">${sample}</ul>` : ''}
                ${warnings ? `<ul class="bob-confirm-changes bob-confirm-warning-list">${warnings}</ul>` : ''}
            `;
        }
        if (preview.warning) {
            return `<p class="bob-confirm-warning">${this.escapeHtml(preview.warning)}</p>`;
        }
        const changes = preview.changes || [];
        if (!changes.length) return '';

        const rows = changes.map(change => `
            <li>
                <span class="bob-confirm-field">${this.escapeHtml(this.humanizeField(change.field))}</span>
                <span class="bob-confirm-from">${this.escapeHtml(change.from || 'empty')}</span>
                <i class="fas fa-arrow-right"></i>
                <span class="bob-confirm-to">${this.escapeHtml(change.to || 'empty')}</span>
            </li>
        `).join('');
        return `<ul class="bob-confirm-changes">${rows}</ul>`;
    }

    humanizeField(field) {
        return (field || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    async resolveConfirm(card, actionId, approved) {
        const buttons = card.querySelectorAll('button');
        buttons.forEach(b => b.disabled = true);
        const status = card.querySelector('.bob-confirm-status');

        try {
            const response = await fetch('/api/ai-chat/tool/confirm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ actionId, approved })
            });
            const result = await response.json();

            card.classList.add('resolved');
            card.querySelector('.bob-confirm-actions').remove();
            status.hidden = false;
            status.classList.add(result.ok ? 'ok' : 'failed');
            status.textContent = result.ok
                ? (approved ? result.summary : 'Cancelled, nothing was changed')
                : (result.error || 'That did not go through.');

            if (result.ok && approved && result.undoable && result.actionId) {
                const undo = document.createElement('button');
                undo.type = 'button';
                undo.className = 'bob-tool-chip-undo';
                undo.textContent = 'Undo';
                undo.addEventListener('click', () => {
                    const chip = document.createElement('div');
                    chip.className = 'bob-tool-chip done';
                    status.appendChild(chip);
                    this.undoAction(result.actionId, chip, undo);
                });
                status.appendChild(document.createTextNode(' '));
                status.appendChild(undo);
            }
        } catch (error) {
            console.error('Confirm failed', error);
            buttons.forEach(b => b.disabled = false);
            status.hidden = false;
            status.classList.add('failed');
            status.textContent = 'Could not reach the server. Nothing was changed.';
        }
    }

    async undoAction(actionId, chip, button) {
        button.disabled = true;
        button.textContent = 'Undoing...';
        try {
            const response = await fetch('/api/ai-chat/tool/undo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ actionId })
            });
            const result = await response.json();

            if (result.ok) {
                chip.classList.remove('done');
                chip.classList.add('undone');
                chip.innerHTML = `
                    <i class="fas fa-rotate-left"></i>
                    <span class="bob-tool-chip-label">${this.escapeHtml(result.summary)}</span>
                `;
            } else {
                button.disabled = false;
                button.textContent = 'Undo';
                chip.setAttribute('title', result.error || 'Undo failed');
            }
        } catch (error) {
            console.error('Undo failed', error);
            button.disabled = false;
            button.textContent = 'Undo';
        }
    }
    
    toggleHistoryDropdown() {
        const dropdown = document.getElementById('bob-history-dropdown');
        if (dropdown.classList.contains('visible')) {
            this.hideHistoryDropdown();
        } else {
            // Load conversations if not loaded
            if (!this.conversationsLoaded) {
                this.loadConversations();
            }
            dropdown.classList.add('visible');
        }
    }
    
    hideHistoryDropdown() {
        document.getElementById('bob-history-dropdown').classList.remove('visible');
    }
    
    async startNewChat() {
        try {
            // Explicit New Chat stays unbound so History keeps the deal setup chat.
            const response = await fetch('/api/ai-chat/conversations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({}),
            });
            
            if (!response.ok) throw new Error('Failed to create conversation');
            
            const conversation = await response.json();

            // Clear UI first, then set the new id (old bug cleared the id).
            this.clearMessages();
            this.currentConversationId = conversation.id;
            this.persistStickyConversation();
            
            this.conversations.unshift(conversation);
            this.renderHistorySidebar();
            this.renderHistoryDropdown();
            
            document.getElementById('bob-textarea').focus();
            
        } catch (error) {
            console.error('Error creating new chat:', error);
        }
    }
    
    async loadConversation(conversationId) {
        try {
            const response = await fetch(`/api/ai-chat/conversations/${conversationId}`);
            if (!response.ok) throw new Error('Failed to load conversation');
            
            const conversation = await response.json();
            await this.applyConversationPayload(conversation);
            document.getElementById('bob-textarea').focus();
            
        } catch (error) {
            console.error('Error loading conversation:', error);
            throw error;
        }
    }
    
    async deleteConversation(conversationId) {
        if (!confirm('Delete this conversation?')) return;
        
        try {
            const response = await fetch(`/api/ai-chat/conversations/${conversationId}`, {
                method: 'DELETE'
            });
            
            if (!response.ok) throw new Error('Failed to delete conversation');
            
            // Remove from local list
            this.conversations = this.conversations.filter(c => c.id !== conversationId);
            
            // If deleted current conversation, start fresh
            if (this.currentConversationId === conversationId) {
                this.currentConversationId = null;
                this.clearMessages();
            }
            
            // Re-render lists
            this.renderHistorySidebar();
            this.renderHistoryDropdown();
            
        } catch (error) {
            console.error('Error deleting conversation:', error);
        }
    }
    
    formatMessage(text) {
        if (!text) return '';
        
        // Use marked.js for consistent markdown parsing
        if (typeof marked !== 'undefined') {
            // Configure marked options
            marked.setOptions({
                breaks: false,      // Don't convert single newlines to <br>
                gfm: true,          // GitHub Flavored Markdown
                headerIds: false,   // Don't add IDs to headers
                mangle: false,      // Don't mangle email addresses
                pedantic: false,
                smartLists: true,   // Better list handling
                smartypants: false  // Don't convert quotes to smart quotes
            });
            
            // Clean up the text before parsing
            let cleaned = text
                .replace(/\r\n/g, '\n')
                .replace(/\n{3,}/g, '\n\n')  // Collapse multiple blank lines
                .trim();
            
            // Parse markdown to HTML
            let html = marked.parse(cleaned);
            
            // Sanitize with DOMPurify if available (XSS protection)
            if (typeof DOMPurify !== 'undefined') {
                html = DOMPurify.sanitize(html, {
                    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'b', 'i', 'u', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
                                   'ul', 'ol', 'li', 'a', 'code', 'pre', 'blockquote', 'hr', 'table', 'thead', 
                                   'tbody', 'tr', 'th', 'td'],
                    ALLOWED_ATTR: ['href', 'target', 'rel']
                });
            }
            
            // Add target="_blank" to all links
            html = html.replace(/<a href="/g, '<a target="_blank" rel="noopener noreferrer" href="');
            
            return html;
        }
        
        // Fallback: basic formatting if marked.js not loaded
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*]+)\*/g, '<em>$1</em>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>');
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    window.bobChat = new BOBChatPanel();
});
