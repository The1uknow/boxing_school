from __future__ import annotations
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, ForeignKey, Text, UniqueConstraint, Index, func
)
from sqlalchemy.orm import relationship, Mapped
from app.core.db import Base

# --------------------------- CRM ---------------------------

class Parent(Base):
    __tablename__ = "parents"

    id: Mapped[int] = Column(Integer, primary_key=True)
    tg_id: Mapped[str | None] = Column(String, unique=True, index=True)
    full_name: Mapped[str] = Column(String, default="")
    phone: Mapped[str] = Column(String, default="", index=True)
    city: Mapped[str] = Column(String, default="")
    language: Mapped[str] = Column(String, default="ru")
    ref_code: Mapped[str] = Column(String, default="")
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)

    children: Mapped[list["Child"]] = relationship(
        "Child", back_populates="parent", cascade="all, delete-orphan"
    )
    leads: Mapped[list["Lead"]] = relationship(
        "Lead", back_populates="parent", cascade="all, delete-orphan"
    )


class Child(Base):
    __tablename__ = "children"

    id: Mapped[int] = Column(Integer, primary_key=True)
    parent_id: Mapped[int] = Column(
        Integer, ForeignKey("parents.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = Column(String, nullable=False)
    age: Mapped[int] = Column(Integer, nullable=False)

    token: Mapped[str | None] = Column(String, unique=True, index=True)
    has_telegram: Mapped[bool] = Column(Boolean, default=True)

    # сделаем timezone-aware и на стороне БД
    created_at: Mapped[datetime] = Column(
        DateTime(timezone=True), server_default=func.now()
    )

    tg_id: Mapped[str | None] = Column(String, index=True, nullable=True)
    phone: Mapped[str | None] = Column(String, index=True, nullable=True)  # ⬅️ добавили
    schedule_text: Mapped[str | None] = Column(Text, default="", nullable=True)
    paid: Mapped[bool] = Column(Boolean, default=False, nullable=True)

    parent: Mapped["Parent"] = relationship("Parent", back_populates="children")
    appointments: Mapped[list["Appointment"]] = relationship(
        "Appointment", back_populates="child", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_children_parent_name", "parent_id", "name"),
    )


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = Column(Integer, primary_key=True, index=True)
    # форма сайта:
    name: Mapped[str] = Column(String(120), nullable=False)
    phone: Mapped[str] = Column(String(64), nullable=False, index=True)
    age: Mapped[str | None] = Column(String(16))
    comment: Mapped[str | None] = Column(String(600))
    # CRM:
    parent_id: Mapped[int | None] = Column(Integer, ForeignKey("parents.id", ondelete="CASCADE"), index=True, nullable=True)
    source: Mapped[str] = Column(String, default="site")
    ref_code: Mapped[str] = Column(String, default="")
    status: Mapped[str] = Column(String, default="new")   # new|in_work|won|lost
    processed: Mapped[bool] = Column(Boolean, default=False)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)

    parent: Mapped["Parent | None"] = relationship("Parent", back_populates="leads")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = Column(Integer, primary_key=True)
    child_id: Mapped[int] = Column(Integer, ForeignKey("children.id", ondelete="CASCADE"), index=True)
    datetime_str: Mapped[str] = Column(String, default="")
    location: Mapped[str] = Column(String, default="Главный зал")
    status: Mapped[str] = Column(String, default="new")
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)

    child: Mapped["Child"] = relationship("Child", back_populates="appointments")


class MessageTemplate(Base):
    __tablename__ = "message_templates"
    __table_args__ = (UniqueConstraint("key", "lang", name="uq_template_key_lang"),)

    id: Mapped[int] = Column(Integer, primary_key=True)
    key: Mapped[str] = Column(String, index=True)
    lang: Mapped[str] = Column(String, default="ru", index=True)
    text: Mapped[str] = Column(Text, default="")
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = Column(Integer, primary_key=True)
    login: Mapped[str] = Column(String, unique=True, index=True)
    password_hash: Mapped[str] = Column(String)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = Column(Boolean, default=True)