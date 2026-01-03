// AI Chat Widget
class AIChatWidget {
    constructor() {
        // Don't initialize on mobile devices
        if (window.innerWidth < 768) {
            return;
        }
        
        this.isOpen = false;
        this.createChatIcon();
        this.createChatBox();
        this.messages = [];
        this.isTyping = false;
    }

    createChatIcon() {
        const icon = document.createElement('div');
        icon.className = 'ai-chat-icon';
        icon.innerHTML = `<i class="fas fa-user-tie"></i>`;
        icon.addEventListener('click', () => this.toggleChat());
        document.body.appendChild(icon);
    }

    createChatBox() {
        const chatBox = document.createElement('div');
        chatBox.className = 'ai-chat-box';
        chatBox.style.display = 'none';
        chatBox.innerHTML = `
            <div class="ai-chat-header">
                <span>B.O.B. - Your Business Optimization Buddy</span>
                <button class="ai-chat-close">&times;</button>
            </div>
            <div class="ai-chat-messages">
                <div class="ai-message">Hi, I'm BOB! 👋 Your Business Optimization Buddy. I'm here to assist you with any questions about real estate. How can I help you today?</div>
            </div>
            <div class="ai-chat-input">
                <textarea placeholder="Type your question here..." id="ai-chat-textarea"></textarea>
                <button class="ai-send-button" id="ai-send-button"><i class="fas fa-paper-plane"></i></button>
            </div>
        `;
        document.body.appendChild(chatBox);

        // Create typing indicator
        const typingIndicator = document.createElement('div');
        typingIndicator.className = 'typing-indicator';
        typingIndicator.id = 'typing-indicator';
        typingIndicator.innerHTML = `
            <div class="typing-indicator-content">
                <div class="typing-indicator-text">BOB is thinking...</div>
                <div class="typing-indicator-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        `;

        // Add typing indicator to messages container
        const messagesContainer = chatBox.querySelector('.ai-chat-messages');
        messagesContainer.appendChild(typingIndicator);

        // Event listeners
        chatBox.querySelector('.ai-chat-close').addEventListener('click', () => this.toggleChat());
        chatBox.querySelector('.ai-send-button').addEventListener('click', () => this.sendMessage());
        chatBox.querySelector('textarea').addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
    }

    async toggleChat() {
        const chatBox = document.querySelector('.ai-chat-box');
        this.isOpen = !this.isOpen;
        chatBox.style.display = this.isOpen ? 'flex' : 'none';
        
        // Clear chat history when closing
        if (!this.isOpen) {
            // Clear visual messages
            const messagesDiv = document.querySelector('.ai-chat-messages');
            // Keep only the initial greeting message and recreate the typing indicator
            messagesDiv.innerHTML = `
                <div class="ai-message">Hi, I'm BOB! 👋 Your Business Optimization Buddy. I'm here to assist you with any questions about real estate. How can I help you today?</div>
                <div class="typing-indicator" id="typing-indicator" style="display: none;">
                    <div class="typing-indicator-content">
                        <div class="typing-indicator-text">BOB is thinking...</div>
                        <div class="typing-indicator-dots">
                            <span></span>
                            <span></span>
                            <span></span>
                        </div>
                    </div>
                </div>
            `;
            
            // Clear server-side history
            try {
                await fetch('/api/ai-chat/clear', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    }
                });
            } catch (error) {
                console.error('Error clearing chat history:', error);
            }
        }
    }

    showTypingIndicator() {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) {
            // Remove the indicator from its current position
            indicator.remove();

            // Add it to the end of the messages container
            const messagesDiv = document.querySelector('.ai-chat-messages');
            messagesDiv.appendChild(indicator);

            // Show the indicator
            this.isTyping = true;
            indicator.style.display = 'flex';

            // Scroll to show the typing indicator
            setTimeout(() => {
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }, 100);
        }
    }

    hideTypingIndicator() {
        const indicator = document.getElementById('typing-indicator');
        if (indicator) {
            this.isTyping = false;
            indicator.style.display = 'none';
        }
    }

    async sendMessage() {
        const textarea = document.getElementById('ai-chat-textarea');
        const sendButton = document.getElementById('ai-send-button');
        const message = textarea.value.trim();

        if (!message || this.isTyping) return;

        // Disable input while processing
        textarea.disabled = true;
        sendButton.disabled = true;

        // Clear any existing typing indicators first
        this.hideTypingIndicator();

        // Add user message
        this.addMessageToChat('user', message);
        textarea.value = '';

        // Show typing indicator after user message
        this.showTypingIndicator();

        try {
            const response = await fetch('/api/ai-chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    message: message,
                    pageContent: document.body.innerText,
                    currentUrl: window.location.href,
                    clearHistory: false  // Add this flag
                })
            });

            if (!response.ok) {
                throw new Error('Network response was not ok');
            }

            const data = await response.json();

            // Artificial delay to ensure typing indicator is visible
            await new Promise(resolve => setTimeout(resolve, 1000));

            // Hide typing indicator and show response
            this.hideTypingIndicator();
            this.addMessageToChat('ai', data.response);

        } catch (error) {
            console.error('Error:', error);
            this.hideTypingIndicator();
            this.addMessageToChat('ai', 'Sorry, I encountered an error. Please try again.');
        } finally {
            // Re-enable input
            textarea.disabled = false;
            sendButton.disabled = false;
            textarea.focus();
        }
    }

    formatMessage(text) {
        // First, aggressively normalize whitespace
        let formatted = text
            .replace(/\r\n/g, '\n')           // Normalize line endings
            .replace(/\n{3,}/g, '\n\n')       // Collapse 3+ newlines to 2
            .replace(/[ \t]+$/gm, '')         // Remove trailing whitespace from lines
            .replace(/^[ \t]+/gm, (match) => { // Preserve only leading spaces for indentation
                return match.replace(/\t/g, '  '); // Convert tabs to 2 spaces
            })
            .trim();

        // Convert markdown-style formatting
        formatted = formatted
            // Headers
            .replace(/### (.*$)/gm, '<h3 class="ai-chat-h3">$1</h3>')
            .replace(/## (.*$)/gm, '<h2 class="ai-chat-h2">$1</h2>')
            .replace(/# (.*$)/gm, '<h1 class="ai-chat-h1">$1</h1>')

            // Code blocks with language support
            .replace(/```(\w+)?\n([\s\S]*?)```/g, '<pre><code class="language-$1">$2</code></pre>')

            // Inline code
            .replace(/`([^`]+)`/g, '<code>$1</code>')

            // Bold and Italic
            .replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*]+)\*/g, '<em>$1</em>')

            // Links
            .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

        // Handle special blocks (like ---)
        formatted = formatted.replace(/^---$/gm, '<hr class="message-divider">');

        // Process lists - simplified and cleaner approach
        let lines = formatted.split('\n');
        let result = [];
        let listStack = []; // Stack of {type: 'ul'|'ol', indent: number}

        for (let i = 0; i < lines.length; i++) {
            let line = lines[i];

            // Check for headings with colons (like "Suggested touch:")
            const headingMatch = line.match(/^\*\*([^*]+)\*\*$/);
            if (headingMatch && !line.match(/^\s*[\-\*\d]/)) {
                // Close all open lists before heading
                while (listStack.length > 0) {
                    result.push(`</li></${listStack.pop().type}>`);
                }
                result.push(`<strong>${headingMatch[1]}</strong>`);
                continue;
            }

            // Check for list items (-, *, or 1.)
            const listMatch = line.match(/^(\s*)([\-\*]|\d+\.)\s+(.+)$/);
            if (listMatch) {
                const [, indent, marker, content] = listMatch;
                const indentLevel = Math.floor(indent.length / 2);
                const isOrdered = /\d+\./.test(marker);
                const listType = isOrdered ? 'ol' : 'ul';

                // Close lists that are deeper than current indent
                while (listStack.length > 0 && listStack[listStack.length - 1].indent > indentLevel) {
                    const closed = listStack.pop();
                    result.push(`</li></${closed.type}>`);
                }

                // Check if we're at the same level but need to handle list type change or same list continuation
                if (listStack.length > 0 && listStack[listStack.length - 1].indent === indentLevel) {
                    // Same indent level
                    if (listStack[listStack.length - 1].type === listType) {
                        // Same list type - just close prev item and add new one
                        result.push('</li>');
                        result.push(`<li>${content}`);
                    } else {
                        // Different list type - close old list, start new one
                        const closed = listStack.pop();
                        result.push(`</li></${closed.type}>`);
                        result.push(`<${listType}>`);
                        listStack.push({type: listType, indent: indentLevel});
                        result.push(`<li>${content}`);
                    }
                } else if (listStack.length === 0 || listStack[listStack.length - 1].indent < indentLevel) {
                    // Starting a new (possibly nested) list
                    result.push(`<${listType}>`);
                    listStack.push({type: listType, indent: indentLevel});
                    result.push(`<li>${content}`);
                }
                continue;
            }

            // Empty line - close all lists
            if (line.trim() === '') {
                while (listStack.length > 0) {
                    const closed = listStack.pop();
                    result.push(`</li></${closed.type}>`);
                }
                result.push('');
                continue;
            }

            // Regular line - if in a list, append to current item; otherwise add as-is
            if (listStack.length > 0) {
                result.push('<br>' + line.trim());
            } else {
                result.push(line);
            }
        }

        // Close any remaining lists
        while (listStack.length > 0) {
            const closed = listStack.pop();
            result.push(`</li></${closed.type}>`);
        }

        formatted = result.join('\n');

        // Now handle paragraphs - wrap non-HTML content
        formatted = formatted
            .split('\n\n')
            .map(p => {
                p = p.trim();
                if (!p) return '';
                // Don't wrap if already has block-level HTML
                if (p.startsWith('<h') || p.startsWith('<pre') ||
                    p.startsWith('<ul') || p.startsWith('<ol') ||
                    p.startsWith('<hr') || p.startsWith('<li') ||
                    p.startsWith('</')) {
                    return p;
                }
                return `<p>${p.replace(/\n/g, '<br>')}</p>`;
            })
            .filter(p => p)
            .join('\n');

        // Clean up any stray newlines in the output
        formatted = formatted.replace(/\n+/g, '');

        return formatted;
    }

    addMessageToChat(sender, message) {
        const messagesDiv = document.querySelector('.ai-chat-messages');
        const messageElement = document.createElement('div');
        messageElement.className = `${sender}-message`;

        if (sender === 'ai') {
            messageElement.innerHTML = this.formatMessage(message);
        } else {
            messageElement.textContent = message;
        }

        messagesDiv.appendChild(messageElement);
    }
}

// Initialize the chat widget when the page loads
document.addEventListener('DOMContentLoaded', () => {
    new AIChatWidget();
});