from pydantic import BaseModel


class SaveRequest(BaseModel):
    url: str
