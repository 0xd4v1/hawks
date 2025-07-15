from pydantic_settings import BaseSettings

class HawksSettings(BaseSettings):
    secret_key: str
    admin_username: str
    admin_password: str
    database_url: str = "sqlite:///./hawks.db"
    max_concurrent_scans: int = 3
    scan_threads: int = 8
    
    class Config:
        env_file = ".env"
        extra = "ignore"

hawks_config = HawksSettings()
