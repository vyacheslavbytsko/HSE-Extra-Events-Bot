import datetime

from sqlalchemy import BigInteger, ForeignKey, String, Integer, Boolean, DateTime
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase

from misc import DBJSON


class Base(AsyncAttrs, DeclarativeBase):
    pass

engine = create_async_engine("sqlite+aiosqlite:///db.db")
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

class DBUser(Base):
    __tablename__ = "Users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    points: Mapped[int] = mapped_column(Integer)

    event_games = relationship("DBEventGame", secondary="UserEventGames", back_populates="users")


class DBEventGame(Base):
    __tablename__ = "EventGames"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_title: Mapped[str] = mapped_column(String)
    stops: Mapped[DBJSON] = mapped_column(DBJSON)
    questions: Mapped[DBJSON] = mapped_column(DBJSON)
    start: Mapped[datetime.datetime] = mapped_column(DateTime)
    end: Mapped[datetime.datetime] = mapped_column(DateTime)

    users = relationship("DBUser", secondary="UserEventGames", back_populates="event_games")


class DBUserEventGame(Base):
    __tablename__ = "UserEventGames"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('Users.user_id'), primary_key=True)
    event_id: Mapped[str] = mapped_column(String, ForeignKey('EventGames.event_id'), primary_key=True)
    pre_start_notified: Mapped[bool] = mapped_column(Boolean)
    start_notified: Mapped[bool] = mapped_column(Boolean)
    end_notified: Mapped[bool] = mapped_column(Boolean)
    stops_done: Mapped[bool] = mapped_column(Boolean)
    questions_done: Mapped[bool] = mapped_column(Boolean)

async def db_add_user(session: AsyncSession, user_id: int, user_full_name: str, user_role: str):
    db_user = DBUser()
    db_user.user_id = user_id
    db_user.full_name = user_full_name
    db_user.role = user_role
    db_user.points = 0
    session.add(db_user)
    await session.commit()

async def db_add_event_game(session: AsyncSession, db_event_game: DBEventGame):
    session.add(db_event_game)
    await session.commit()


async def db_add_event_game_to_user(session: AsyncSession, user_id: int, event_id: str):
    db_user_event_game = DBUserEventGame()
    db_user_event_game.user_id = user_id
    db_user_event_game.event_id = event_id
    db_user_event_game.pre_start_notified = False
    db_user_event_game.start_notified = False
    db_user_event_game.end_notified = False
    db_user_event_game.stops_done = False
    db_user_event_game.questions_done = False
    session.add(db_user_event_game)
    await session.commit()


async def db_get_user(session: AsyncSession, user_id: int) -> DBUser | None:
    try:
        db_user = await session.get(DBUser, user_id)
        if not db_user:
            return None

        return await session.get(DBUser, user_id)
    except SQLAlchemyError:
        return None

async def db_get_event_game(session: AsyncSession, event_id: str) -> DBEventGame | None:
    try:
        db_event_game = await session.get(DBEventGame, event_id)
        if not db_event_game:
            return None

        return await session.get(DBEventGame, event_id)
    except SQLAlchemyError:
        return None