from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    assets_root: str = r"C:\assets"
    db_url: str = "sqlite:///./app.db"

    admin_key: str = "change-me"

    opcua_endpoint: str = ""
    opcua_username: str = ""
    opcua_password: str = ""

    enable_opencv: bool = True


settings = Settings()