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
        
    }
    
    openModal() {
        this.state = 'modal';
        const panel = document.getElementById('bob-panel');
        
        // Add modal class and open
        panel.classList.add('modal');
        panel.classList.add('open');
        
        this.updateExpandButton();
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
        
        // Clear history on close
        this.clearMessages();
        
        // Clear server-side history
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
        document.getElementById('bob-messages').innerHTML = '';
        document.getElementById('bob-messages').classList.remove('active');
        document.getElementById('bob-welcome').classList.remove('hidden');
        this.removeAttachment();
        this.mentionedContacts = [];
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
    addMessage(role, content) {
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
        
        this.messages.push({ role, content });
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
            
            // Save to history
            if (fullResponse) {
                await fetch('/api/ai-chat/history', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        userMessage: message,
                        assistantResponse: fullResponse
                    })
                });
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
    
    formatMessage(text) {
        if (!text) return '';
        
        let formatted = text
            .replace(/\r\n/g, '\n')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
        
        // Headers
        formatted = formatted
            .replace(/### (.*$)/gm, '<h3>$1</h3>')
            .replace(/## (.*$)/gm, '<h2>$1</h2>')
            .replace(/# (.*$)/gm, '<h1>$1</h1>');
        
        // Code blocks
        formatted = formatted.replace(/```(\w+)?\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
        formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');
        
        // Bold and Italic
        formatted = formatted
            .replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*]+)\*/g, '<em>$1</em>');
        
        // Links
        formatted = formatted.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
        
        // Horizontal rule
        formatted = formatted.replace(/^---$/gm, '<hr>');
        
        // Lists
        const lines = formatted.split('\n');
        let result = [];
        let inList = false;
        let listType = null;
        
        for (const line of lines) {
            const ulMatch = line.match(/^(\s*)[-*]\s+(.+)$/);
            const olMatch = line.match(/^(\s*)\d+\.\s+(.+)$/);
            
            if (ulMatch) {
                if (!inList || listType !== 'ul') {
                    if (inList) result.push(`</${listType}>`);
                    result.push('<ul>');
                    inList = true;
                    listType = 'ul';
                }
                result.push(`<li>${ulMatch[2]}</li>`);
            } else if (olMatch) {
                if (!inList || listType !== 'ol') {
                    if (inList) result.push(`</${listType}>`);
                    result.push('<ol>');
                    inList = true;
                    listType = 'ol';
                }
                result.push(`<li>${olMatch[2]}</li>`);
            } else {
                if (inList) {
                    result.push(`</${listType}>`);
                    inList = false;
                    listType = null;
                }
                result.push(line);
            }
        }
        
        if (inList) result.push(`</${listType}>`);
        
        formatted = result.join('\n');
        
        // Paragraphs
        formatted = formatted
            .split('\n\n')
            .map(p => {
                p = p.trim();
                if (!p) return '';
                if (p.startsWith('<h') || p.startsWith('<pre') || 
                    p.startsWith('<ul') || p.startsWith('<ol') || 
                    p.startsWith('<hr') || p.startsWith('</')) {
                    return p;
                }
                return `<p>${p.replace(/\n/g, '<br>')}</p>`;
            })
            .filter(p => p)
            .join('');
        
        return formatted;
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    window.bobChat = new BOBChatPanel();
});
