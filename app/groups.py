"""Validation shared by the group editor and PostgreSQL configuration API."""
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rotation import UNIVERSE


class Group(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=60)
    tickers: list[str] = Field(default_factory=list, max_length=50)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError('Group names must contain visible text')
        return value

    @field_validator('tickers')
    @classmethod
    def clean_tickers(cls, values):
        values = [value.strip().upper() for value in values]
        if any(not re.fullmatch(r'[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?', value)
               for value in values):
            raise ValueError('Use US ticker symbols, e.g. MAGS, AIPO, DRAM or BRK-B (maximum 15 characters)')
        if len(values) != len(set(values)):
            raise ValueError('A ticker can appear only once in a group')
        return values


class GroupsConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    revision: int = Field(default=0, ge=0)
    active_id: str
    groups: list[Group] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def consistent_groups(self):
        ids = [group.id for group in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError('Group IDs must be unique')
        names = [group.name.casefold() for group in self.groups]
        if len(names) != len(set(names)):
            raise ValueError('Group names must be unique')
        if self.active_id not in ids:
            raise ValueError('The active group must exist')
        return self

    @property
    def active(self):
        return next(group for group in self.groups if group.id == self.active_id)

    @property
    def universe_key(self):
        return self.active.id, tuple(self.active.tickers)


def default_config():
    return GroupsConfig(active_id='ishare-sector', groups=[
        Group(id='ishare-sector', name='iShare Sector', tickers=UNIVERSE.copy()),
        Group(id='sector-etf', name='Sector ETF', tickers=[]),
    ])
