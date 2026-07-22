import { Application } from "@hotwired/stimulus";
import DashboardPageController from "./controllers/dashboard_page_controller";
import ContactsPageController from "./controllers/contacts_page_controller";
import MarketInsightsController from "./controllers/market_insights_controller";
import GroupsPageController from "./controllers/groups_page_controller";
import DailyBriefingController from "./controllers/daily_briefing_controller";
import DailyBriefingBannerController from "./controllers/daily_briefing_banner_controller";
import BriefingChatController from "./controllers/briefing_chat_controller";
import "./styles/app.css";

const application = Application.start();

application.register("dashboard-page", DashboardPageController);
application.register("contacts-page", ContactsPageController);
application.register("market-insights", MarketInsightsController);
application.register("groups-page", GroupsPageController);
application.register("daily-briefing", DailyBriefingController);
application.register("daily-briefing-banner", DailyBriefingBannerController);
application.register("briefing-chat", BriefingChatController);

window.Stimulus = application;
