from pydantic import BaseModel
from typing import List, Optional

class BookNode(BaseModel):
    title: str
    content: str = ""
    level: int
    page: int = 1  # Новое поле: номер страницы
    children: List['BookNode'] = []

    class Config:
        arbitrary_types_allowed = True