// Dashboard Todo List Management
document.addEventListener('DOMContentLoaded', function() {
    // Elements
    const newTodoInput = document.getElementById('dashboardNewTodoInput');
    const addTodoBtn = document.getElementById('dashboardAddTodoBtn');
    const activeTodoList = document.getElementById('dashboardActiveTodoList');
    let todos = [];
    let completedTodos = [];

    console.log('Dashboard todo initialization started');
    console.log('Found elements:', {
        newTodoInput: !!newTodoInput,
        addTodoBtn: !!addTodoBtn,
        activeTodoList: !!activeTodoList
    });

    // Load initial todos
    loadTodos();

    // Event Listeners
    if (addTodoBtn) {
        addTodoBtn.addEventListener('click', addTodo);
    }

    if (newTodoInput) {
        newTodoInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                addTodo();
            }
        });
    }

    // Functions
    function loadTodos() {
        console.log('Loading todos...');
        fetch('/api/user_todos/get')
            .then(response => response.json())
            .then(data => {
                console.log('Received todos:', data);
                todos = data.active || [];
                completedTodos = data.completed || [];
                renderTodos();
                updateEmptyState();
            })
            .catch(error => {
                console.error('Error loading todos:', error);
                updateEmptyState();
            });
    }

    function updateEmptyState() {
        if (!activeTodoList) {
            console.error('No active todo list element found');
            return;
        }

        // Remove existing empty state if it exists
        const existingEmptyState = activeTodoList.querySelector('.empty-state');
        if (existingEmptyState) {
            existingEmptyState.remove();
        }

        // Add empty state if no todos
        if (todos.length === 0) {
            console.log('No todos found, showing empty state');
            const emptyState = document.createElement('li');
            emptyState.className = 'empty-state text-center py-8 text-gray-500 italic bg-gray-50/50 rounded-lg';
            emptyState.textContent = 'No active tasks. Add one above!';
            activeTodoList.appendChild(emptyState);
        }
    }

    function renderTodos() {
        if (!activeTodoList) {
            console.error('No active todo list element found during render');
            return;
        }
        
        console.log('Rendering todos:', todos);
        activeTodoList.innerHTML = '';
        todos.forEach((todo, index) => {
            const li = createTodoElement(todo, index);
            activeTodoList.appendChild(li);
        });
    }

    function createTodoElement(todoText, index) {
        const template = document.getElementById('todoItemTemplate');
        if (!template) {
            console.error('Todo item template not found');
            return document.createElement('li');
        }

        const clone = template.content.cloneNode(true);
        const li = clone.querySelector('li');
        const todoTextElement = clone.querySelector('.todo-text');
        const editContainer = clone.querySelector('.todo-edit-container');
        const editInput = clone.querySelector('.todo-edit-input');
        const checkbox = clone.querySelector('.todo-checkbox');
        const deleteBtn = clone.querySelector('.delete-todo');

        // Set todo text
        todoTextElement.textContent = todoText;
        editInput.value = todoText;

        // Add transition styles
        li.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
        li.style.opacity = '0';
        li.style.transform = 'translateX(20px)';

        // Double click to edit
        todoTextElement.addEventListener('dblclick', function() {
            startEditing(li);
        });

        // Handle edit input
        editInput.addEventListener('blur', function() {
            finishEditing(li, index);
        });

        editInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                finishEditing(li, index);
            } else if (e.key === 'Escape') {
                cancelEditing(li);
            }
        });

        // Prevent click events from bubbling up when editing
        editContainer.addEventListener('click', function(e) {
            e.stopPropagation();
        });

        // Handle checkbox
        checkbox.addEventListener('change', function() {
            li.style.opacity = '0';
            li.style.transform = 'translateX(20px)';
            
            setTimeout(() => {
                completeTodo(index);
            }, 300);
        });

        // Handle delete
        deleteBtn.addEventListener('click', function() {
            li.style.opacity = '0';
            li.style.transform = 'translateX(20px)';
            
            setTimeout(() => {
                deleteTodo(index);
            }, 300);
        });

        // Trigger animation after creation
        setTimeout(() => {
            li.style.opacity = '1';
            li.style.transform = 'translateX(0)';
        }, 50);

        return li;
    }

    function startEditing(todoItem) {
        const textElement = todoItem.querySelector('.todo-text');
        const editContainer = todoItem.querySelector('.todo-edit-container');
        const editInput = todoItem.querySelector('.todo-edit-input');
        
        // Hide text, show input
        textElement.classList.add('hidden');
        editContainer.classList.remove('hidden');
        
        // Focus and select text
        editInput.focus();
        editInput.select();
    }

    function cancelEditing(todoItem) {
        const textElement = todoItem.querySelector('.todo-text');
        const editContainer = todoItem.querySelector('.todo-edit-container');
        
        // Hide input, show text
        textElement.classList.remove('hidden');
        editContainer.classList.add('hidden');
    }

    function finishEditing(li, index) {
        const textElement = li.querySelector('.todo-text');
        const editContainer = li.querySelector('.todo-edit-container');
        const editInput = li.querySelector('.todo-edit-input');
        const newText = editInput.value.trim();
        
        if (newText && newText !== todos[index]) {
            console.log('Updating todo text at index:', index);
            todos[index] = newText;
            textElement.textContent = newText;
            saveTodos();
        }
        
        // Hide input, show text
        textElement.classList.remove('hidden');
        editContainer.classList.add('hidden');
    }

    function addTodo() {
        const text = newTodoInput.value.trim();
        if (!text) return;

        console.log('Adding new todo:', text);
        todos.push(text);
        saveTodos();
        newTodoInput.value = '';
    }

    function completeTodo(index) {
        console.log('Completing todo at index:', index);
        const completedTodo = todos.splice(index, 1)[0];
        completedTodos.push(completedTodo); // Add to completed todos
        saveTodos();
    }

    function deleteTodo(index) {
        console.log('Deleting todo at index:', index);
        todos.splice(index, 1);
        saveTodos();
    }

    function saveTodos() {
        console.log('Saving todos:', { active: todos, completed: completedTodos });
        fetch('/api/user_todos/save', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                active: todos,
                completed: completedTodos // Now including completed todos in the save
            })
        })
        .then(response => response.json())
        .then(() => {
            loadTodos(); // Reload to ensure sync
        })
        .catch(error => console.error('Error saving todos:', error));
    }
}); 