from urllib.request import urlopen

import dateparser
import icalendar
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

from classes import Event, RoughEvent
from db import async_session, db_get_event_game

options = Options()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_argument("--disable-gpu")
options.add_argument("disable-infobars")
options.add_argument("--disable-extensions")
options.add_argument("window-size=1200x600")
options.add_argument("headless")
driver = webdriver.Chrome(options=options)

def get_event_from_internet(event_id: str) -> Event:
    driver.get(f"https://extra.hse.ru/announcements/{event_id}.html")
    event_html = driver.find_element(by=By.CLASS_NAME, value="post")
    event = Event()
    event.id = event_id
    event.title = event_html.find_element(by=By.CLASS_NAME, value="post_single").text
    event.rating = event_html.find_element(by=By.CLASS_NAME, value="rating-round").text
    event.ical = \
        icalendar.Calendar.from_ical(urlopen(f"https://extra.hse.ru/events/ics/{event_id}.ics").read()).walk("VEVENT")[
            0]
    event.description = event_html.find_element(by=By.CLASS_NAME, value="post__text").text.replace(
        "Добавить в календарь", "").strip()
    event.address = event_html.find_elements(by=By.CLASS_NAME, value="articleMetaItem")[1].find_element(
        by=By.CLASS_NAME, value="articleMetaItem__content").text
    return event


# rough - грубые, то есть не совсем полная информация
async def get_rough_events_from_internet(with_games: bool):
    async with async_session() as session:
        driver.get("https://extra.hse.ru/news/announcements/")
        driver.implicitly_wait(0.5)
        events_html = driver.find_elements(by=By.CLASS_NAME, value="b-events")
        events = []
        for event_html in events_html:
            event = RoughEvent()
            event.id = event_html.find_element(by=By.CLASS_NAME, value="b-events__body_title").find_element(by=By.TAG_NAME,
                                                                                                            value="a").get_attribute(
                name="href").split("/")[-1].split(".")[0]
            event.title = event_html.find_element(by=By.CLASS_NAME, value="b-events__body_title").find_element(
                by=By.TAG_NAME, value="a").text
            event_date_text = event_html.find_element(by=By.CLASS_NAME, value="b-events__title").find_element(
                by=By.CLASS_NAME, value="title").text
            event.date = dateparser.parse(
                event_date_text.split(",")[0] if event_date_text[0].isalpha() else event_date_text).replace(hour=0,
                                                                                                            minute=0,
                                                                                                            second=0,
                                                                                                            microsecond=0)
            db_event_game = await db_get_event_game(session=session, event_id=event.id)
            if (db_event_game is not None) == with_games:
                events.append(event)
        return events