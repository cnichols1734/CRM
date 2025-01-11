// AI Chat Widget
class AIChatWidget {
    constructor() {
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

    toggleChat() {
        const chatBox = document.querySelector('.ai-chat-box');
        this.isOpen = !this.isOpen;
        chatBox.style.display = this.isOpen ? 'flex' : 'none';
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
                    currentUrl: window.location.href
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
        // Convert markdown-style formatting
        let formatted = text
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
            .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')

            // First, normalize all newlines and remove extra spaces
            .replace(/\r\n/g, '\n')
            .replace(/\n{3,}/g, '\n\n')
            .trim();

        // Handle special blocks (like ---)
        formatted = formatted.replace(/^---$/gm, '<hr class="message-divider">');

        // Process lists and headings
        let inList = false;
        let listStack = []; // Stack to track nested lists
        let currentIndentLevel = 0;

        formatted = formatted.split('\n').map(line => {
            // Check for headings with colons (like "Personalized Communication:")
            const headingMatch = line.match(/^([^:]+):$/);
            if (headingMatch) {
                if (inList) {
                    let closeTags = '';
                    while (listStack.length > 0) {
                        closeTags += `</li></${listStack.pop()}>`;
                    }
                    inList = false;
                    currentIndentLevel = 0;
                    return closeTags + `<h3 class="ai-chat-h3">${headingMatch[1]}</h3>`;
                }
                return `<h3 class="ai-chat-h3">${headingMatch[1]}</h3>`;
            }

            // Check for list items
            const listMatch = line.match(/^(\s*)(\d+\.|\-|\*)\s+(.+)$/);
            if (listMatch) {
                const [, indent, marker, content] = listMatch;
                const indentLevel = Math.floor(indent.length / 2);
                const isOrdered = /\d+\./.test(marker);
                const listType = isOrdered ? 'ol' : 'ul';

                if (!inList) {
                    // Start a new list
                    inList = true;
                    currentIndentLevel = indentLevel;
                    listStack.push(listType);
                    return `<${listType} class="list-level-${indentLevel}"><li>${content}`;
                } else {
                    if (indentLevel > currentIndentLevel) {
                        // Start a nested list
                        currentIndentLevel = indentLevel;
                        listStack.push(listType);
                        return `<${listType} class="list-level-${indentLevel}"><li>${content}`;
                    } else if (indentLevel < currentIndentLevel) {
                        // End nested lists and start new item at correct level
                        let closeTags = '';
                        while (currentIndentLevel > indentLevel) {
                            closeTags += `</li></${listStack.pop()}>`;
                            currentIndentLevel--;
                        }
                        // If the list type changes at the same level, close the old list and start a new one
                        if (listStack.length > 0 && listStack[listStack.length - 1] !== listType) {
                            closeTags += `</li></${listStack.pop()}>`;
                            listStack.push(listType);
                            return closeTags + `<${listType} class="list-level-${indentLevel}"><li>${content}`;
                        }
                        return closeTags + `</li><li>${content}`;
                    } else {
                        // Same level, check if list type changes
                        if (listStack.length > 0 && listStack[listStack.length - 1] !== listType) {
                            const closeTags = `</li></${listStack.pop()}>`;
                            listStack.push(listType);
                            return closeTags + `<${listType} class="list-level-${indentLevel}"><li>${content}`;
                        }
                        return `</li><li>${content}`;
                    }
                }
            } else if (inList && line.trim() === '') {
                // End all open lists
                let closeTags = '';
                while (listStack.length > 0) {
                    closeTags += `</li></${listStack.pop()}>`;
                }
                inList = false;
                currentIndentLevel = 0;
                return closeTags;
            } else if (inList) {
                // Continue list item content
                return ' ' + line.trim();
            }
            return line;
        }).join('\n');

        // Close any remaining lists
        if (inList) {
            let closeTags = '';
            while (listStack.length > 0) {
                closeTags += `</li></${listStack.pop()}>`;
            }
            formatted += closeTags;
        }

        // Now handle paragraphs
        formatted = formatted
            .split('\n\n')
            .map(p => {
                p = p.trim();
                if (!p) return '';
                if (p.startsWith('<h') || p.startsWith('<pre') ||
                    p.startsWith('<ul') || p.startsWith('<ol') ||
                    p.startsWith('<hr')) {
                    return p;
                }
                return `<p>${p.replace(/\n/g, '<br>')}</p>`;
            })
            .filter(p => p)
            .join('\n');

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