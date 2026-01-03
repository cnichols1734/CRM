/**
 * Dashboard JavaScript
 * - Joke of the Day functionality
 * - Dashboard Todo List (uses shared TodoManager class)
 */

// ==================== Joke of the Day ====================

async function fetchDadJoke() {
    const response = await fetch('https://icanhazdadjoke.com/', {
        headers: { 'Accept': 'application/json' }
    });
    const data = await response.json();
    return data.joke;
}

async function fetchJokeAPI() {
    const response = await fetch('https://v2.jokeapi.dev/joke/Pun,Misc?type=single&blacklistFlags=nsfw,religious,political,racist,sexist,explicit');
    const data = await response.json();
    if (data.error) throw new Error('JokeAPI error');
    return data.joke;
}

async function displayRandomJoke() {
    const jokeText = document.getElementById('joke-text');
    if (!jokeText) return; // Not on dashboard page
    
    try {
        // Randomly pick which API to use
        const useIcanhazdadjoke = Math.random() < 0.5;
        const joke = useIcanhazdadjoke ? await fetchDadJoke() : await fetchJokeAPI();
        jokeText.textContent = joke;
    } catch (error) {
        // Fallback if APIs fail
        jokeText.textContent = "Why did the real estate agent bring a ladder? To reach new heights in sales!";
    }
}

// ==================== Dashboard Todo List ====================

document.addEventListener('DOMContentLoaded', function() {
    // Load joke of the day
    displayRandomJoke();
    
    // Check if we're on the dashboard with todo elements
    const activeList = document.getElementById('dashboardActiveTodoList');
    if (!activeList) return; // Not on dashboard or no todo section

    // Initialize TodoManager for dashboard
    // Dashboard uses a simpler config - no completed list, no counts
    const dashboardTodos = new TodoManager({
        templateId: 'todoItemTemplate',
        activeListId: 'dashboardActiveTodoList',
        inputId: 'dashboardNewTodoInput',
        addBtnId: 'dashboardAddTodoBtn'
    });

    // Make it accessible for debugging if needed
    window.dashboardTodos = dashboardTodos;
});
