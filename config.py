from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_name: str = "Ashmont Lead Qualification"
    frontend_origin: str = "http://localhost:3000"
    cors_origins: str = ""
    dev_auth_bypass: bool = False
    dev_mock_data: bool = False

    database_url: str = ""
    supabase_url: str = ""
    supabase_jwt_secret: str = ""
    intake_api_key: str = ""
    tool_api_key: str = ""

    retell_api_key: str = ""
    retell_agent_id: str = ""
    retell_from_number: str = ""
    retell_create_call_url: str = "https://api.retellai.com/v2/create-phone-call"
    retell_webhook_secret: str = ""

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    cal_api_key: str = ""
    cal_api_base_url: str = "https://api.cal.com/v2"
    cal_api_version: str = "2024-09-04"
    cal_webhook_secret: str = ""
    cal_event_type_id: str = ""
    cal_aditya_event_type_id: str = ""
    cal_archit_event_type_id: str = ""
    cal_default_timezone: str = "America/New_York"

    calendar_provider: str = "calcom"
    calendar_default_timezone: str = ""

    calendly_api_key: str = ""
    calendly_api_base_url: str = "https://api.calendly.com"
    calendly_event_type_uri: str = ""
    calendly_aditya_event_type_uri: str = ""
    calendly_archit_event_type_uri: str = ""
    calendly_location_kind: str = ""
    calendly_location_value: str = ""
    calendly_event_guests: str = ""

    gmail_smtp_host: str = "smtp.gmail.com"
    gmail_smtp_port: int = 587
    gmail_username: str = ""
    gmail_app_password: str = ""
    gmail_from_email: str = ""
    gmail_from_name: str = "Ashmont Insurance"
    aditya_notification_email: str = ""
    archit_notification_email: str = ""
    appointment_notification_cc: str = ""

    alert_email_to: str = ""
    alert_slack_webhook_url: str = ""
    momentum_api_key: str = ""
    momentum_api_base_url: str = "https://receiver.momentum.io"
    dashboard_users_json: str = ""
    dashboard_token_secret: str = ""
    dashboard_token_ttl_minutes: int = 1440

    @property
    def calendar_timezone(self) -> str:
        return self.calendar_default_timezone or self.cal_default_timezone or "America/New_York"

    @property
    def normalized_calendar_provider(self) -> str:
        provider = (self.calendar_provider or "calcom").strip().lower().replace(".", "")
        if provider in {"cal", "calcom"}:
            return "calcom"
        if provider == "calendly":
            return "calendly"
        return provider

    @property
    def allowed_origins(self) -> list[str]:
        configured = [
            origin.strip()
            for origin in self.cors_origins.replace(";", ",").split(",")
            if origin.strip()
        ]
        defaults = [
            self.frontend_origin,
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ]
        return list(dict.fromkeys(origin for origin in [*configured, *defaults] if origin))


@lru_cache
def get_settings() -> Settings:
    return Settings()
