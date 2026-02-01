/**
 * B.O.B. AI Chat Panel - Breeze-style slide-in assistant
 * Business Optimization Buddy for Origen Realty CRM
 */

class BOBChatPanel {
    constructor() {
        this.state = 'closed'; // 'closed' | 'side' | 'modal'
        this.isTyping = false;
        this.messages = [];
        this.mentionedContacts = [];
        this.attachedImage = null;
        this.mentionSearchTimeout = null;
        this.selectedMentionIndex = 0;
        
        // Conversation state
        this.currentConversationId = null;
        this.conversations = [];
        this.conversationsLoaded = false;
        
        this.init();
    }
    
    init() {
        this.createPanel();
        this.bindEvents();
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
                    <div class="bob-tagline">Your AI-powered assistant for real estate success. Ask me anything about your contacts, tasks, or pipeline.</div>
                </div>
                
                <!-- Messages Container -->
                <div class="bob-messages" id="bob-messages"></div>
            </div>
            
            <!-- Quick Actions - Always visible -->
            <div class="bob-quick-actions">
                <div class="bob-quick-label">Quick actions</div>
                <div class="bob-quick-options visible">
                    <button class="bob-quick-option" data-action="summarize_tasks">
                        Summarize my open tasks
                    </button>
                    <button class="bob-quick-option" data-action="top_contacts">
                        Top 3 contacts to reach out to
                    </button>
                    <button class="bob-quick-option" data-action="pipeline_overview">
                        Quick pipeline overview
                    </button>
                </div>
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
                
                <!-- Input Container -->
                <div class="bob-input-container">
                    <!-- Mentions Dropdown - inside container for proper positioning -->
                    <div class="bob-mentions-dropdown" id="bob-mentions-dropdown"></div>
                    <div class="bob-input-row">
                        <div class="bob-toolbar">
                            <button class="bob-tool-btn" id="bob-attach-btn" title="Attach image">
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
                    accept="image/jpeg,image/png,image/gif,image/webp">
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
        
        // Mention button
        document.getElementById('bob-mention-btn').addEventListener('click', () => {
            const textarea = document.getElementById('bob-textarea');
            textarea.value += '@';
            textarea.focus();
            this.handleMentionTrigger();
        });
        
        // Quick actions - no longer need toggle, options are always visible
        
        document.querySelectorAll('.bob-quick-option').forEach(btn => {
            btn.addEventListener('click', () => {
                this.handleQuickAction(btn.dataset.action);
            });
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
            this.openSide();
        } else {
            this.close();
        }
    }
    
    openSide() {
        this.state = 'side';
        const panel = document.getElementById('bob-panel');
        const overlay = document.getElementById('bob-overlay');
        
        // Ensure clean state
        overlay.classList.remove('visible');
        panel.classList.remove('modal');
        
        // Open panel
        panel.classList.add('open');
        
        this.updateExpandButton();
        
        // Load conversations for dropdown
        if (!this.conversationsLoaded) {
            this.loadConversations();
        }
    }
    
    openModal() {
        this.state = 'modal';
        const panel = document.getElementById('bob-panel');
        
        // Add modal class and open
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
        if (this.state === 'modal') {
            btn.innerHTML = '<i class="fas fa-compress-alt"></i>';
            btn.title = 'Collapse to sidebar';
        } else {
            btn.innerHTML = '<i class="fas fa-expand-alt"></i>';
            btn.title = 'Expand to full screen';
        }
    }
    
    toggleExpand() {
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
        
        // Remove body lock
        document.body.classList.remove('bob-fullscreen-open');
        document.body.style.overflow = '';
        
        // Wait for animation to complete
        setTimeout(() => {
            panel.classList.remove('modal');
            this.state = 'closed';
            this.updateExpandButton();
        }, 300);
        
        // Clear local state (but keep database history)
        this.clearMessages();
        
        // Clear server-side session history (not database)
        try {
            await fetch('/api/ai-chat/clear', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
        } catch (error) {
            console.error('Error clearing chat history:', error);
        }
    }
    
    clearMessages() {
        this.messages = [];
        this.currentConversationId = null;
        document.getElementById('bob-messages').innerHTML = '';
        document.getElementById('bob-messages').classList.remove('active');
        document.getElementById('bob-welcome').classList.remove('hidden');
        this.removeAttachment();
        this.mentionedContacts = [];
        
        // Update active state in sidebar/dropdown
        this.renderHistorySidebar();
        this.renderHistoryDropdown();
    }
    
    autoResizeTextarea() {
        const textarea = document.getElementById('bob-textarea');
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
    }
    
    // ===== Image Attachment =====
    handleFileSelect(e) {
        const file = e.target.files[0];
        if (!file) return;
        
        // Validate file type
        if (!file.type.startsWith('image/')) {
            alert('Please select an image file.');
            return;
        }
        
        // Validate file size (max 10MB)
        if (file.size > 10 * 1024 * 1024) {
            alert('Image size must be less than 10MB.');
            return;
        }
        
        // Read and preview
        const reader = new FileReader();
        reader.onload = (event) => {
            this.attachedImage = event.target.result;
            document.getElementById('bob-image-preview-img').src = this.attachedImage;
            document.getElementById('bob-image-preview').classList.add('visible');
        };
        reader.readAsDataURL(file);
    }
    
    removeAttachment() {
        this.attachedImage = null;
        document.getElementById('bob-image-preview').classList.remove('visible');
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
    
    // ===== Quick Actions =====
    async handleQuickAction(action) {
        // Show the messages area
        document.getElementById('bob-welcome').classList.add('hidden');
        document.getElementById('bob-messages').classList.add('active');
        
        // Add a user message showing what was requested
        const actionLabels = {
            'summarize_tasks': 'Summarize my open tasks',
            'top_contacts': 'Who are my top 3 contacts to reach out to?',
            'pipeline_overview': 'Give me a quick pipeline overview'
        };
        
        this.addMessage('user', actionLabels[action]);
        
        // Show typing indicator
        this.showTyping();
        
        try {
            const response = await fetch('/api/ai-chat/quick-action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action })
            });
            
            if (!response.ok) throw new Error('Quick action failed');
            
            const data = await response.json();
            this.hideTyping();
            this.addMessage('assistant', data.response);
            
        } catch (error) {
            console.error('Quick action error:', error);
            this.hideTyping();
            this.addMessage('assistant', 'Sorry, I encountered an error. Please try again.');
        }
    }
    
    // ===== Message Handling =====
    addMessage(role, content, saveToArray = true) {
        const messagesDiv = document.getElementById('bob-messages');
        const messageEl = document.createElement('div');
        messageEl.className = `bob-message ${role}`;
        
        if (role === 'assistant') {
            messageEl.innerHTML = this.formatMessage(content);
        } else {
            messageEl.textContent = content;
        }
        
        messagesDiv.appendChild(messageEl);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
        
        if (saveToArray) {
            this.messages.push({ role, content });
        }
    }
    
    showTyping() {
        this.isTyping = true;
        const messagesDiv = document.getElementById('bob-messages');
        const typingEl = document.createElement('div');
        typingEl.className = 'bob-typing';
        typingEl.id = 'bob-typing-indicator';
        typingEl.innerHTML = `
            <span class="bob-typing-text">Thinking...</span>
            <div class="bob-typing-dots">
                <span></span><span></span><span></span>
            </div>
        `;
        messagesDiv.appendChild(typingEl);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }
    
    hideTyping() {
        this.isTyping = false;
        const typing = document.getElementById('bob-typing-indicator');
        if (typing) typing.remove();
    }
    
    async sendMessage() {
        const textarea = document.getElementById('bob-textarea');
        const message = textarea.value.trim();
        
        if (!message && !this.attachedImage) return;
        if (this.isTyping) return;
        
        // Show messages area
        document.getElementById('bob-welcome').classList.add('hidden');
        document.getElementById('bob-messages').classList.add('active');
        
        // Create conversation if none exists
        if (!this.currentConversationId) {
            try {
                const convResponse = await fetch('/api/ai-chat/conversations', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                if (convResponse.ok) {
                    const conv = await convResponse.json();
                    this.currentConversationId = conv.id;
                    this.conversations.unshift(conv);
                }
            } catch (error) {
                console.error('Error creating conversation:', error);
            }
        }
        
        // Add user message
        this.addMessage('user', message || '[Image attached]');
        
        // Clear input
        textarea.value = '';
        this.autoResizeTextarea();
        
        // Prepare the image data if attached
        const imageData = this.attachedImage ? this.attachedImage.split(',')[1] : null;
        this.removeAttachment();
        
        // Create streaming message element
        const messagesDiv = document.getElementById('bob-messages');
        const aiMessageEl = document.createElement('div');
        aiMessageEl.className = 'bob-message assistant streaming';
        aiMessageEl.innerHTML = '<span class="bob-cursor">▌</span>';
        messagesDiv.appendChild(aiMessageEl);
        
        this.isTyping = true;
        document.getElementById('bob-send-btn').disabled = true;
        
        try {
            const response = await fetch('/api/ai-chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message,
                    pageContent: document.body.innerText.substring(0, 3000),
                    currentUrl: window.location.href,
                    clearHistory: false,
                    image: imageData,
                    mentionedContactIds: this.mentionedContacts.map(c => c.id)
                })
            });
            
            if (!response.ok) throw new Error('Network response was not ok');
            
            // Read the stream
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullResponse = '';
            let buffer = '';
            
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
                        if (data.startsWith('[FULL_RESPONSE]')) {
                            const match = data.match(/\[FULL_RESPONSE\]([\s\S]*)\[\/FULL_RESPONSE\]/);
                            if (match) fullResponse = match[1];
                            continue;
                        }
                        
                        const unescaped = data.replace(/\\n/g, '\n').replace(/\\r/g, '\r');
                        fullResponse += unescaped;
                        aiMessageEl.innerHTML = this.formatMessage(fullResponse) + '<span class="bob-cursor">▌</span>';
                        messagesDiv.scrollTop = messagesDiv.scrollHeight;
                    }
                }
            }
            
            // Finalize message
            aiMessageEl.innerHTML = this.formatMessage(fullResponse);
            aiMessageEl.classList.remove('streaming');
            
            // Save to history (both session and database)
            if (fullResponse) {
                const historyResponse = await fetch('/api/ai-chat/history', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        userMessage: message,
                        assistantResponse: fullResponse,
                        conversationId: this.currentConversationId,
                        imageData: imageData,
                        mentionedContactIds: this.mentionedContacts.map(c => c.id)
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
            aiMessageEl.innerHTML = this.formatMessage('Sorry, I encountered an error. Please try again.');
            aiMessageEl.classList.remove('streaming');
        } finally {
            this.isTyping = false;
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
            // Create new conversation on server
            const response = await fetch('/api/ai-chat/conversations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            
            if (!response.ok) throw new Error('Failed to create conversation');
            
            const conversation = await response.json();
            this.currentConversationId = conversation.id;
            
            // Clear UI
            this.clearMessages();
            
            // Add to conversations list and re-render
            this.conversations.unshift(conversation);
            this.renderHistorySidebar();
            this.renderHistoryDropdown();
            
            // Focus textarea
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
            this.currentConversationId = conversationId;
            
            // Clear current messages
            this.messages = [];
            const messagesDiv = document.getElementById('bob-messages');
            messagesDiv.innerHTML = '';
            
            // Show messages area
            document.getElementById('bob-welcome').classList.add('hidden');
            messagesDiv.classList.add('active');
            
            // Render messages
            if (conversation.messages && conversation.messages.length > 0) {
                for (const msg of conversation.messages) {
                    this.addMessage(msg.role, msg.content, false);
                }
            }
            
            // Update sidebar/dropdown to show active state
            this.renderHistorySidebar();
            this.renderHistoryDropdown();
            
            // Focus textarea
            document.getElementById('bob-textarea').focus();
            
        } catch (error) {
            console.error('Error loading conversation:', error);
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
