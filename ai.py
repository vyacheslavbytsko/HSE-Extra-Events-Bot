from langchain_community.chat_models import GigaChat
from langchain_core.messages import SystemMessage, HumanMessage

from tokens import gigachat_token

giga = GigaChat(credentials=gigachat_token,
                model='GigaChat:latest',
                verify_ssl_certs=False
                )


def get_questions_from_gigachat(title: str, description: str):
    system_message = SystemMessage(content='Когда пользователь отправит тебе название и описание мероприятия, '
                                           'ты должен будешь составить 5 контрольных вопросов. '
                                           'Формат каждого контрольного вопроса: '
                                           '\"Контрольный вопрос\\nОтвет 1\\nОтвет 2\\nОтвет 3\". '
                                           'Учти, что самый первый ответ должен быть правильным, '
                                           'а остальные - нет. Пять вопросов. '
                                           'Учти, что эти вопросы будут задаваться после прохождения '
                                           'мероприятия. Будь креативным.')

    response = giga([
        system_message,
        HumanMessage(f"Название: {title}, описание: {description}")
    ])

    print(response.content)
    return response.content

def get_stops_from_gigachat(title: str, description: str):
    system_message = SystemMessage(content='Когда пользователь отправит тебе название и описание мероприятия, '
                                           'ты должен будешь составить 5 контрольных точек этого мероприятия. '
                                           'Это может быть поговорить с каким-либо экспертом либо что-то подобное. '
                                           'Формат каждой контрольный точки: '
                                           '\"Контрольная точка\". Пять контрольных точек.'
                                           'Каждая контрольная точка - с новой строки.'
                                           'Будь креативным. Нумеруй контрольные точки.')

    response = giga([
        system_message,
        HumanMessage(f"Название: {title}, описание: {description}")
    ])

    print(response.content)
    return response.content