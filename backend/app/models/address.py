from pydantic import BaseModel, Field


class Address(BaseModel):
    address: str = Field(min_length=1)
