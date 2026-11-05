import uvicorn

from .config import load_settings

settings = load_settings()
uvicorn.run("ai_pulse.app:app", host=settings.host, port=settings.port, workers=1)
