"""Wire model configuration, separate from domain validation and execution."""
from pydantic import BaseModel, ConfigDict


class WireModel(BaseModel):
    # Primitive fields are generated as Strict* types. Formats such as a JSON
    # date-time must still parse from strings before domain validation.
    model_config = ConfigDict(regex_engine='python-re')
