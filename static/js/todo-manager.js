/**
 * TodoManager - Shared todo list management class
 * Used by the dashboard Stimulus controller and user_todo.js to eliminate code duplication
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
        this.revision = null;
        this.identities = { active: [], completed: [] };
        this.saveQueue = Promise.resolve();
        this.subtodos = {};
        
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
            const response = await fetch('/api/user_todos/get?include_ids=1');
            if (!response.ok) throw new Error('Could not load todos');
            const data = await response.json();
            this.revision = response.headers.get('X-Todo-Revision');
            this.identities = data.identities || { active: [], completed: [] };
            this.subtodos = data.subtodos || {};

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
        const active = [...this.todos];
        const completed = [...this.completedTodos];
        const identities = { active: [...this.identities.active], completed: [...this.identities.completed] };
        const subtodos = Object.fromEntries([...identities.active, ...identities.completed].map(id =>
            [id, (this.subtodos[id] || []).map(child => ({ ...child }))]));
        const generation = this.loadGeneration || 0;
        this.saveQueue = this.saveQueue.then(async () => {
            if (generation !== (this.loadGeneration || 0)) return;
            try {
                if (!this.revision) throw new Error('Reload todos before saving.');
                const response = await fetch('/api/user_todos/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ active, completed, identities, subtodos, revision: this.revision })
                });
                if (!response.ok) throw new Error(response.status === 409
                    ? 'Your todos changed elsewhere. The latest list has been loaded. Please try your change again.'
                    : 'Your change could not be saved. Please try again.');
                this.revision = response.headers.get('X-Todo-Revision');
                if (this.config.onSave) this.config.onSave(active, completed);
            } catch (error) {
                this.revision = null;
                this.loadGeneration = (this.loadGeneration || 0) + 1;
                await this.loadTodos();
                window.alert(error.message);
            }
        });
        return this.saveQueue;
    }

    /**
     * Sync todos from DOM (useful after drag-drop reordering)
     */
    syncFromDOM() {
        if (this.activeList) { this.todos = []; this.identities.active = []; }
        if (this.completedList) { this.completedTodos = []; this.identities.completed = []; }

        if (this.activeList) {
            this.activeList.querySelectorAll('.todo-text').forEach(el => {
                if (!el.closest('.empty-state') && !el.closest('.text-center')) {
                    this.todos.push(el.textContent);
                    this.identities.active.push(el.closest('.todo-item').dataset.todoId);
                }
            });
        }

        if (this.completedList) {
            this.completedList.querySelectorAll('.todo-text').forEach(el => {
                if (!el.closest('.empty-state') && !el.closest('.text-center')) {
                    this.completedTodos.push(el.textContent);
                    this.identities.completed.push(el.closest('.todo-item').dataset.todoId);
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
        const identity = crypto.randomUUID();
        this.identities.active.push(identity);
        this.input.value = '';
        
        // Add to DOM
        if (this.activeList) {
            const element = this.createTodoElement(text, false, identity);
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
        this.identities.completed.push(this.identities.active.splice(index, 1)[0]);
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
        this.identities.active.push(this.identities.completed.splice(index, 1)[0]);
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
            this.identities.completed.splice(index, 1);
        } else {
            this.todos.splice(index, 1);
            this.identities.active.splice(index, 1);
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

    attachSubtodos(todoItem, identity) {
        const section = document.createElement('details');
        section.className = 'crm-subtodos';
        const summary = document.createElement('summary');
        const list = document.createElement('ul');
        list.className = 'crm-subtodos__list';
        list.setAttribute('aria-label', 'Sub-to-dos');
        const form = document.createElement('form');
        form.className = 'crm-subtodos__form';
        const input = document.createElement('textarea');
        input.className = 'crm-input';
        input.rows = 1;
        input.placeholder = 'Add sub-to-do';
        input.setAttribute('aria-label', 'New sub-to-do');
        const button = (text, label, action) => {
            const control = document.createElement('button');
            control.type = 'button';
            control.className = 'crm-btn crm-btn-ghost';
            control.textContent = text;
            control.setAttribute('aria-label', label);
            control.addEventListener('click', action);
            return control;
        };
        const validText = field => {
            const text = field.value.trim();
            field.setCustomValidity(!text ? 'Enter a sub-to-do.' :
                [...text].length > 500 ? 'Use 500 characters or fewer.' : '');
            return field.reportValidity() ? text : null;
        };
        input.addEventListener('input', () => input.setCustomValidity(''));
        const add = button('Add', 'Add sub-to-do', () => form.requestSubmit());
        form.append(input, add);
        section.append(summary, list, form);
        todoItem.append(section);
        const children = () => this.subtodos[identity] || [];
        const refreshSummary = () => {
            const items = children();
            summary.textContent = items.length
                ? `Sub-to-dos · ${items.filter(child => child.completed).length}/${items.length}`
                : 'Sub-to-dos';
            input.disabled = add.disabled = items.length >= 100;
        };
        const persist = () => {
            refreshSummary();
            this.saveTodos();
        };
        const render = (focusId, focusAction = 'edit') => {
            list.replaceChildren();
            children().forEach((child, index) => {
                const row = document.createElement('li');
                row.className = 'crm-subtodos__row';
                row.dataset.subtodoId = child.id;
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = child.completed;
                checkbox.setAttribute('aria-label', `Complete sub-to-do: ${child.text}`);
                checkbox.addEventListener('change', () => {
                    child.completed = checkbox.checked;
                    edit.classList.toggle('is-completed', child.completed);
                    persist();
                });
                const edit = button(child.text, `Edit sub-to-do: ${child.text}`, () => {
                    const editor = document.createElement('form');
                    editor.className = 'crm-subtodos__editor';
                    const field = document.createElement('textarea');
                    field.className = 'crm-input';
                    field.rows = 2;
                    field.value = child.text;
                    field.setAttribute('aria-label', 'Edit sub-to-do');
                    field.addEventListener('input', () => field.setCustomValidity(''));
                    const save = button('Save', 'Save sub-to-do', () => editor.requestSubmit());
                    const cancel = button('Cancel', 'Cancel edit', () => render(child.id));
                    editor.append(field, save, cancel);
                    editor.addEventListener('submit', event => {
                        event.preventDefault();
                        const text = validText(field);
                        if (!text) return;
                        child.text = text;
                        persist();
                        render(child.id);
                    });
                    field.addEventListener('keydown', event => {
                        if (event.key === 'Escape') { event.preventDefault(); render(child.id); }
                    });
                    row.replaceChildren(editor);
                    field.focus();
                    field.select();
                });
                edit.className = 'crm-subtodos__text';
                edit.classList.toggle('is-completed', child.completed);
                edit.dataset.action = 'edit';
                const actions = document.createElement('div');
                actions.className = 'crm-subtodos__actions';
                for (const [direction, offset, symbol] of [['up', -1, '↑'], ['down', 1, '↓']]) {
                    const move = button(symbol, `Move sub-to-do ${direction}: ${child.text}`, () => {
                        const items = children();
                        [items[index], items[index + offset]] = [items[index + offset], items[index]];
                        persist();
                        render(child.id, direction);
                    });
                    move.dataset.action = direction;
                    move.disabled = index + offset < 0 || index + offset >= children().length;
                    actions.append(move);
                }
                actions.append(button('Delete', `Delete sub-to-do: ${child.text}`, () => {
                    this.subtodos[identity] = children().filter(item => item.id !== child.id);
                    persist();
                    render();
                    const next = list.children[Math.min(index, list.children.length - 1)];
                    (next?.querySelector('[data-action="edit"]') || input).focus();
                }));
                row.append(checkbox, edit, actions);
                list.append(row);
                if (child.id === focusId) {
                    const target = row.querySelector(`[data-action="${focusAction}"]`);
                    (target && !target.disabled ? target : edit).focus();
                }
            });
            refreshSummary();
        };
        form.addEventListener('submit', event => {
            event.preventDefault();
            if (children().length >= 100) return;
            const text = validText(input);
            if (!text) return;
            this.subtodos[identity] = [...children(), { id: crypto.randomUUID(), text, completed: false }];
            input.value = '';
            persist();
            render();
            (input.disabled ? summary : input).focus();
        });
        input.addEventListener('keydown', event => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                form.requestSubmit();
            }
        });
        render();
    }

    // ==================== DOM Creation ====================

    /**
     * Create a todo element from the template
     * @param {string} text - Todo text
     * @param {boolean} [completed=false] - Whether this is a completed todo
     * @returns {HTMLElement} The created todo element
     */
    createTodoElement(text, completed = false, identity = crypto.randomUUID()) {
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

        todoItem.dataset.todoId = identity;

        // Set content
        todoText.textContent = text;
        editInput.value = text;
        if (checkbox) checkbox.checked = completed;
        const check = todoItem.querySelector('.t-check');
        if (check) {
            check.setAttribute('aria-checked', completed ? 'true' : 'false');
            if (window.TMotion) TMotion.initCheck(check);
        }

        // Setup transitions
        todoItem.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
        todoItem.style.opacity = '0';
        todoItem.style.transform = 'translateX(20px)';

        // Bind events
        this.bindTodoEvents(todoItem, todoText, editContainer, editInput, checkbox, deleteBtn);
        this.attachSubtodos(todoItem, identity);

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
        const check = todoItem.querySelector('.t-check') || todoItem.querySelector('.todo-checkbox');

        const slideOut = () => {
            todoItem.style.opacity = '0';
            todoItem.style.transform = 'translateX(20px)';
        };

        if (isChecked && !isInCompleted && window.TMotion && TMotion.burstConfetti) {
            TMotion.burstConfetti(check);
        } else {
            slideOut();
        }

        const startMove = () => {
            if (isChecked && !isInCompleted && todoItem.style.opacity !== '0') slideOut();
            setTimeout(() => {
                if (isChecked && !isInCompleted) {
                    if (this.completedList) {
                        todoItem.remove();
                        this.completedList.appendChild(todoItem);
                        todoItem.style.opacity = '1';
                        todoItem.style.transform = 'translateX(0)';
                        this.syncFromDOM();
                        this.saveTodos();
                    } else {
                        const todoText = todoItem.querySelector('.todo-text').textContent;
                        const activeIndex = this.identities.active.indexOf(todoItem.dataset.todoId);
                        if (activeIndex !== -1) {
                            this.todos.splice(activeIndex, 1);
                            this.completedTodos.push(todoText);
                            this.identities.completed.push(this.identities.active.splice(activeIndex, 1)[0]);
                        }
                        todoItem.remove();
                        this.saveTodos();
                    }
                } else if (!isChecked && isInCompleted) {
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
        };

        if (isChecked && !isInCompleted && window.TMotion && TMotion.whenConfettiSettled) {
            TMotion.whenConfettiSettled(startMove);
        } else {
            startMove();
        }
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
            this.todos.forEach((text, index) => {
                const element = this.createTodoElement(text, false, this.identities.active[index]);
                this.activeList.appendChild(element);
            });
        }

        if (this.completedList) {
            this.completedList.innerHTML = '';
            this.completedTodos.forEach((text, index) => {
                const element = this.createTodoElement(text, true, this.identities.completed[index]);
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
            emptyState.className = 'empty-state text-center py-8 text-sm italic rounded-md border border-dashed';
            emptyState.style.color = 'var(--ink-4)';
            emptyState.style.borderColor = 'var(--hairline)';
            emptyState.style.background = 'var(--paper-2)';
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

if (typeof window !== 'undefined') {
    window.TodoManager = TodoManager;
}

// Export for module usage (if needed in future)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = TodoManager;
}

