import { Application } from "@hotwired/stimulus";
import DashboardPageController from "./controllers/dashboard_page_controller";
import ContactsPageController from "./controllers/contacts_page_controller";
import "./styles/app.css";

const application = Application.start();

application.register("dashboard-page", DashboardPageController);
application.register("contacts-page", ContactsPageController);

window.Stimulus = application;
