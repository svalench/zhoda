"""Фикстура для red_team-прогона: намеренно дырявый login."""


def login(user: str, password: str) -> bool:
    query = f"SELECT * FROM users WHERE name='{user}' AND pass='{password}'"
    return db.execute(query).fetchone() is not None  # noqa: F821
