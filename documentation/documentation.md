# Real Estate CRM Documentation

## Project Overview

This project is a Customer Relationship Management (CRM) system tailored for the real estate industry. It's built using Flask (Python) for the backend, HTML templates, Tailwind CSS and JavaScript for the frontend, and SQLite for the database. The CRM is designed to be multi-user, with distinct roles for administrators and agents.

### Key Features

*   **Contact Management:** Agents can create, view, edit, and delete contacts. Admins have the ability to view all contacts across the system.
*   **Task Management:** Agents can create tasks associated with contacts, and admins can view all tasks.
*   **User Roles:** The system differentiates between 'admin' and 'agent' roles, with different levels of access and functionality.
*   **Contact Grouping:** Contacts can be categorized into groups (e.g., "Buyer," "Seller," "Network").
*   **Data Import/Export:** Contacts can be imported from and exported to CSV files.
*   **Dashboard:** Provides an overview of key metrics, such as total contacts, average commission, and top contacts.
*   **User Authentication:** Secure user registration, login, and password reset functionality.
*   **Responsive Design:** The UI is designed to be responsive and work well on different screen sizes.
*   **User Management:** Admins can manage user roles, edit user profiles, and delete users.

### Planned Features

*   **Marketing Section:** Email campaign functionality using a third-party provider (SendGrid, Mailchimp, etc.).

## Data Model

The database is structured using SQLAlchemy and includes the following models:

*   **User (models.py:17-46):**
    *   `id`: Integer, primary key.
    *   `username`: String, unique, required.
    *   `email`: String, unique, required.
    *   `password_hash`: String, stores hashed password.
    *   `first_name`: String, required.
    *   `last_name`: String, required.
    *   `role`: String, defaults to 'agent'.
    *   `created_at`: DateTime, defaults to current time.
    *   `last_login`: DateTime, tracks last login time.
    *   Methods for setting and checking passwords, and generating password reset tokens.
*   **ContactGroup (models.py:47-59):**
    *   `id`: Integer, primary key.
    *   `name`: String, unique, required.
    *   `category`: String, required.
    *   `sort_order`: Integer, required.
    *   `created_at`: DateTime, defaults to current time.
    *   `contacts`: Relationship to `Contact` model using association table `contact_groups`.
*   **Contact (models.py:60-86):**
    *   `id`: Integer, primary key.
    *   `user_id`: Integer, foreign key referencing `User.id`, required.
    *   `first_name`: String, required.
    *   `last_name`: String, required.
    *   `email`: String, optional.
    *   `phone`: String, optional.
    *   `street_address`: String, optional.
    *   `city`: String, optional.
    *   `state`: String, optional.
    *   `zip_code`: String, optional.
    *   `notes`: Text, optional.
    *   `created_at`: DateTime, defaults to current time.
    *   `updated_at`: DateTime, defaults to current time, updated on each update.
    *   `potential_commission`: Numeric, defaults to 5000.00.
    *   `owner`: Relationship to `User` model.
    *   `groups`: Relationship to `ContactGroup` model using association table `contact_groups`.
*   **Interaction (models.py:87-99):**
    *   `id`: Integer, primary key.
    *   `contact_id`: Integer, foreign key referencing `Contact.id`, required.
    *   `user_id`: Integer, foreign key referencing `User.id`, required.
    *   `type`: String, required.
    *   `notes`: Text, optional.
    *   `date`: DateTime, required.
    *   `follow_up_date`: DateTime, optional.
    *   `created_at`: DateTime, defaults to current time.
    *   `contact`: Relationship to `Contact` model.
    *   `user`: Relationship to `User` model.
*   **TaskType (models.py:100-107):**
    *   `id`: Integer, primary key.
    *   `name`: String, required (e.g., 'Call', 'Email', 'Meeting').
    *   `sort_order`: Integer, required.
    *   `subtypes`: Relationship to `TaskSubtype` model.
*   **TaskSubtype (models.py:108-113):**
    *   `id`: Integer, primary key.
    *   `task_type_id`: Integer, foreign key referencing `TaskType.id`, required.
    *   `name`: String, required (e.g., 'Check-in', 'Send Documents').
    *   `sort_order`: Integer, required.
*   **Task (models.py:114-143):**
    *   `id`: Integer, primary key.
    *   `contact_id`: Integer, foreign key referencing `Contact.id`, required.
    *   `assigned_to_id`: Integer, foreign key referencing `User.id`, required.
    *   `created_by_id`: Integer, foreign key referencing `User.id`, required.
    *   `type_id`: Integer, foreign key referencing `TaskType.id`, required.
    *   `subtype_id`: Integer, foreign key referencing `TaskSubtype.id`, required.
    *   `subject`: String, required.
    *   `description`: Text, optional.
    *   `priority`: String, defaults to 'medium' ('low', 'medium', 'high').
    *   `status`: String, defaults to 'pending' ('pending', 'completed', 'cancelled').
    *   `outcome`: Text, optional.
    *   `created_at`: DateTime, defaults to current time.
    *   `due_date`: DateTime, required.
    *   `completed_at`: DateTime, optional.
    *   `property_address`: String, optional.
    *   `scheduled_time`: DateTime, optional.
    *   `reminder_sent`: Boolean, defaults to False.
    *   `contact`: Relationship to `Contact` model.
    *   `assigned_to`: Relationship to `User` model.
    *   `created_by`: Relationship to `User` model.
    *   `task_type`: Relationship to `TaskType` model.
    *   `task_subtype`: Relationship to `TaskSubtype` model.

### Association Table

*   **contact\_groups (models.py:12-15):**
    *   `contact_id`: Integer, foreign key referencing `contact.id`, primary key.
    *   `group_id`: Integer, foreign key referencing `contact_group.id`, primary key.

## Routes and Functionality

The application is structured using Flask blueprints. Here's a breakdown of the routes and their functions:

### Main Blueprint (`routes/main.py`)

*   **`/` (index):**
    *   Displays a list of contacts.
    *   Supports filtering by 'all' (admin only) or 'my' contacts.
    *   Supports sorting by various fields (name, email, phone, address, notes, created\_at, owner, potential\_commission).
    *   Supports searching contacts by name, email, or phone.
    *   Renders `index.html` (templates/index.html).
*   **`/dashboard` (dashboard):**
    *   Displays a dashboard with key metrics.
    *   Shows total contacts, total commission, average commission, top contacts by commission, and group statistics.
    *   Displays upcoming tasks.
    *   Renders `dashboard.html` (templates/dashboard.html).

### Contacts Blueprint (`routes/contacts.py`)

*   **`/contact/<int:contact_id>` (view\_contact):**
    *   Displays details for a specific contact.
    *   Returns JSON data if it's an AJAX request.
    *   Renders `view_contact.html` (templates/view_contact.html).
*   **`/contacts/create` (create\_contact):**
    *   Handles the creation of new contacts.
    *   Renders `contact_form.html` (templates/contact_form.html).
*   **`/contacts/<int:contact_id>/edit` (edit\_contact):**
    *   Handles the editing of existing contacts via POST request.
    *   Returns JSON response indicating success or error.
*   **`/import-contacts` (import\_contacts):**
    *   Imports contacts from a CSV file.
*   **`/export-contacts` (export\_contacts):**
    *   Exports contacts to a CSV file.
*   **`/contacts/<int:contact_id>/delete` (delete\_contact):**
    *   Deletes a contact.

### Tasks Blueprint (`routes/tasks.py`)

*   **`/tasks` (tasks):**
    *   Displays a list of tasks.
    *   Supports filtering by 'all' (admin only) or 'my' tasks.
    *   Supports filtering by status (pending, completed, all).
    *   Renders `tasks.html` (templates/tasks.html).
*   **`/tasks/new` (create\_task):**
    *   Handles the creation of new tasks.
    *   Renders `create_task.html` (templates/create_task.html).
*   **`/tasks/<int:task_id>/edit` (edit\_task):**
    *   Handles the editing of existing tasks via POST request.
    *   Returns JSON response indicating success or error.
*   **`/tasks/<int:task_id>/delete` (delete\_task):**
    *   Deletes a task.
*   **`/tasks/types/<int:type_id>/subtypes` (get\_task\_subtypes):**
    *   Returns JSON data of subtypes for a given task type.
*   **`/tasks/<int:task_id>` (view\_task):**
    *   Displays details for a specific task.
    *   Returns JSON data if it's an AJAX request.
    *   Renders `view_task.html` (templates/view_task.html).
*   **`/tasks/<int:task_id>/quick-update` (quick\_update\_task):**
    *   Handles quick updates to task status and priority.

### Authentication Blueprint (`routes/auth.py`)

*   **`/register` (register):**
    *   Handles user registration.
    *   Renders `register.html` (templates/register.html).
*   **`/login` (login):**
    *   Handles user login.
    *   Renders `login.html` (templates/login.html).
*   **`/logout` (logout):**
    *   Handles user logout.
*   **`/profile` (view\_user\_profile):**
    *   Displays the current user's profile.
    *   Renders `user_profile.html` (templates/user_profile.html).
*   **`/profile/update` (update\_profile):**
    *   Handles updating the current user's profile information.
*   **`/reset_password` (reset\_request):**
    *   Handles password reset requests.
    *   Renders `reset_request.html` (templates/reset_request.html).
*   **`/reset_password/<token>` (reset\_password):**
    *   Handles password reset using a token.
    *   Renders `reset_password.html` (templates/reset_password.html).
*   **`/manage-users` (manage\_users):**
    *   Displays a list of all users (admin only).
    *   Renders `manage_users.html` (templates/manage_users.html).
*   **`/user/<int:user_id>/role` (update\_user\_role):**
    *   Handles updating a user's role (admin only).
*   **`/user/<int:user_id>/edit` (edit\_user):**
    *   Handles editing a user's profile (admin only).
*   **`/user/<int:user_id>/delete` (delete\_user):**
    *   Handles deleting a user (admin only).
*   **`/debug_users` (debug\_users):**
    *   Debug route to display all users.
*   **`/test_password/<username>/<password>` (test\_password):**
    *   Debug route to test password verification.

## Forms

The application uses Flask-WTF for form handling:

*   **RegistrationForm (forms.py:5-14):** For user registration.
*   **LoginForm (forms.py:15-23):** For user login.
*   **ContactForm (forms.py:24-36):** For creating and editing contacts.
*   **RequestResetForm (forms.py:38-41):** For requesting a password reset.
*   **ResetPasswordForm (forms.py:43-47):** For resetting a password.

## Migrations

The codebase includes several migration scripts:

*   **`migrate_add_tasks.py`:** Populates initial task types and subtypes.
*   **`update_contact_groups.py`:** Migrates contact group relationships to a new association table.
*   **`adress_migration.py`:**  Appears to be a partial migration script related to address fields.
*   **`migrate_add_commsion.py`:** Adds a `potential_commission` field to the `Contact` model.
*   **`migrate_autoincrement.py`:** Migrates tables to use AUTOINCREMENT for primary keys.

## Initial Data

The `init_db.py` script initializes the database with:

*   Initial contact groups (Buyer, Seller, Rating, Network, Professional).
*   Initial task types (Call, Meeting, Email, Document) and their subtypes.

## Configuration

The `config.py` file contains application settings:

*   `SECRET_KEY`: For session management and security.
*   `SQLALCHEMY_DATABASE_URI`: Database connection string.
*   `SQLALCHEMY_TRACK_MODIFICATIONS`: Disable modification tracking.
*   `PERMANENT_SESSION_LIFETIME`: Session timeout.
*   Mail settings for password reset emails.

## HTML Templates

The application uses Jinja2 templates for rendering HTML:

*   **`base.html` (templates/base.html):** Base template with common layout and styles.
*   **`index.html` (templates/index.html):** Displays the contact list.
*   **`dashboard.html` (templates/dashboard.html):** Displays the dashboard.
*   **`view_contact.html` (templates/view_contact.html):** Displays contact details.
*   **`contact_form.html` (templates/contact_form.html):** Form for creating contacts.
*   **`tasks.html` (templates/tasks.html):** Displays the task list.
*   **`view_task.html` (templates/view_task.html):** Displays task details.
*   **`create_task.html` (templates/create_task.html):** Form for creating tasks.
*   **`register.html` (templates/register.html):** Form for user registration.
*   **`login.html` (templates/login.html):** Form for user login.
*   **`user_profile.html` (templates/user_profile.html):** Displays user profile information.
*   **`reset_request.html` (templates/reset_request.html):** Form for requesting password reset.
*   **`reset_password.html` (templates/reset_password.html):** Form for resetting password.
*   **`manage_users.html` (templates/manage_users.html):** Displays a list of users for admin management.

## JavaScript

The templates include JavaScript for:

*   Handling mobile search functionality.
*   Opening and closing contact modals.
*   Fetching contact details via AJAX.
*   Submitting contact edits.
*   Deleting contacts.
*   Handling task modals.
*   Updating task status and priority.
*   Fetching task subtypes based on selected task type.

## Summary

This CRM is a well-structured application with a clear separation of concerns. It provides a solid foundation for managing real estate contacts and tasks. The use of Flask, SQLAlchemy, Tailwind CSS, and JavaScript makes it a modern and maintainable project. The planned marketing section will further enhance its capabilities.