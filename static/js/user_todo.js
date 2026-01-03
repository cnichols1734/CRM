/**
 * User Todo Page JavaScript
 * Full-featured todo list with active/completed sections and drag-drop reordering
 * Uses shared TodoManager class with SortableJS integration
 */

document.addEventListener('DOMContentLoaded', function() {
    // Check if we're on the todo page
    const activeList = document.getElementById('activeTodoList');
    const completedList = document.getElementById('completedTodoList');
    if (!activeList) return;

    // Initialize TodoManager with full configuration
    const todoManager = new TodoManager({
        templateId: 'todoItemTemplate',
        activeListId: 'activeTodoList',
        completedListId: 'completedTodoList',
        inputId: 'newTodoInput',
        addBtnId: 'addTodoBtn',
        activeCountId: 'activeTodoCount',
        completedCountId: 'completedTodoCount',
        onSave: () => {
            // Update counts after any save
            updateTaskCounts();
        },
        onLoad: () => {
            // Initialize sortable after todos are loaded
            initializeSortable();
            updateTaskCounts();
        }
    });

    // Make accessible for debugging
    window.todoManager = todoManager;

    /**
     * Initialize SortableJS for drag-drop reordering
     */
    function initializeSortable() {
        if (typeof Sortable === 'undefined') {
            console.warn('SortableJS not loaded');
            return;
        }

        // Active list sortable
        new Sortable(activeList, {
            animation: 150,
            ghostClass: 'bg-gray-100',
            chosenClass: 'opacity-70',
            dragClass: 'shadow-lg',
            group: 'todos',
            handle: '.drag-handle',
            onEnd: function() {
                todoManager.syncFromDOM();
                todoManager.saveTodos();
                updateTaskCounts();
            }
        });

        // Completed list sortable
        if (completedList) {
            new Sortable(completedList, {
                animation: 150,
                ghostClass: 'bg-gray-100',
                chosenClass: 'opacity-70',
                dragClass: 'shadow-lg',
                group: 'todos',
                handle: '.drag-handle',
                onEnd: function() {
                    todoManager.syncFromDOM();
                    todoManager.saveTodos();
                    updateTaskCounts();
                }
            });
        }
    }

    /**
     * Update task count displays
     * Overrides TodoManager's updateCounts for custom formatting
     */
    function updateTaskCounts() {
        const activeCount = activeList.querySelectorAll('.todo-item').length;
        const completedCount = completedList ? completedList.querySelectorAll('.todo-item').length : 0;

        const activeCountEl = document.getElementById('activeTodoCount');
        const completedCountEl = document.getElementById('completedTodoCount');

        if (activeCountEl) {
            activeCountEl.textContent = `${activeCount} ${activeCount === 1 ? 'task' : 'tasks'}`;
        }

        if (completedCountEl) {
            completedCountEl.textContent = `${completedCount} ${completedCount === 1 ? 'completed' : 'completed'}`;
        }
    }
});
