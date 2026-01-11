/**
 * TodoManager - Shared todo list management class
 * Used by both dashboard.js and user_todo.js to eliminate code duplication
 */
class TodoManager {
    /**
     * @param {Object} config - Configuration options
     * @param {string} config.templateId - ID of the todo item template element
     * @param {string} config.activeListId - ID of the active todos list element
     * @param {string} [config.completedListId] - ID of the completed todos list (optional)
     * @param {string} [config.inputId] - ID of the new todo input element
     * @param {string} [config.addBtnId] - ID of the add todo button
     * @param {string} [config.activeCountId] - ID of active count display element
     * @param {string} [config.completedCountId] - ID of completed count display element
     * @param {Function} [config.onSave] - Callback after saving todos
     * @param {Function} [config.onLoad] - Callback after loading todos
     */
    constructor(config) {
        this.config = config;
        this.todos = [];
        this.completedTodos = [];
        
        // Cache DOM elements
        this.template = document.getElementById(config.templateId);
        this.activeList = document.getElementById(config.activeListId);
        this.completedList = config.completedListId ? document.getElementById(config.completedListId) : null;
        this.input = config.inputId ? document.getElementById(config.inputId) : null;
        this.addBtn = config.addBtnId ? document.getElementById(config.addBtnId) : null;
        this.activeCountEl = config.activeCountId ? document.getElementById(config.activeCountId) : null;
        this.completedCountEl = config.completedCountId ? document.getElementById(config.completedCountId) : null;
        
        this.init();
    }

    /**
     * Initialize the todo manager
     */
    init() {
        this.bindEvents();
        this.loadTodos();
    }

    /**
     * Bind event listeners for input and add button
     */
    bindEvents() {
        if (this.addBtn) {
            this.addBtn.addEventListener('click', () => this.addTodo());
        }
        
        if (this.input) {
            this.input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    this.addTodo();
                }
            });
        }
    }

    // ==================== API Methods ====================

    /**
     * Load todos from the server
     */
    async loadTodos() {
        try {
            const response = await fetch('/api/user_todos/get');
            const data = await response.json();
            
            this.todos = data.active || [];
            this.completedTodos = data.completed || [];
            
            this.render();
            this.updateCounts();
            this.updateEmptyStates();
            
            if (this.config.onLoad) {
                this.config.onLoad(this.todos, this.completedTodos);
            }
        } catch (error) {
            console.error('Error loading todos:', error);
            this.updateEmptyStates();
        }
    }

    /**
     * Save todos to the server
     */
    async saveTodos() {
        try {
            await fetch('/api/user_todos/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    active: this.todos,
                    completed: this.completedTodos
                })
            });
            
            if (this.config.onSave) {
                this.config.onSave(this.todos, this.completedTodos);
            }
        } catch (error) {
            console.error('Error saving todos:', error);
        }
    }

    /**
     * Sync todos from DOM (useful after drag-drop reordering)
     */
    syncFromDOM() {
        this.todos = [];
        this.completedTodos = [];

        if (this.activeList) {
            this.activeList.querySelectorAll('.todo-text').forEach(el => {
                if (!el.closest('.empty-state') && !el.closest('.text-center')) {
                    this.todos.push(el.textContent);
                }
            });
        }

        if (this.completedList) {
            this.completedList.querySelectorAll('.todo-text').forEach(el => {
                if (!el.closest('.empty-state') && !el.closest('.text-center')) {
                    this.completedTodos.push(el.textContent);
                }
            });
        }
    }

    // ==================== CRUD Operations ====================

    /**
     * Add a new todo from the input field
     */
    addTodo() {
        if (!this.input) return;
        
        const text = this.input.value.trim();
        if (!text) return;

        this.todos.push(text);
        this.input.value = '';
        
        // Add to DOM
        if (this.activeList) {
            const element = this.createTodoElement(text, false);
            this.activeList.appendChild(element);
        }
        
        this.saveTodos();
        this.updateCounts();
        this.updateEmptyStates();
    }

    /**
     * Complete a todo at the given index
     * @param {number} index - Index in the todos array
     */
    completeTodo(index) {
        const completedTodo = this.todos.splice(index, 1)[0];
        this.completedTodos.push(completedTodo);
        this.saveTodos();
        this.updateCounts();
        this.updateEmptyStates();
    }

    /**
     * Uncomplete a todo (move from completed to active)
     * @param {number} index - Index in the completedTodos array
     */
    uncompleteTodo(index) {
        const todo = this.completedTodos.splice(index, 1)[0];
        this.todos.push(todo);
        this.saveTodos();
        this.updateCounts();
        this.updateEmptyStates();
    }

    /**
     * Delete a todo
     * @param {number} index - Index in the array
     * @param {boolean} [isCompleted=false] - Whether it's from completed list
     */
    deleteTodo(index, isCompleted = false) {
        if (isCompleted) {
            this.completedTodos.splice(index, 1);
        } else {
            this.todos.splice(index, 1);
        }
        this.saveTodos();
        this.updateCounts();
        this.updateEmptyStates();
    }

    /**
     * Update a todo's text
     * @param {number} index - Index in the array
     * @param {string} newText - New text for the todo
     * @param {boolean} [isCompleted=false] - Whether it's from completed list
     */
    updateTodo(index, newText, isCompleted = false) {
        if (isCompleted) {
            this.completedTodos[index] = newText;
        } else {
            this.todos[index] = newText;
        }
        this.saveTodos();
    }

    // ==================== DOM Creation ====================

    /**
     * Create a todo element from the template
     * @param {string} text - Todo text
     * @param {boolean} [completed=false] - Whether this is a completed todo
     * @returns {HTMLElement} The created todo element
     */
    createTodoElement(text, completed = false) {
        if (!this.template) {
            console.error('Todo item template not found');
            return document.createElement('li');
        }

        const clone = this.template.content.cloneNode(true);
        const todoItem = clone.querySelector('li') || clone.querySelector('.todo-item');
        const todoText = clone.querySelector('.todo-text');
        const editContainer = clone.querySelector('.todo-edit-container');
        const editInput = clone.querySelector('.todo-edit-input');
        const checkbox = clone.querySelector('.todo-checkbox');
        const deleteBtn = clone.querySelector('.delete-todo');

        // Set content
        todoText.textContent = text;
        editInput.value = text;
        if (checkbox) checkbox.checked = completed;

        // Setup transitions
        todoItem.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
        todoItem.style.opacity = '0';
        todoItem.style.transform = 'translateX(20px)';

        // Bind events
        this.bindTodoEvents(todoItem, todoText, editContainer, editInput, checkbox, deleteBtn);

        // Animate in
        setTimeout(() => {
            todoItem.style.opacity = '1';
            todoItem.style.transform = 'translateX(0)';
        }, 50);

        return todoItem;
    }

    /**
     * Bind event listeners to a todo element
     */
    bindTodoEvents(todoItem, todoText, editContainer, editInput, checkbox, deleteBtn) {
        // Double-click to edit
        todoText.addEventListener('dblclick', () => {
            this.startEditing(todoItem);
        });

        // Edit input events
        if (editContainer) {
            editContainer.addEventListener('click', (e) => e.stopPropagation());
        }

        if (editInput) {
            editInput.addEventListener('blur', () => {
                this.stopEditing(todoItem, true);
            });

            editInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    this.stopEditing(todoItem, true);
                } else if (e.key === 'Escape') {
                    this.stopEditing(todoItem, false);
                }
            });
        }

        // Checkbox (complete/uncomplete)
        if (checkbox) {
            checkbox.addEventListener('change', () => {
                this.handleCheckboxChange(todoItem, checkbox.checked);
            });
        }

        // Delete button
        if (deleteBtn) {
            deleteBtn.addEventListener('click', () => {
                this.handleDelete(todoItem);
            });
        }
    }

    /**
     * Handle checkbox change (complete/uncomplete)
     */
    handleCheckboxChange(todoItem, isChecked) {
        const index = this.getItemIndex(todoItem);
        const isInCompleted = this.completedList && this.completedList.contains(todoItem);

        // Animate out
        todoItem.style.opacity = '0';
        todoItem.style.transform = 'translateX(20px)';

        setTimeout(() => {
            if (isChecked && !isInCompleted) {
                // Moving to completed
                if (this.completedList) {
                    // If we have a completed list, move the item there
                    todoItem.remove();
                    this.completedList.appendChild(todoItem);
                    todoItem.style.opacity = '1';
                    todoItem.style.transform = 'translateX(0)';
                    this.syncFromDOM();
                    this.saveTodos();
                } else {
                    // No completed list (dashboard) - move from active to completed array
                    const todoText = todoItem.querySelector('.todo-text').textContent;
                    const activeIndex = this.todos.indexOf(todoText);
                    if (activeIndex !== -1) {
                        this.todos.splice(activeIndex, 1);
                        this.completedTodos.push(todoText);
                    }
                    todoItem.remove();
                    this.saveTodos();
                }
            } else if (!isChecked && isInCompleted) {
                // Moving back to active
                todoItem.remove();
                this.activeList.appendChild(todoItem);
                todoItem.style.opacity = '1';
                todoItem.style.transform = 'translateX(0)';
                this.syncFromDOM();
                this.saveTodos();
            }
            this.updateCounts();
            this.updateEmptyStates();
        }, 300);
    }

    /**
     * Handle delete button click
     */
    handleDelete(todoItem) {
        todoItem.style.opacity = '0';
        todoItem.style.transform = 'translateX(20px)';

        setTimeout(() => {
            todoItem.remove();
            this.syncFromDOM();
            this.saveTodos();
            this.updateCounts();
            this.updateEmptyStates();
        }, 300);
    }

    /**
     * Get the index of a todo item in its list
     */
    getItemIndex(todoItem) {
        const parent = todoItem.parentElement;
        const items = Array.from(parent.querySelectorAll('.todo-item'));
        return items.indexOf(todoItem);
    }

    // ==================== Editing ====================

    /**
     * Start editing a todo item
     */
    startEditing(todoItem) {
        const textElement = todoItem.querySelector('.todo-text');
        const editContainer = todoItem.querySelector('.todo-edit-container');
        const editInput = todoItem.querySelector('.todo-edit-input');

        textElement.classList.add('hidden');
        editContainer.classList.remove('hidden');
        editInput.focus();
        editInput.select();
    }

    /**
     * Stop editing a todo item
     * @param {boolean} save - Whether to save the changes
     */
    stopEditing(todoItem, save) {
        const textElement = todoItem.querySelector('.todo-text');
        const editContainer = todoItem.querySelector('.todo-edit-container');
        const editInput = todoItem.querySelector('.todo-edit-input');

        if (save && editInput.value.trim() !== '') {
            const newText = editInput.value.trim();
            if (newText !== textElement.textContent) {
                textElement.textContent = newText;
                this.syncFromDOM();
                this.saveTodos();
            }
        }

        textElement.classList.remove('hidden');
        editContainer.classList.add('hidden');
    }

    // ==================== Rendering ====================

    /**
     * Render all todos to the DOM
     */
    render() {
        if (this.activeList) {
            this.activeList.innerHTML = '';
            this.todos.forEach((text) => {
                const element = this.createTodoElement(text, false);
                this.activeList.appendChild(element);
            });
        }

        if (this.completedList) {
            this.completedList.innerHTML = '';
            this.completedTodos.forEach((text) => {
                const element = this.createTodoElement(text, true);
                this.completedList.appendChild(element);
            });
        }
    }

    /**
     * Update task count displays
     */
    updateCounts() {
        if (this.activeCountEl) {
            const count = this.todos.length;
            this.activeCountEl.textContent = `${count} ${count === 1 ? 'task' : 'tasks'}`;
        }

        if (this.completedCountEl) {
            const count = this.completedTodos.length;
            this.completedCountEl.textContent = `${count} completed`;
        }
    }

    /**
     * Update empty state messages
     */
    updateEmptyStates() {
        this.updateListEmptyState(this.activeList, this.todos.length, 'No active tasks. Add one above!');
        
        if (this.completedList) {
            this.updateListEmptyState(this.completedList, this.completedTodos.length, 'No completed tasks yet');
        }
    }

    /**
     * Update empty state for a specific list
     */
    updateListEmptyState(list, itemCount, message) {
        if (!list) return;

        // Remove existing empty state
        const existing = list.querySelector('.empty-state');
        if (existing) existing.remove();

        // Add empty state if no items
        if (itemCount === 0) {
            const emptyState = document.createElement('li');
            emptyState.className = 'empty-state text-center py-8 text-gray-500 italic bg-gray-50/50 rounded-lg';
            emptyState.textContent = message;
            list.appendChild(emptyState);
        }
    }

    // ==================== Utilities ====================

    /**
     * Get current todos
     */
    getTodos() {
        return { active: this.todos, completed: this.completedTodos };
    }

    /**
     * Refresh from server
     */
    refresh() {
        this.loadTodos();
    }
}

// Export for module usage (if needed in future)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = TodoManager;
}

