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
        document.getElementById('todoSummary').textContent = todo.summary;

        // Update priority tasks with new format
        const priorityTasksList = document.getElementById('priorityTasks');
        priorityTasksList.innerHTML = todo.priority_tasks
            .map(task => {
                const statusColor = {
                    'OVERDUE': 'text-rose-600',
                    'TODAY': 'text-emerald-600',
                    'UPCOMING': 'text-blue-600'
                }[task.status] || 'text-blue-600';

                const priorityColor = {
                    'HIGH': 'text-rose-600',
                    'MEDIUM': 'text-amber-600',
                    'LOW': 'text-blue-600'
                }[task.priority] || 'text-gray-600';

                return `
                    <li class="group flex items-start space-x-2 mb-4">
                        <div class="flex-grow">
                            <div class="flex items-center space-x-2">
                                <span class="${statusColor} font-medium">${task.status}</span>
                                <span class="text-gray-600">${task.date}</span>
                            </div>
                            <p class="mt-1 text-gray-900">${task.description}</p>
                        </div>
                        <span class="text-sm font-medium ${priorityColor} whitespace-nowrap">${task.priority}</span>
                    </li>`;
            })
            .join('');

        // Update follow-ups with formatting
        const followUpsList = document.getElementById('followUps');
        followUpsList.innerHTML = todo.follow_ups
            .map(followUp => {
                // Style email and phone numbers as links
                const styledFollowUp = followUp
                    .replace(/\(Email: (.*?)\)/, '<a href="mailto:$1" class="text-indigo-600 hover:text-indigo-800">$1</a>')
                    .replace(/\(Phone: (.*?)\)/, '<a href="tel:$1" class="text-indigo-600 hover:text-indigo-800">$1</a>')
                    .replace(/\(Added: (.*?)\)/, '<span class="text-gray-500">(Added: $1)</span>');
                return `<li class="mb-3">${styledFollowUp}</li>`;
            })
            .join('');

        // Update opportunities with formatting
        const opportunitiesList = document.getElementById('opportunities');
        opportunitiesList.innerHTML = todo.opportunities
            .map(opportunity => {
                // Style commission amounts - match any dollar amount pattern
                const styledOpportunity = opportunity.replace(
                    /\$(\d{1,3}(?:,\d{3})*)/g,
                    '<span class="text-emerald-600 font-medium">$$$1</span>'
                );
                return `<li class="mb-3">${styledOpportunity}</li>`;
            })
            .join('');

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