from pydantic import BaseModel, Field, field_validator
from .roles import Role


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Invalid email")
        return value


class OperatorCreateRequest(LoginRequest):
    password: str = Field(min_length=12, max_length=1024)
    building_ids: list[str] = Field(default_factory=list, max_length=100)


class UserResponse(BaseModel):
    user_id: str
    email: str
    role: Role
    active: bool
    building_ids: list[str]
    organization_ids: list[str] = Field(default_factory=list)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class LogoutResponse(BaseModel):
    status: str = "client_token_disposal_required"
    message: str = "This access token is stateless; discard it in the client."
