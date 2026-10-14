The pinned generator propagates an anyOf property default to every constrained
RootModel branch (including a string branch when the default is an integer).
RootModel.jinja2 retains the upstream template with one change: a branch root is
required, so it does not inherit the parent union's default. The containing
wire field keeps its original default and validates it through the union.
This avoids invalid branch defaults without altering the OpenAPI document.

BaseModel.jinja2 places the discriminator on the actual union before adding
MISSING. Pydantic cannot treat its missing sentinel as a tagged union variant.
The branch default and optional discriminator cases are covered at the build
command boundary in test_api_generation.py.
