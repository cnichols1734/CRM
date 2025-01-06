// Modal and action functions
window.showAddGroupModal = function() {
    document.getElementById('addGroupModal').classList.remove('hidden');
    document.getElementById('groupName').focus();
}

window.hideAddGroupModal = function() {
    document.getElementById('addGroupModal').classList.add('hidden');
    document.getElementById('addGroupForm').reset();
}

window.editGroup = function(id, name, category) {
    document.getElementById('editGroupId').value = id;
    document.getElementById('editGroupName').value = name;
    document.getElementById('editGroupCategory').value = category;
    document.getElementById('editGroupModal').classList.remove('hidden');
    document.getElementById('editGroupName').focus();
}

window.hideEditGroupModal = function() {
    document.getElementById('editGroupModal').classList.add('hidden');
    document.getElementById('editGroupForm').reset();
}

window.deleteGroup = function(id) {
    if (confirm('Are you sure you want to delete this group? This action cannot be undone.')) {
        fetch(`/admin/groups/${id}`, {
            method: 'DELETE',
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert('Error: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('An error occurred while deleting the group');
        });
    }
}

window.updateGroupOrder = function(category) {
    const container = document.getElementById(`category-${category.toLowerCase()}`);
    const items = container.getElementsByClassName('group-item');
    const orderData = Array.from(items).map((item, index) => ({
        id: parseInt(item.dataset.id),
        sort_order: index + 1
    }));
    
    fetch('/admin/groups/reorder', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
        },
        body: JSON.stringify(orderData)
    })
    .then(response => response.json())
    .then(data => {
        if (!data.success) {
            alert('Error updating order');
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('An error occurred while updating the order');
    });
}

// Initialize everything when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    // Add Group Form Submission
    document.getElementById('addGroupForm').addEventListener('submit', function(e) {
        e.preventDefault();
        const formData = new FormData(this);
        
        fetch('/admin/groups/add', {
            method: 'POST',
            body: formData,
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert('Error: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('An error occurred while adding the group');
        });
    });

    // Edit Group Form Submission
    document.getElementById('editGroupForm').addEventListener('submit', function(e) {
        e.preventDefault();
        const groupId = document.getElementById('editGroupId').value;
        const data = {
            name: document.getElementById('editGroupName').value,
            category: document.getElementById('editGroupCategory').value
        };
        
        fetch(`/admin/groups/${groupId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(data)
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                window.location.reload();
            } else {
                alert('Error: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('An error occurred while updating the group');
        });
    });
}); 