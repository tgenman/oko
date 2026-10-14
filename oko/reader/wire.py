"""Wire model configuration, separate from domain validation and execution."""
from pydantic import BaseModel, ConfigDict


class WireModel(BaseModel):
    model_config = ConfigDict(strict=True, regex_engine='python-re')
