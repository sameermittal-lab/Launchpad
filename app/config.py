"""Global application configuration."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LAUNCHPAD_",
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 7070

    # Paths (relative to launchpad/ root)
    base_dir: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = Path("./users")
    db_path: Path = Path("./launchpad.db")
    logs_dir: Path = Path("./logs")

    # Logging
    log_level: str = "INFO"

    # Limits
    max_profiles: int = 3
    max_gmail_accounts_per_profile: int = 3

    # Security
    session_lifetime_days: int = 30

    @property
    def database_url(self) -> str:
        """SQLAlchemy database URL. Resolves relative paths against base_dir."""
        path = self.db_path
        if not path.is_absolute():
            path = (self.base_dir / path).resolve()
        return f"sqlite:///{path}"

    @property
    def frontend_dir(self) -> Path:
        return self.base_dir / "frontend"

    @property
    def templates_dir(self) -> Path:
        return self.base_dir / "templates"

    @property
    def prompts_dir(self) -> Path:
        return self.base_dir / "app" / "prompts"

    @property
    def resolved_data_dir(self) -> Path:
        """Absolute data_dir, resolved against base_dir if relative."""
        if self.data_dir.is_absolute():
            return self.data_dir
        return (self.base_dir / self.data_dir).resolve()

    @property
    def resolved_logs_dir(self) -> Path:
        if self.logs_dir.is_absolute():
            return self.logs_dir
        return (self.base_dir / self.logs_dir).resolve()

    def ensure_dirs(self) -> None:
        """Create required directories if they don't exist."""
        for p in (self.resolved_data_dir, self.resolved_logs_dir):
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
