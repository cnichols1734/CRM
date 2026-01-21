// Daily Todo List functionality
let todoModal = null;
let todoContent = null;
let todoLoading = null;
let todoError = null;
let todoSections = null;

document.addEventListener('DOMContentLoaded', () => {
    todoModal = document.getElementById('dailyTodoModal');
    todoContent = document.getElementById('todoContent');
    todoLoading = document.getElementById('todoLoading');
    todoError = document.getElementById('todoError');
    todoSections = document.getElementById('todoSections');

    // Check if we should show the todo list on page load
    checkAndShowTodoList();
});

// Function to show loading state
function showLoading() {
    if (todoLoading && todoSections && todoError) {
        todoLoading.classList.remove('hidden');
        todoSections.classList.add('hidden');
        todoError.classList.add('hidden');
    }
}

// Function to hide loading state
function hideLoading() {
    if (todoLoading) {
        todoLoading.classList.add('hidden');
    }
}

// Function to show error
function showError(message) {
    if (todoError) {
        todoError.textContent = message;
        todoError.classList.remove('hidden');
        hideLoading();
    }
}

// Function to display todo content
function displayTodoList(todoData) {
    try {
        // Parse the JSON content if it's a string
        const todo = typeof todoData === 'string' ? JSON.parse(todoData) : todoData;

        // Update summary
        const summaryEl = document.getElementById('todoSummary');
        const rawSummary = typeof todo.summary === 'string' ? todo.summary : '';
        const extractedFromSummary = [];
        const remainingSummary = rawSummary
            .split(/\r?\n/)
            .filter(line => {
                const m = line.match(/^\s*\[([^\]]+)\]\s*(.+)$/);
                if (m) {
                    extractedFromSummary.push(`[${m[1]}] ${m[2]}`);
                    return false;
                }
                return true;
            })
            .join(' ')
            .trim();
        summaryEl.textContent = remainingSummary || rawSummary;

        // Update marketing ideas
        const marketingIdeasList = document.getElementById('marketingIdeas');
        const marketingIdeasContainer = document.getElementById('marketingIdeasContainer');
        if (marketingIdeasList && marketingIdeasContainer) {
            const combinedIdeas = [
                ...(Array.isArray(todo.marketing_ideas) ? todo.marketing_ideas : []),
                ...extractedFromSummary
            ];
            if (combinedIdeas.length > 0) {
                marketingIdeasList.innerHTML = combinedIdeas
                    .map(rawIdea => {
                        const idea = String(rawIdea || '').replace(/^\s*[•\-\*]\s+/, '');
                        const bracketMatch = idea.match(/^\s*\[([^\]]+)\]\s*(.+)$/);
                        const colonMatch = idea.match(/^\s*([^:]+):\s*(.+)$/);
                        let channelLabel = null;
                        let body = idea;
                        if (bracketMatch) {
                            channelLabel = bracketMatch[1].trim();
                            body = bracketMatch[2].trim();
                        } else if (colonMatch) {
                            channelLabel = colonMatch[1].trim();
                            body = colonMatch[2].trim();
                        }
                        
                        // Create a text node for the body to safely escape it
                        const tempDiv = document.createElement('div');
                        tempDiv.textContent = body;
                        const safeBody = tempDiv.innerHTML;
                        
                        tempDiv.textContent = channelLabel || 'Tip';
                        const safeLabel = tempDiv.innerHTML;
                        
                        return `<div class="todo-marketing-item"><strong>${safeLabel}:</strong> ${safeBody}</div>`;
                    })
                    .join('');
                marketingIdeasContainer.classList.remove('hidden');
            } else {
                marketingIdeasContainer.classList.add('hidden');
            }
        }

        // Update priority tasks
        const priorityTasksList = document.getElementById('priorityTasks');
        if (todo.priority_tasks && todo.priority_tasks.length > 0) {
            priorityTasksList.innerHTML = todo.priority_tasks
                .map(task => {
                    const statusClass = {
                        'OVERDUE': 'todo-status-overdue',
                        'TODAY': 'todo-status-today',
                        'UPCOMING': 'todo-status-upcoming'
                    }[task.status] || 'todo-status-upcoming';

                    const priorityClass = {
                        'HIGH': 'todo-priority-high',
                        'MEDIUM': 'todo-priority-medium',
                        'LOW': 'todo-priority-low'
                    }[task.priority] || 'todo-priority-medium';
                    
                    // Clean the description
                    let cleanDescription = String(task.description || '');
                    cleanDescription = cleanDescription
                        .replace(/^\[OVERDUE SINCE [^\]]+\]\s*-?\s*/i, '')
                        .replace(/^\[DUE TODAY\]\s*-?\s*/i, '')
                        .replace(/^\[UPCOMING\]\s*-?\s*/i, '');
                    
                    // Safely escape
                    const tempDiv = document.createElement('div');
                    tempDiv.textContent = cleanDescription;
                    const safeDescription = tempDiv.innerHTML;
                    
                    tempDiv.textContent = task.date || '';
                    const safeDate = tempDiv.innerHTML;

                    return `
                        <div class="todo-task-item ${statusClass}">
                            <div class="todo-task-header">
                                <span class="todo-status-badge ${statusClass}">${task.status}</span>
                                <span class="todo-date">${safeDate}</span>
                                <span class="todo-priority ${priorityClass}">${task.priority}</span>
                            </div>
                            <p class="todo-task-description">${safeDescription}</p>
                        </div>`;
                })
                .join('');
        } else {
            priorityTasksList.innerHTML = '<p class="todo-empty">No priority tasks at this time.</p>';
        }

        // Update follow-ups
        const followUpsList = document.getElementById('followUps');
        const followUpsContainer = document.getElementById('followUpsContainer');
        if (todo.follow_ups && todo.follow_ups.length > 0) {
            followUpsList.innerHTML = todo.follow_ups
                .map(followUp => {
                    // Simple text processing - no complex escaping
                    let text = String(followUp || '');
                    
                    // Build HTML by processing text segments
                    // First, handle (Email: xxx) format - extract and replace with marker
                    const emailMatches = [];
                    const phoneMatches = [];
                    
                    // Extract (Email: xxx) patterns
                    text = text.replace(/\(Email:\s*([^)]+)\)/g, (match, email) => {
                        const idx = emailMatches.length;
                        emailMatches.push(email.trim());
                        return `%%EMAIL${idx}%%`;
                    });
                    
                    // Extract (Phone: xxx) patterns  
                    text = text.replace(/\(Phone:\s*([^)]+)\)/g, (match, phone) => {
                        const idx = phoneMatches.length;
                        phoneMatches.push(phone.trim());
                        return `%%PHONE${idx}%%`;
                    });
                    
                    // Extract standalone emails (not in markers)
                    const standaloneEmails = [];
                    text = text.replace(/([a-zA-Z0-9._+-]+@[a-zA-Z0-9._-]+\.[a-zA-Z0-9._-]+)/g, (match, email) => {
                        const idx = standaloneEmails.length;
                        standaloneEmails.push(email);
                        return `%%SEMAIL${idx}%%`;
                    });
                    
                    // Extract (Added: xxx) pattern
                    let addedDate = null;
                    text = text.replace(/\(Added:\s*([^)]+)\)/g, (match, date) => {
                        addedDate = date.trim();
                        return '';
                    });
                    
                    // Now escape the remaining text for safety
                    const tempDiv = document.createElement('div');
                    tempDiv.textContent = text.trim();
                    let html = tempDiv.innerHTML;
                    
                    // Replace markers with styled HTML
                    emailMatches.forEach((email, idx) => {
                        html = html.replace(`%%EMAIL${idx}%%`, `<a href="mailto:${email}" class="todo-link">${email}</a>`);
                    });
                    phoneMatches.forEach((phone, idx) => {
                        html = html.replace(`%%PHONE${idx}%%`, `<span class="todo-contact-info">${phone}</span>`);
                    });
                    standaloneEmails.forEach((email, idx) => {
                        html = html.replace(`%%SEMAIL${idx}%%`, `<a href="mailto:${email}" class="todo-link">${email}</a>`);
                    });
                    
                    // Add date at end if present
                    if (addedDate) {
                        html += ` <span class="todo-meta">(Added: ${addedDate})</span>`;
                    }

                    return `<div class="todo-followup-item">${html}</div>`;
                })
                .join('');
            followUpsContainer.classList.remove('hidden');
        } else {
            followUpsContainer.classList.add('hidden');
        }

        // Update opportunities
        const opportunitiesList = document.getElementById('opportunities');
        const opportunitiesContainer = document.getElementById('opportunitiesContainer');
        if (todo.opportunities && todo.opportunities.length > 0) {
            opportunitiesList.innerHTML = todo.opportunities
                .map(opportunity => {
                    let text = String(opportunity || '');
                    
                    // Extract commission amount before escaping
                    let commission = null;
                    text = text.replace(/\.?\s*Potential commission:\s*(\$[\d,]+)\.?/i, (match, amount) => {
                        commission = amount;
                        return '';
                    });
                    
                    // Escape the remaining text
                    const tempDiv = document.createElement('div');
                    tempDiv.textContent = text.trim();
                    const safeText = tempDiv.innerHTML;
                    
                    const commissionHtml = commission ? `<span class="todo-commission">${commission}</span>` : '';

                    return `<div class="todo-opportunity-item"><span class="todo-opportunity-text">${safeText}</span>${commissionHtml}</div>`;
                })
                .join('');
            opportunitiesContainer.classList.remove('hidden');
        } else {
            opportunitiesContainer.classList.add('hidden');
        }

        // Hide loading, show content
        hideLoading();
        if (todoError) todoError.classList.add('hidden');
        if (todoSections) todoSections.classList.remove('hidden');
    } catch (error) {
        console.error('Error displaying todo list:', error);
        showError('Failed to display your daily todo list. Please try again later.');
    }
}

// Function to fetch and display todo list
async function fetchTodoList(forceRefresh = false) {
    showLoading();
    try {
        const endpoint = forceRefresh ? '/api/daily-todo/generate' : '/api/daily-todo/latest';
        const response = await fetch(endpoint, {
            method: forceRefresh ? 'POST' : 'GET',
            headers: {
                'Content-Type': 'application/json',
            },
            ...(forceRefresh && {
                body: JSON.stringify({
                    force: true
                })
            })
        });
        
        if (!response.ok) {
            throw new Error('Failed to fetch todo list');
        }
        
        const data = await response.json();
        if (data.error) {
            showError(data.error);
            return;
        }
        
        displayTodoList(data.todo);
    } catch (error) {
        showError(error.message);
    }
}

async function checkAndShowTodoList() {
    try {
        const response = await fetch('/api/daily-todo/latest');
        if (response.ok) {
            const data = await response.json();
            const generatedAt = new Date(data.generated_at);
            const now = new Date();
            const hoursSinceGeneration = (now - generatedAt) / (1000 * 60 * 60);

            // If more than 16 hours have passed, generate a new list
            if (hoursSinceGeneration >= 16) {
                showDailyTodoModal();
                fetchTodoList(true); // Force a refresh
            }
        } else if (response.status === 404) {
            // If no todo list exists, generate a new one immediately
            fetchTodoList(true); // Force a refresh to generate new list
        } else {
            console.error('Error fetching todo list:', response.status);
        }
    } catch (error) {
        console.error('Error checking todo list:', error);
    }
}

// Function to show modal and load todo list
function showDailyTodoModal() {
    if (todoModal) {
        todoModal.classList.remove('hidden');
        fetchTodoList();
    }
}

// Function to close modal
function closeDailyTodoModal() {
    if (todoModal) {
        todoModal.classList.add('hidden');
    }
}

// Function to refresh todo list
function refreshDailyTodo() {
    fetchTodoList(true);
} 