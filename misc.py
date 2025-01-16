import json

from sqlalchemy import types


class DBJSON(types.TypeDecorator):
    impl = types.String

    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        return json.loads(value)


# число, "0 друзей", "1 друг", "2 друга"
def declension(n, form_0, form_1, form_2):
    units = n % 10
    tens = (n // 10) % 10
    if tens == 1:
        return form_0
    if units in [0, 5, 6, 7, 8, 9]:
        return form_0
    if units == 1:
        return form_1
    if units in [2, 3, 4]:
        return form_2