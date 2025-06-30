from pydantic_settings import BaseSettings

class HawksSettings(BaseSettings):
    secret_key: str
    admin_username: str
    admin_password: str
    chaos_api_key: str = ""
    database_url: str = "sqlite:///./hawks.db"
    
    class Config:
        env_file = ".env"

hawks_config = HawksSettings()
