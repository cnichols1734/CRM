document.addEventListener('DOMContentLoaded', function() {
    // DOM Elements
    const addTodoBtn = document.getElementById('addTodoBtn');
    const newTodoInput = document.getElementById('newTodoInput');
    const activeTodoList = document.getElementById('activeTodoList');
    const completedTodoList = document.getElementById('completedTodoList');
    const todoItemTemplate = document.getElementById('todoItemTemplate');
    const activeTodoCount = document.getElementById('activeTodoCount');
    const completedTodoCount = document.getElementById('completedTodoCount');

    // Function to update task counts
    function updateTaskCounts() {
        const activeCount = activeTodoList.children.length;
        const completedCount = completedTodoList.children.length;
        
        activeTodoCount.textContent = `${activeCount} ${activeCount === 1 ? 'task' : 'tasks'}`;
        completedTodoCount.textContent = `${completedCount} ${completedCount === 1 ? 'completed' : 'completed'}`;
        
        // Add empty state message if no tasks
        if (activeCount === 0) {
            const emptyState = document.createElement('li');
            emptyState.className = 'text-center py-8 text-gray-500 italic bg-gray-50/50 rounded-lg';
            emptyState.textContent = 'No active tasks. Add one above!';
            activeTodoList.appendChild(emptyState);
        } else {
            const emptyState = activeTodoList.querySelector('.text-center.py-8');
            if (emptyState) {
                emptyState.remove();
            }
        }
        
        if (completedCount === 0) {
            const emptyState = document.createElement('li');
            emptyState.className = 'text-center py-8 text-gray-500 italic bg-gray-50/50 rounded-lg';
            emptyState.textContent = 'No completed tasks yet';
            completedTodoList.appendChild(emptyState);
        } else {
            const emptyState = completedTodoList.querySelector('.text-center.py-8');
            if (emptyState) {
                emptyState.remove();
            }
        }
    }

    // Initialize SortableJS for both lists
    const activeListSortable = new Sortable(activeTodoList, {
        animation: 150,
        ghostClass: 'bg-gray-100',
        chosenClass: 'opacity-70',
        dragClass: 'shadow-lg',
        group: 'todos',
        handle: '.drag-handle',
        onEnd: function(evt) {
            saveTodoOrder();
            updateTaskCounts();
        }
    });

    const completedListSortable = new Sortable(completedTodoList, {
        animation: 150,
        ghostClass: 'bg-gray-100',
        chosenClass: 'opacity-70',
        dragClass: 'shadow-lg',
        group: 'todos',
        handle: '.drag-handle',
        onEnd: function(evt) {
            saveTodoOrder();
            updateTaskCounts();
        }
    });

    // Todo Functions
    function createTodoElement(todoText, completed = false) {
        const clone = todoItemTemplate.content.cloneNode(true);
        const todoItem = clone.querySelector('.todo-item');
        const todoTextElement = clone.querySelector('.todo-text');
        const todoEditContainer = clone.querySelector('.todo-edit-container');
        const todoEditInput = clone.querySelector('.todo-edit-input');
        const checkbox = clone.querySelector('.todo-checkbox');
        const deleteBtn = clone.querySelector('.delete-todo');

        todoTextElement.textContent = todoText;
        todoEditInput.value = todoText;
        checkbox.checked = completed;

        // Add event listeners
        checkbox.addEventListener('change', function() {
            const targetList = checkbox.checked ? completedTodoList : activeTodoList;
            const sourceList = checkbox.checked ? activeTodoList : completedTodoList;
            
            todoItem.style.opacity = '0';
            todoItem.style.transform = 'translateX(20px)';
            
            setTimeout(() => {
                sourceList.removeChild(todoItem);
                targetList.appendChild(todoItem);
                todoItem.style.opacity = '1';
                todoItem.style.transform = 'translateX(0)';
                saveTodoOrder();
                updateTaskCounts();
            }, 300);
        });

        deleteBtn.addEventListener('click', function() {
            todoItem.style.opacity = '0';
            todoItem.style.transform = 'translateX(20px)';
            
            setTimeout(() => {
                todoItem.remove();
                saveTodoOrder();
                updateTaskCounts();
            }, 300);
        });

        // Edit mode event listeners
        todoTextElement.addEventListener('dblclick', function(e) {
            e.preventDefault();
            startEditing(todoItem);
        });

        // Prevent click events from bubbling up when editing
        todoEditContainer.addEventListener('click', function(e) {
            e.stopPropagation();
        });

        todoEditInput.addEventListener('click', function(e) {
            e.stopPropagation();
        });

        todoEditInput.addEventListener('keydown', function(e) {
            e.stopPropagation();
            if (e.key === 'Enter') {
                stopEditing(todoItem, true);
            } else if (e.key === 'Escape') {
                stopEditing(todoItem, false);
            }
        });

        todoEditInput.addEventListener('blur', function() {
            stopEditing(todoItem, true);
        });

        // Add transition styles
        todoItem.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
        todoItem.style.opacity = '0';
        todoItem.style.transform = 'translateX(20px)';

        // Trigger animation after append
        setTimeout(() => {
            todoItem.style.opacity = '1';
            todoItem.style.transform = 'translateX(0)';
        }, 50);

        return todoItem;
    }

    function startEditing(todoItem) {
        const textElement = todoItem.querySelector('.todo-text');
        const editContainer = todoItem.querySelector('.todo-edit-container');
        const editInput = todoItem.querySelector('.todo-edit-input');
        
        // Hide text, show input
        textElement.classList.add('hidden');
        editContainer.classList.remove('hidden');
        
        // Just focus the input without selecting text
        editInput.focus();
    }

    function stopEditing(todoItem, save) {
        const textElement = todoItem.querySelector('.todo-text');
        const editContainer = todoItem.querySelector('.todo-edit-container');
        const editInput = todoItem.querySelector('.todo-edit-input');
        
        if (save && editInput.value.trim() !== '') {
            textElement.textContent = editInput.value.trim();
            saveTodoOrder();
        }
        
        // Hide input, show text
        textElement.classList.remove('hidden');
        editContainer.classList.add('hidden');
    }

    function saveTodoOrder() {
        const todos = {
            active: [],
            completed: []
        };

        activeTodoList.querySelectorAll('.todo-text').forEach(todo => {
            if (!todo.closest('.text-center')) {  // Skip empty state message
                todos.active.push(todo.textContent);
            }
        });

        completedTodoList.querySelectorAll('.todo-text').forEach(todo => {
            if (!todo.closest('.text-center')) {  // Skip empty state message
                todos.completed.push(todo.textContent);
            }
        });

        fetch('/api/user_todos/save', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(todos)
        });
    }

    function loadTodos() {
        fetch('/api/user_todos/get')
            .then(response => response.json())
            .then(todos => {
                // Clear existing todos
                activeTodoList.innerHTML = '';
                completedTodoList.innerHTML = '';

                // Add active todos
                todos.active.forEach(todo => {
                    activeTodoList.appendChild(createTodoElement(todo, false));
                });

                // Add completed todos
                todos.completed.forEach(todo => {
                    completedTodoList.appendChild(createTodoElement(todo, true));
                });

                // Update counts after loading
                updateTaskCounts();
            });
    }

    // Event Listeners
    function addNewTodo() {
        const todoText = newTodoInput.value.trim();
        if (todoText) {
            const todoElement = createTodoElement(todoText);
            activeTodoList.appendChild(todoElement);
            newTodoInput.value = '';
            saveTodoOrder();
            updateTaskCounts();
        }
    }

    addTodoBtn.addEventListener('click', addNewTodo);
    
    newTodoInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            addNewTodo();
        }
    });

    // Load initial todos
    loadTodos();
}); 