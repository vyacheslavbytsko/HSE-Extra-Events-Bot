import datetime

from aiogram.filters.callback_data import CallbackData
from icalendar.cal import Calendar


class RoughEvent:
    id: str
    title: str
    date: datetime.datetime


class Event:
    id: str
    title: str
    rating: str
    ical: Calendar
    description: str
    address: str


class EventsMessageChangePageCallback(CallbackData, prefix="events_message_page"):
    page: int


class EventInfoCallback(CallbackData, prefix="event_info"):
    event_id: str
    from_page: int


class JoinEventGameCallback(CallbackData, prefix="join_event_game"):
    event_id: str


class CreateEventGameCallback(CallbackData, prefix="create_event_game"):
    event_id: str

class EventGameStopCallback(CallbackData, prefix="event_game_stop"):
    event_id: str
    stop_index: int
    points: int

class EventsGamesStopsChangePageCallback(CallbackData, prefix="events_games_stops_page"):
    page: int

class EventsGamesQuestionsChangePageCallback(CallbackData, prefix="events_games_qs_page"):
    page: int

class EventGameQuestionCallback(CallbackData, prefix="event_game_q"):
    event_id: str
    question_index: int
    last_answer_was_right: bool
    points: int