import asyncio
import datetime
import locale
import logging
import random
import sys
from typing import Callable, Dict, Any, Awaitable

import pytz
from aiogram import Bot, Dispatcher, html, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, BotCommand, KeyboardButton, ReplyKeyboardRemove, TelegramObject, \
    InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from ai import get_stops_from_gigachat, get_questions_from_gigachat
from browser import get_event_from_internet, get_rough_events_from_internet, driver
from classes import CreateEventGameCallback, JoinEventGameCallback, EventsMessageChangePageCallback, \
    EventInfoCallback, EventGameStopCallback, EventsGamesStopsChangePageCallback, \
    EventsGamesQuestionsChangePageCallback, EventGameQuestionCallback
from db import async_session, db_add_event_game_to_user, create_tables, db_get_user, db_add_user, DBUser, DBEventGame, \
    db_add_event_game, DBUserEventGame, db_get_event_game
from misc import declension
from tokens import tg_token

locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")
moscow_tz = pytz.timezone("Europe/Moscow")

bot = Bot(token=tg_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

dp = Dispatcher(storage=MemoryStorage())

csv_file_datetime = datetime.datetime.now()
csv_file_name = f"log-{str(csv_file_datetime)}.csv"


class EventGameCreationStates(StatesGroup):
    stops = State()
    questions = State()


class RegistrationStates(StatesGroup):
    full_name = State()
    role = State()


class RegistrationMiddleware(BaseMiddleware):
    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any]
    ) -> Any:
        user = data["event_from_user"]
        state = data["state"]

        with open(csv_file_name, "a") as csv_file:
            csv_file.write(str(event.update_id)+",")
            csv_file.write(str(data["event_from_user"].id)+",")
            csv_file.write("\""+str(datetime.datetime.now())+"\",")

            if event.message:
                csv_file.write("message,")
                csv_file.write(event.message.text+"\n")
            elif event.callback_query:
                csv_file.write("callback_query,")
                csv_file.write(event.callback_query.data+"\n")
            else:
                csv_file.write("unknown,None\n")

        async with async_session() as session:
            db_user = await db_get_user(session, user.id)

            if db_user is None:
                if event.message.text == "/register" or (
                        await state.get_state() in [RegistrationStates.full_name, RegistrationStates.role]):
                    result = await handler(event, data)
                    return result
                else:
                    await bot.send_message(data["event_context"].chat.id, "Вы не зарегистрированы в системе. /register")
            else:
                data["db_user"] = db_user
                result = await handler(event, data)
                return result



@dp.message(Command("register"))
async def command_register_handler(message: Message, state: FSMContext):
    async with async_session() as session:
        db_user = await session.get(DBUser, message.from_user.id)
        if db_user is not None:
            await message.answer("Вы уже зарегистрированы в системе. /start")
            return
    await message.answer(f"Здравствуйте! Давайте зарегистрируемся.\n\nНапишите своё имя.")
    await state.set_state(RegistrationStates.full_name)


button_user = "Обычный пользователь"
button_organisator = "Организатор"


@dp.message(RegistrationStates.full_name)
async def registration_full_name_handler(message: Message, state: FSMContext):
    await state.update_data({"full_name": message.text})

    reply_keyboard_builder = ReplyKeyboardBuilder()
    reply_keyboard_builder.row(KeyboardButton(text=button_user))
    reply_keyboard_builder.row(KeyboardButton(text=button_organisator))

    await message.answer(f"{html.bold(message.text + ", приятно познакомиться!")}\n\nВыберите свою роль.",
                         reply_markup=reply_keyboard_builder.as_markup())
    await state.set_state(RegistrationStates.role)


@dp.message(RegistrationStates.role)
async def registration_role_handler(message: Message, state: FSMContext):
    role = None
    if message.text == button_user:
        role = "user"
    elif message.text == button_organisator:
        role = "organisator"

    if role is None:
        await message.answer("Неправильная роль. Введите роль ещё раз.")
    else:
        async with async_session() as session:
            await db_add_user(session=session,
                              user_id=message.from_user.id,
                              user_full_name=(await state.get_data())["full_name"],
                              user_role=role)
            await state.clear()
            await message.answer("Спасибо за регистрацию! Введите /start.", reply_markup=ReplyKeyboardRemove())


@dp.message(Command("me"))
async def command_me_handler(message: Message, db_user: DBUser):
    await message.answer(
        f"Здравствуйте, {html.bold(db_user.full_name)}! Вот ваш профиль:\n\n"
        f"{html.bold("Роль:")} {button_user if db_user.role == "user" else button_organisator}\n"
        f"{f"{html.bold("Баллов:")} {db_user.points}" if db_user.role == "user" else ""}\n"
        f"{f"{html.bold("Мероприятий:")} {len(await db_user.awaitable_attrs.event_games)}" if db_user.role == "user" else ""}"
    )


@dp.message(CommandStart())
async def command_start_handler(message: Message, db_user: DBUser) -> None:
    sent_message = await message.answer(
        f"Здравствуйте, {html.bold(db_user.full_name)}! Сейчас мы получаем список мероприятий EXTRA.HSE, пожалуйста, подождите...")
    await edit_events_message(sent_message=sent_message, db_user=db_user, page_index=0)


async def edit_events_message(sent_message: Message, db_user: DBUser,
                              page_index: int):
    rough_events = await get_rough_events_from_internet(with_games=(db_user.role == "user"))
    if db_user.role == "user":
        db_user_event_games = await db_user.awaitable_attrs.event_games
        db_user_event_games_ids = [
            db_user_event_game.event_id for db_user_event_game in db_user_event_games]
        rough_events = list(filter(lambda rough_event: rough_event.id not in db_user_event_games_ids, rough_events))

    if len(rough_events) == 0:
        await sent_message.edit_text(
            f"Здравствуйте, {html.bold(db_user.full_name)}! Пока что мероприятий, {"на которые Вы можете записаться" if db_user.role == "user" else "для которых можно составить игровой маршрут"}, нет."
        )
        return

    rough_events_limited = rough_events[page_index * 5:page_index * 5 + 5]

    max_pages = ((len(rough_events) - 1) // 5 + 1) - 1

    inline_keyboard_builder = InlineKeyboardBuilder()
    for i in range(len(rough_events_limited)):
        inline_keyboard_builder.button(text=f"{(page_index * 5) + i + 1}",
                                       callback_data=EventInfoCallback(
                                           event_id=rough_events_limited[i].id,
                                           from_page=page_index))
    if page_index != 0:
        inline_keyboard_builder.button(text="◀️",
                                       callback_data=EventsMessageChangePageCallback(
                                           page=max(page_index - 1, 0)))
    if page_index != max_pages:
        inline_keyboard_builder.button(text="▶️️",
                                       callback_data=EventsMessageChangePageCallback(
                                           page=min(page_index + 1, max_pages)))

    inline_keyboard_builder.adjust(len(rough_events_limited), int(page_index != 0) + int(page_index != max_pages))

    text_meropriyatiye = declension(len(rough_events), "мероприятиях", "мероприятии", "мероприятиях")
    text_na_kotoriye = declension(len(rough_events), "которые", "которое", "которые")
    text_dlya_kototrykh = declension(len(rough_events), "которых", "которого", "которых")

    await sent_message.edit_text(
        f"Здравствуйте, {html.bold(db_user.full_name)}! "
        f"Прямо сейчас мы знаем о {len(rough_events)} {text_meropriyatiye}, {f"на {text_na_kotoriye} Вы можете записаться" if db_user.role == "user" else f"для {text_dlya_kototrykh} можно составить игровой маршрут"}:\n\n"
        f"{"\n\n".join([f"{rough_events.index(rough_event) + 1}) " +
                        rough_event.date.strftime("%d %B %Y") + ". " +
                        html.bold(rough_event.title)
                        for rough_event in rough_events_limited])}\n\n"
        f"Нажмите на любую из кнопок ниже, чтобы " +
        (f"записаться на мероприятие, "
         f"выполнять задания и получать очки:"
         if db_user.role == "user" else
         f"создать список игровых точек или вопросов:"),
        reply_markup=inline_keyboard_builder.as_markup())


@dp.callback_query(EventsMessageChangePageCallback.filter())
async def events_message_change_page_callback(query: CallbackQuery, callback_data: EventsMessageChangePageCallback,
                                              db_user: DBUser):
    await edit_events_message(
        sent_message=query.message,
        db_user=db_user,
        page_index=callback_data.page)
    await query.answer()


@dp.callback_query(EventInfoCallback.filter())
async def events_message_event_info_callback(query: CallbackQuery, callback_data: EventInfoCallback, db_user: DBUser):
    event = get_event_from_internet(callback_data.event_id)

    inline_keyboard_builder = InlineKeyboardBuilder()
    if db_user.role == "organisator":
        inline_keyboard_builder.button(text="Создать игровой маршрут",
                                       callback_data=CreateEventGameCallback(
                                           event_id=event.id))
    else:
        inline_keyboard_builder.button(text="Я хочу участвовать",
                                       callback_data=JoinEventGameCallback(
                                           event_id=event.id))
    inline_keyboard_builder.button(text="↩️",
                                   callback_data=EventsMessageChangePageCallback(
                                       page=callback_data.from_page))
    inline_keyboard_builder.adjust(1, 1)

    await query.message.edit_text(
        f"🔸 {html.bold(event.title)}. {event.rating}\n\n"
        f"{event.description}\n\n"
        f"{html.bold("Где:")} {event.address}\n"
        f"{html.bold("Начало:")} {event.ical.get("DTSTART").dt.astimezone(moscow_tz).strftime("%d %B %Y %H:%M")}\n"
        f"{html.bold("Конец:")} {event.ical.get("DTEND").dt.astimezone(moscow_tz).strftime("%d %B %Y %H:%M")}\n"
        f"{html.bold(html.link("Ссылка", f"https://extra.hse.ru/announcements/{event.id}.html"))}",
        reply_markup=inline_keyboard_builder.as_markup()
    )
    await query.answer()


@dp.callback_query(CreateEventGameCallback.filter())
async def create_event_game_callback(query: CallbackQuery, callback_data: CreateEventGameCallback, state: FSMContext):
    event = get_event_from_internet(callback_data.event_id)

    reply_keyboard_builder = ReplyKeyboardBuilder()
    reply_keyboard_builder.row(KeyboardButton(text="✨ Генерация контрольных точек"))

    await bot.send_message(query.message.chat.id,
                           f"Мероприятие: {html.bold(event.title)}\n\nВведите, какие контрольные точки нужно пройти участнику. За каждую пройденную контрольную точку пользователь получит +1 балл.\n\nФормат ввода контрольных точек: через перенос строки.\n\nДля отмены введите /cancel.",
                           reply_markup=reply_keyboard_builder.as_markup())
    await state.set_state(EventGameCreationStates.stops)
    await state.update_data({"event": event})
    await query.answer()
    await query.message.delete()


@dp.message(Command("cancel"), EventGameCreationStates.stops)
async def create_event_game_cancel_stops(message: Message, state: FSMContext):
    await message.answer("Отменили запись контрольных точек и создание маршрута мероприятия. /start",
                         reply_markup=ReplyKeyboardRemove())
    await state.clear()


@dp.message(F.text, EventGameCreationStates.stops)
async def create_event_game_stops_handler(message: Message, state: FSMContext):
    event = (await state.get_data())["event"]

    # for further bot messages
    reply_keyboard_builder = ReplyKeyboardBuilder()
    reply_keyboard_builder.row(KeyboardButton(text="✨ Генерация контрольных вопросов"))

    if message.text == "✨ Генерация контрольных точек":
        await message.answer("Подождите, пожалуйста...")
        gigachat_answer = ""
        i = 0
        while len(gigachat_answer.split("\n")) not in [6, 7] and i < 3:
            gigachat_answer = get_stops_from_gigachat(event.title, event.description)
            i += 1

        if i >= 3:
            await message.answer(f"GigaChat не смог сформировать контрольные точки. Повторите попытку.")
        else:
            await message.answer(
                f"GigaChat сформировал такие контрольные точки:\n\n{html.code(gigachat_answer.replace("Контрольные точки:", "").strip())}\n\nВы можете взять этот ответ за основу. Вы также можете запросить у GigaChat сформировать контрольные точки ещё раз - нажмите на кнопку ниже. Как только Вы придумаете контрольные точки, отправьте их нам в ответном сообщении.")
    else:
        await state.update_data({"stops": message.text.split("\n")})
        await message.answer("Хорошо, вписали.", reply_markup=ReplyKeyboardRemove())
        await message.answer(
            f"Мероприятие: {html.bold(event.title)}\n\nВведите контрольные вопросы. Каждый вопрос должен иметь три ответа, первый обязательно должен быть правильным. За каждый правильно отвеченный вопрос пользователь получит +1 балл.\n\nФормат написания контрольных вопросов:\n{html.italic("Контрольный вопрос №1\nОтвет №1 - правильный\nОтвет №2\nОтвет №3\n\nКонтрольный вопрос №2\nОтвет №1 - правильный\nОтвет №2\nОтвет №3\n\n...")}\n\nУчтите, что между контрольными вопросами есть пустая строка. \n\nДля отмены введите /cancel.",
            reply_markup=reply_keyboard_builder.as_markup())
        await state.set_state(EventGameCreationStates.questions)


@dp.message(Command("cancel"), EventGameCreationStates.questions)
async def create_event_game_cancel_questions(message: Message, state: FSMContext):
    await message.answer("Отменили запись контрольных вопросов и создание игрового маршрута мероприятия. /start",
                         reply_markup=ReplyKeyboardRemove())
    await state.clear()


@dp.message(F.text, EventGameCreationStates.questions)
async def create_event_game_questions_handler(message: Message, state: FSMContext):
    event = (await state.get_data())["event"]

    if message.text == "✨ Генерация контрольных вопросов":
        await message.answer("Подождите, пожалуйста...")
        gigachat_answer = ""
        i = 0
        while len(gigachat_answer.split("\n")) != 24 and i < 3:
            gigachat_answer = get_questions_from_gigachat(event.title, event.description)
            i += 1

        if i >= 3:
            await message.answer(f"GigaChat не смог сформировать контрольные вопросы. Повторите попытку.")
        else:
            await message.answer(
                f"GigaChat сформировал такие контрольные вопросы:\n\n{html.code(gigachat_answer)}\n\nВы можете взять этот ответ за основу. Вы также можете запросить у GigaChat сформировать контрольные вопросы ещё раз - нажмите на кнопку ниже. Как только Вы придумаете контрольные вопросы, отправьте их нам в ответном сообщении.")
    else:
        # first of all, check
        questions_and_answers = message.text.split("\n\n")
        if not all(len(question_and_answers.split("\n")) == 4 for question_and_answers in questions_and_answers):
            await message.answer("Проверьте правильность введённых данных. У каждого вопроса по три ответа.")
            return

        questions = [question_and_answers.split("\n") for question_and_answers in questions_and_answers]
        for i in range(len(questions)):
            if questions[i][0].startswith("Контрольный вопрос"): questions[i][0] = ":".join(
                questions[i][0].split(":")[1:]).strip()
            for j in range(1, 3 + 1):
                if questions[i][j].startswith("Ответ"): questions[i][j] = ":".join(
                    questions[i][j].split(":")[1:]).strip()

        await state.update_data({"questions": questions})
        await message.answer("Хорошо, вписали.", reply_markup=ReplyKeyboardRemove())
        async with async_session() as session:
            db_event_game = DBEventGame()
            db_event_game.event_id = event.id
            db_event_game.event_title = event.title
            db_event_game.stops = (await state.get_data())["stops"]
            db_event_game.questions = (await state.get_data())["questions"]
            db_event_game.start = event.ical.get("DTSTART").dt.astimezone(moscow_tz)
            db_event_game.end = event.ical.get("DTEND").dt.astimezone(moscow_tz)
            await db_add_event_game(session, db_event_game)

            await message.answer(
                "Игровой маршрут создан! Теперь пользователи данного бота могут принять участие в Вашем мероприятии.",
                reply_markup=ReplyKeyboardRemove())
            await state.clear()


@dp.callback_query(JoinEventGameCallback.filter())
async def join_event_game_callback(query: CallbackQuery, callback_data: JoinEventGameCallback, db_user: DBUser):
    async with async_session() as session:
        await db_add_event_game_to_user(
            session=session,
            user_id=db_user.user_id,
            event_id=callback_data.event_id)
        await query.message.edit_text(f"{html.bold("Вы успешно записались на данное мероприятие!")} "
                                      "За час до и в начале мероприятия мы уведомим Вас о нём. "
                                      "Во время мероприятия будет доступен список контрольных "
                                      "точек - пройдите каждую! После мероприятия мы зададим Вам "
                                      "пару вопросов о мероприятии - за каждый правильный ответ Вы получите "
                                      "балл.")
        await query.answer()


async def send_notifications():
    async with async_session() as session:
        should_commit = False
        db_event_games = await session.execute(select(DBEventGame))
        for db_event_game in db_event_games.scalars():
            notify_about_pre_start = datetime.datetime.now(tz=moscow_tz) >= moscow_tz.localize(db_event_game.start) - datetime.timedelta(hours=1)
            notify_about_start = datetime.datetime.now(tz=moscow_tz) >= moscow_tz.localize(db_event_game.start)
            notify_about_end = datetime.datetime.now(tz=moscow_tz) >= moscow_tz.localize(db_event_game.end)

            db_users: list[DBUser] = await db_event_game.awaitable_attrs.users
            for db_user in db_users:
                db_user_event_game = await session.get(DBUserEventGame, (db_user.user_id, db_event_game.event_id))
                if notify_about_pre_start and not db_user_event_game.pre_start_notified:
                    await bot.send_message(
                        chat_id=db_user.user_id,
                        text=f"{html.bold(db_user.full_name)}, через час состоится мероприятие {html.bold(db_event_game.event_title)}. Не опаздывайте!")
                    db_user_event_game.pre_start_notified = True
                    should_commit = True
                if notify_about_start and not db_user_event_game.start_notified:
                    await bot.send_message(
                        chat_id=db_user.user_id,
                        text=f"{html.bold(db_user.full_name)}, началось мероприятие {html.bold(db_event_game.event_title)}. Чтобы начать проходить контрольные точки мероприятия, напишите /stops.")
                    db_user_event_game.start_notified = True
                    should_commit = True
                if notify_about_end and not db_user_event_game.end_notified:
                    await bot.send_message(
                        chat_id=db_user.user_id,
                        text=f"{html.bold(db_user.full_name)}, мероприятие {html.bold(db_event_game.event_title)} завершено. Предлагаем ответить на вопросы после мероприятия: /questions.")
                    db_user_event_game.end_notified = True
                    should_commit = True
        if should_commit:
            await session.commit()


@dp.message(Command("stops"))
async def stops_command_handler(message: Message, state: FSMContext, db_user: DBUser):
    if db_user.role != "user":
        await message.answer("Только обычные пользователи могут воспользоваться этой командой.")
        return

    sent_message = await message.answer(
        f"Здравствуйте, {html.bold(db_user.full_name)}! Сейчас мы получаем список мероприятий EXTRA.HSE, пожалуйста, подождите...")
    await edit_stops_events_message(sent_message=sent_message, db_user=db_user, page_index=0)


async def edit_stops_events_message(sent_message: Message, db_user: DBUser, page_index: int):
    async with async_session() as session:
        db_event_games: list[DBEventGame] = await db_user.awaitable_attrs.event_games
        db_event_games_filtered: list[DBEventGame] = []
        for db_event_game in db_event_games:
            if (moscow_tz.localize(db_event_game.end) + datetime.timedelta(days=10) >=
                    datetime.datetime.now(tz=moscow_tz) >=
                    moscow_tz.localize(db_event_game.start)
                    and
                    not (await session.get(DBUserEventGame, (db_user.user_id, db_event_game.event_id))).stops_done):
                db_event_games_filtered.append(db_event_game)
        db_event_games = db_event_games_filtered

        if len(db_event_games) == 0:
            await sent_message.edit_text(
                f"Здравствуйте, {html.bold(db_user.full_name)}! Вы пока что не принимаете участие ни в каком мероприятии, в котором нужно проходить контрольные точки. Если Вы прошли все контрольные точки всех мероприятий, на которые Вы записались, то Вы - большой молодец! /start"
            )
            return

        db_event_games_limited = db_event_games[page_index*5:page_index*5+5]

        max_pages = ((len(db_event_games) - 1) // 5 + 1) - 1

        inline_keyboard_builder = InlineKeyboardBuilder()
        for i in range(len(db_event_games_limited)):
            inline_keyboard_builder.button(text=f"{(page_index * 5) + i + 1}",
                                           callback_data=EventGameStopCallback(
                                               event_id=db_event_games_limited[i].event_id,
                                               stop_index=0,
                                               points=0))
        if page_index != 0:
            inline_keyboard_builder.button(text="◀️",
                                           callback_data=EventsGamesStopsChangePageCallback(
                                               page=max(page_index - 1, 0)))
        if page_index != max_pages:
            inline_keyboard_builder.button(text="▶️️",
                                           callback_data=EventsGamesStopsChangePageCallback(
                                               page=min(page_index + 1, max_pages)))

        inline_keyboard_builder.adjust(len(db_event_games_limited), int(page_index != 0) + int(page_index != max_pages))

        await sent_message.edit_text(
            f"Здравствуйте, {html.bold(db_user.full_name)}! "
            f"Пожалуйста, выберите мероприятие, чьи контрольные точки Вы хотите пройти:\n\n"
            f"{"\n\n".join([f"{db_event_games.index(db_event_game) + 1}) " +
                            html.bold(db_event_game.event_title)
                            for db_event_game in db_event_games_limited])}\n\n"
            f"Нажмите на любую из кнопок ниже, чтобы начать выполнять контрольные точки.",
            reply_markup=inline_keyboard_builder.as_markup())


@dp.callback_query(EventsGamesStopsChangePageCallback.filter())
async def events_games_stops_change_page_callback(query: CallbackQuery, callback_data: EventsGamesStopsChangePageCallback,
                                              db_user: DBUser):
    await edit_stops_events_message(
        sent_message=query.message,
        db_user=db_user,
        page_index=callback_data.page)
    await query.answer()


@dp.callback_query(EventGameStopCallback.filter())
async def event_game_stop_callback(query: CallbackQuery, callback_data: EventGameStopCallback, db_user: DBUser):
    async with async_session() as session:
        db_event_game = await db_get_event_game(session, callback_data.event_id)
        stops: list[str] = db_event_game.stops
        stop_index = callback_data.stop_index

        if callback_data.stop_index >= len(stops):
            ball = declension(callback_data.points, "баллов", "балл", "балла")

            recent_db_user = await session.get(DBUser, db_user.user_id)
            recent_db_user.points = recent_db_user.points + callback_data.points
            db_user_event_game = await session.get(DBUserEventGame, (db_user.user_id, db_event_game.event_id))
            db_user_event_game.stops_done = True
            await session.commit()
            await query.message.edit_text(
                f"{html.bold(db_event_game.event_title)}\n\n"
                f"Вы успешно прошли контрольные точки мероприятия и получили за это {html.bold(f"{callback_data.points} {ball}")}! После завершения мероприятия не забудьте ответить на вопросы: /questions.")
        else:
            inline_keyboard_builder = InlineKeyboardBuilder()
            inline_keyboard_builder.button(text="❌ Не прошёл", callback_data=EventGameStopCallback(
                    event_id=callback_data.event_id, stop_index=callback_data.stop_index+1, points=callback_data.points))
            inline_keyboard_builder.button(text="✅ Прошёл", callback_data=EventGameStopCallback(
                    event_id=callback_data.event_id, stop_index=callback_data.stop_index+1, points=callback_data.points+1))

            await query.message.edit_text(
                f"{html.bold(db_event_game.event_title)}\n\n"
                f"{stops[stop_index]}", reply_markup=inline_keyboard_builder.as_markup())


@dp.message(Command("questions"))
async def questions_command_handler(message: Message, state: FSMContext, db_user: DBUser):
    if db_user.role != "user":
        await message.answer("Только обычные пользователи могут воспользоваться этой командой.")
        return

    sent_message = await message.answer(
        f"Здравствуйте, {html.bold(db_user.full_name)}! Сейчас мы получаем список мероприятий EXTRA.HSE, пожалуйста, подождите...")
    await edit_questions_events_message(sent_message=sent_message, db_user=db_user, page_index=0)


async def edit_questions_events_message(sent_message: Message, db_user: DBUser, page_index: int):
    async with async_session() as session:
        db_event_games: list[DBEventGame] = await db_user.awaitable_attrs.event_games
        db_event_games_filtered: list[DBEventGame] = []
        for db_event_game in db_event_games:
            if (moscow_tz.localize(db_event_game.end) + datetime.timedelta(days=10) >=
                    datetime.datetime.now(tz=moscow_tz) >=
                    moscow_tz.localize(db_event_game.end)
                    and
                    not (await session.get(DBUserEventGame, (db_user.user_id, db_event_game.event_id))).questions_done):
                db_event_games_filtered.append(db_event_game)
        db_event_games = db_event_games_filtered

        if len(db_event_games) == 0:
            await sent_message.edit_text(
                f"Здравствуйте, {html.bold(db_user.full_name)}! Вы пока что не принимаете участие ни в каком мероприятии, в котором нужно отвечать на контрольные вопросы. Если Вы ответили на все контрольные вопросы всех мероприятий, на которые Вы записались, то Вы - большой молодец! /start"
            )
            return

        db_event_games_limited = db_event_games[page_index*5:page_index*5+5]

        max_pages = ((len(db_event_games) - 1) // 5 + 1) - 1

        inline_keyboard_builder = InlineKeyboardBuilder()
        for i in range(len(db_event_games_limited)):
            inline_keyboard_builder.button(text=f"{(page_index * 5) + i + 1}",
                                           callback_data=EventGameQuestionCallback(
                                               event_id=db_event_games_limited[i].event_id,
                                               last_answer_was_right=True,
                                               question_index=0,
                                               points=0))
        if page_index != 0:
            inline_keyboard_builder.button(text="◀️",
                                           callback_data=EventsGamesQuestionsChangePageCallback(
                                               page=max(page_index - 1, 0)))
        if page_index != max_pages:
            inline_keyboard_builder.button(text="▶️️",
                                           callback_data=EventsGamesQuestionsChangePageCallback(
                                               page=min(page_index + 1, max_pages)))

        inline_keyboard_builder.adjust(len(db_event_games_limited), int(page_index != 0) + int(page_index != max_pages))

        await sent_message.edit_text(
            f"Здравствуйте, {html.bold(db_user.full_name)}! "
            f"Пожалуйста, выберите мероприятие, контрольные вопросы которого Вы хотите пройти:\n\n"
            f"{"\n\n".join([f"{db_event_games.index(db_event_game) + 1}) " +
                            html.bold(db_event_game.event_title)
                            for db_event_game in db_event_games_limited])}\n\n"
            f"Нажмите на любую из кнопок ниже, чтобы начать отвечать на контрольные вопросы.",
            reply_markup=inline_keyboard_builder.as_markup())


@dp.callback_query(EventsGamesQuestionsChangePageCallback.filter())
async def events_games_questions_change_page_callback(query: CallbackQuery, callback_data: EventsGamesQuestionsChangePageCallback,
                                              db_user: DBUser):
    await edit_questions_events_message(
        sent_message=query.message,
        db_user=db_user,
        page_index=callback_data.page)
    await query.answer()


@dp.callback_query(EventGameQuestionCallback.filter())
async def event_game_stop_callback(query: CallbackQuery, callback_data: EventGameQuestionCallback, db_user: DBUser):
    async with async_session() as session:
        db_event_game = await db_get_event_game(session, callback_data.event_id)
        questions: list[list[str]] = db_event_game.questions
        question_index = callback_data.question_index

        if question_index != 0:
            await query.answer(f"✅ Правильно!\n\n" if callback_data.last_answer_was_right else "❌ Неправильно :(\n\n")

        if callback_data.question_index >= len(questions):
            ball = declension(callback_data.points, "баллов", "балл", "балла")

            recent_db_user = await session.get(DBUser, db_user.user_id)
            recent_db_user.points = recent_db_user.points + callback_data.points
            db_user_event_game = await session.get(DBUserEventGame, (db_user.user_id, db_event_game.event_id))
            db_user_event_game.questions_done = True
            await session.commit()
            await query.message.edit_text(
                f"{html.bold(db_event_game.event_title)}\n\n"
                f"{"✅ Правильно!\n\n" if callback_data.last_answer_was_right else "❌ Неправильно :(\n\n"}"
                f"Вы успешно ответили на все контрольные вопросы мероприятия и получили за это {html.bold(f"{callback_data.points} {ball}")}! Посмотрите, какие мероприятия Вас ещё могут заинтересовать: /start.")
        else:
            question = questions[question_index]
            inline_keyboard_builder = InlineKeyboardBuilder()
            buttons = [
                InlineKeyboardButton(text=question[1], callback_data=EventGameQuestionCallback(
                    event_id=callback_data.event_id, question_index=callback_data.question_index+1,
                    last_answer_was_right=True, points=callback_data.points+1
                ).pack()),
                InlineKeyboardButton(text=question[2], callback_data=EventGameQuestionCallback(
                    event_id=callback_data.event_id, question_index=callback_data.question_index+1,
                    last_answer_was_right=False, points=callback_data.points
                ).pack()),
                InlineKeyboardButton(text=question[3], callback_data=EventGameQuestionCallback(
                    event_id=callback_data.event_id, question_index=callback_data.question_index+1,
                    last_answer_was_right=False, points=callback_data.points
                ).pack()),
            ]

            random.shuffle(buttons)

            for button in buttons:
                inline_keyboard_builder.add(button)

            inline_keyboard_builder.adjust(1, 1, 1)

            await query.message.edit_text(
                f"{html.bold(db_event_game.event_title)}\n\n"
                f"{("✅ Правильно!\n\n" if callback_data.last_answer_was_right else "❌ Неправильно :(\n\n") if callback_data.question_index != 0 else ""}"
                f"{question[0]}", reply_markup=inline_keyboard_builder.as_markup())


@dp.message(F.text)
async def message_handler(message: Message):
    await message.answer("Что-то я Вас не понял. Выберите команду из меню или пропишите /start.")


async def start_bot():
    commands = [
        BotCommand(command='start', description='Старт'),
        BotCommand(command='cancel', description='Отмена действия'),
        BotCommand(command='me', description='Посмотреть профиль'),
        BotCommand(command='stops', description="Начать прохождение контрольных точек"),
        BotCommand(command='questions', description="Начать отвечать на вопросы")
    ]
    await bot.set_my_commands(commands)
    with open(csv_file_name, "w") as csv_file:
        csv_file.write("update_id,user_id,datetime,event_type,data\n")

async def main() -> None:
    await create_tables()
    dp.startup.register(start_bot)
    scheduler = AsyncIOScheduler(timezone='Europe/Moscow')
    job = scheduler.add_job(send_notifications, 'interval', minutes=1)
    scheduler.start()
    dp.update.outer_middleware(RegistrationMiddleware())
    await dp.start_polling(bot)
    await bot.session.close()
    scheduler.remove_job(job.id)
    driver.quit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
