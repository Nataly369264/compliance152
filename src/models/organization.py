from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OrganizationData(BaseModel):
    id: str = ""
    legal_name: str
    short_name: str = ""
    inn: str = ""
    ogrn: str = ""
    legal_address: str = ""
    actual_address: str = ""
    ceo_name: str = ""
    ceo_position: str = "Генеральный директор"
    responsible_person: str = ""
    responsible_contact: str = ""
    website_url: str
    email: str = ""
    phone: str = ""

    data_categories: list[str] = Field(default_factory=list)
    processing_purposes: list[str] = Field(default_factory=list)
    data_subjects: list[str] = Field(default_factory=list)
    third_parties: list[str] = Field(default_factory=list)
    cross_border: bool = False
    cross_border_countries: list[str] = Field(default_factory=list)
    hosting_location: str = "Российская Федерация"
    info_systems: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
