import { Application } from "@hotwired/stimulus";
import DashboardPageController from "./controllers/dashboard_page_controller";
import ContactsPageController from "./controllers/contacts_page_controller";
import MarketInsightsController from "./controllers/market_insights_controller";
import GroupsPageController from "./controllers/groups_page_controller";
import DailyBriefingController from "./controllers/daily_briefing_controller";
import DailyBriefingBannerController from "./controllers/daily_briefing_banner_controller";
import BriefingChatController from "./controllers/briefing_chat_controller";
import TransactionLiveController from "./controllers/transaction_live_controller";
import DocumentReviewWorkspaceController from "./controllers/document_review_workspace_controller";
import AmendmentReviewController from "./controllers/amendment_review_controller";
import OfferCompareController from "./controllers/offer_compare_controller";
import DocumentUploadHubController from "./controllers/document_upload_hub_controller";
import OfferPackageReviewController from "./controllers/offer_package_review_controller";
import "./analytics";
import "./styles/app.css";

const application = Application.start();

application.register("dashboard-page", DashboardPageController);
application.register("contacts-page", ContactsPageController);
application.register("market-insights", MarketInsightsController);
application.register("groups-page", GroupsPageController);
application.register("daily-briefing", DailyBriefingController);
application.register("daily-briefing-banner", DailyBriefingBannerController);
application.register("briefing-chat", BriefingChatController);
application.register("transaction-live", TransactionLiveController);
application.register("document-review-workspace", DocumentReviewWorkspaceController);
application.register("amendment-review", AmendmentReviewController);
application.register("offer-compare", OfferCompareController);
application.register("document-upload-hub", DocumentUploadHubController);
application.register("offer-package-review", OfferPackageReviewController);

window.Stimulus = application;
