"""Static bot copy in Russian and English.

Course/teacher/room text comes straight from the spreadsheet and is never
translated — only our own UI strings (labels, confirmations, help) are looked
up here. Each chat/topic picks its own display language with /language.
"""

DEFAULT_LANGUAGE = "ru"
LANGUAGES = ("ru", "en")

_HELP_RU = """Привет! Покажу расписание группы и сообщу об изменениях.

/group B26-CSE-01 — выбрать группу и включить отслеживание
/today [группа] — сегодня
/tomorrow [группа] — завтра
/all [группа] — всё расписание
/groups [часть названия] — доступные группы
/subscribe — включить уведомления для выбранной группы
/unsubscribe — отключить уведомления
/morning on|off — утренняя рассылка сегодня+завтра в {digest_time}
/morning at ЧЧ:ММ — своё время рассылки для этого чата
/language ru|en — язык ответов бота в этом чате
/status — группа, подписка и время загрузки

Группу, рассылку и язык может менять только администратор беседы (в ЛС — сам
пользователь). В ЛС можно просто отправить название группы. В беседе — полной командой:
/group@имя_бота B26-CSE-01
Команды просмотра доступны всем; чужая группа в /today не меняет подписку.
В темах форума настройки отдельные. Проверка каждые 30 минут, сообщения — только о правках.
Недельная сетка не содержит календарных исключений: текстовые оговорки показаны как в таблице.
"""

_HELP_EN = """Hi! I show a group's class schedule and tell you when it changes.

/group B26-CSE-01 — pick a group and turn on tracking
/today [group] — today
/tomorrow [group] — tomorrow
/all [group] — the full schedule
/groups [part of a name] — list available groups
/subscribe — turn notifications back on for the saved group
/unsubscribe — turn notifications off
/morning on|off — daily today+tomorrow digest at {digest_time}
/morning at HH:MM — this chat's own digest time
/language ru|en — language the bot replies in for this chat
/status — group, subscription and last-load status

Only a group admin can change the group, digest, or language here (in a DM,
that's just you). In a DM you can just send the group name as plain text. In a
group chat, use the full command:
/group@bot_username B26-CSE-01
Viewing commands work for everyone; looking up another group with /today does
not change the saved subscription. Forum topics have independent settings.
Checked every 30 minutes; you're only notified about actual changes.
The weekly grid has no calendar exceptions built in: caveats in the sheet are
shown exactly as written.
"""

_STRINGS: dict[str, dict[str, object]] = {
    "ru": {
        "language_name_ru": "русский",
        "language_name_en": "английский",
        "weekdays": (
            "Понедельник",
            "Вторник",
            "Среда",
            "Четверг",
            "Пятница",
            "Суббота",
            "Воскресенье",
        ),
        "weekly_suffix": " (еженедельно)",
        "title_schedule": "Расписание",
        "title_changed": "Расписание изменилось",
        "no_lessons": "😴 Пар нет.",
        "no_lessons_changed": "Пар нет — все занятия на этот день убраны.",
        "teacher_label": "Преподаватель",
        "room_label": "Аудитория",
        "out_of_period": "На эту дату выбранный блок расписания не действует.",
        "no_data_for_date": "В таблице нет данных на эту дату.",
        "weekly_disclaimer": (
            "По недельной сетке. Текстовые оговорки о датах сохранены без интерпретации."
        ),
        "full_schedule_title": "Полное расписание",
        "sheet_label": "Лист",
        "period_label": "Период",
        "not_specified": "не указан",
        "weekly_grid_note": (
            "Недельные дни повторяются. Оговорки о датах/аудиториях — как в источнике."
        ),
        "weekly_change_note": "Изменилась недельная сетка, не отдельная календарная дата.",
        "help": _HELP_RU,
        "cannot_check_permissions": "Не могу проверить права. Назначь бота администратором беседы.",
        "admin_only": "Менять группу и подписку беседы может только администратор.",
        "source_unavailable": (
            "Не удалось загрузить расписание. Проверь источник и доступ; попробуй позже."
        ),
        "source_error_cached": "⚠️ Источник недоступен. Показываю последнюю копию от {stamp}.",
        "select_group_first": "Сначала выбери группу: /group B26-CSE-01. Список: /groups",
        "group_not_found": "Группа {name} не найдена на выбранных листах.",
        "similar_groups": "\nПохожие: {list}",
        "see_groups_list": "\nСписок: /groups",
        "specify_group": "Укажи группу: /group B26-CSE-01. Доступные группы: /groups",
        "subscription_blocked": "Подписку пока не меняю: дождись успешной загрузки источника.",
        "group_saved": (
            "Группа {group} сохранена. Отслеживание включено.\n"
            "Проверка каждые {interval} мин.; уведомляю только об изменениях."
        ),
        "unsubscribed": "Уведомления отключены. Просмотр расписания остаётся доступен.",
        "morning_need_group": "Сначала выбери группу: /group B26-CSE-01.",
        "morning_status": (
            "Утренняя рассылка сейчас {status} (время: {stamp} {tz}).\n"
            "Включить: /morning on. Выключить: /morning off.\n"
            "Своё время: /morning at 07:30."
        ),
        "morning_on": (
            "Утренняя рассылка включена: каждый день в {stamp} — расписание на сегодня и завтра."
        ),
        "morning_off": "Утренняя рассылка выключена.",
        "morning_time_set": "Утренняя рассылка теперь приходит в {stamp}.",
        "morning_invalid_time": (
            "Не понял время. Пример: /morning at 07:30 (часы 00–23, минуты 00–59)."
        ),
        "digest_on": "включена",
        "digest_off": "выключена",
        "subscription_on": "включена",
        "subscription_off": "отключена",
        "status_block": (
            "Группа: {group}\n"
            "Подписка: {subscription}\n"
            "Утренняя рассылка: {digest} ({digest_time})\n"
            "Язык: {language}\n"
            "Последняя загрузка: {stamp}\nЧасовой пояс: {tz}\n"
            "Интервал: {interval} мин.\n"
            "Источник: {source_status}"
        ),
        "status_not_selected": "не выбрана",
        "status_never_loaded": "ещё не загружено после запуска",
        "status_source_error": "ошибка — сохранена предыдущая копия",
        "status_source_ok": "OK",
        "unknown_command": "Неизвестная команда. Список: /help",
        "generic_error": "Не удалось обработать запрос. Попробуй ещё раз.",
        "groups_not_found": "Группы не найдены.",
        "groups_title": "Доступные группы",
        "language_usage": "Выбери язык: /language ru или /language en. Сейчас: {current}.",
        "language_set": "Язык переключён на {name}.",
    },
    "en": {
        "language_name_ru": "Russian",
        "language_name_en": "English",
        "weekdays": (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ),
        "weekly_suffix": " (weekly)",
        "title_schedule": "Schedule",
        "title_changed": "Schedule changed",
        "no_lessons": "😴 No classes.",
        "no_lessons_changed": "No classes — everything on this day was cancelled.",
        "teacher_label": "Teacher",
        "room_label": "Room",
        "out_of_period": "This date falls outside the configured schedule period.",
        "no_data_for_date": "No data for this date in the sheet.",
        "weekly_disclaimer": (
            "Based on the weekly grid. Any date/room caveats are shown exactly as in the source."
        ),
        "full_schedule_title": "Full schedule",
        "sheet_label": "Sheet",
        "period_label": "Period",
        "not_specified": "not set",
        "weekly_grid_note": (
            "Weekly days repeat. Date/room caveats are shown exactly as in the source."
        ),
        "weekly_change_note": (
            "This is a change to the weekly grid, not one specific calendar date."
        ),
        "help": _HELP_EN,
        "cannot_check_permissions": "Can't check permissions. Make the bot a group admin.",
        "admin_only": "Only a group admin can change the group or subscription here.",
        "source_unavailable": (
            "Couldn't load the schedule. Check the source and access; try again later."
        ),
        "source_error_cached": "⚠️ Source unavailable. Showing the last good copy from {stamp}.",
        "select_group_first": "Pick a group first: /group B26-CSE-01. List: /groups",
        "group_not_found": "Group {name} was not found on the configured sheets.",
        "similar_groups": "\nSimilar: {list}",
        "see_groups_list": "\nList: /groups",
        "specify_group": "Specify a group: /group B26-CSE-01. Available groups: /groups",
        "subscription_blocked": "Not changing the subscription yet: waiting for a successful load.",
        "group_saved": (
            "Group {group} saved. Tracking is on.\n"
            "Checked every {interval} min; you'll only be notified about changes."
        ),
        "unsubscribed": "Notifications turned off. You can still view the schedule.",
        "morning_need_group": "Pick a group first: /group B26-CSE-01.",
        "morning_status": (
            "The morning digest is currently {status} (time: {stamp} {tz}).\n"
            "Turn on: /morning on. Turn off: /morning off.\n"
            "Custom time: /morning at 07:30."
        ),
        "morning_on": (
            "Morning digest turned on: every day at {stamp} — today's and tomorrow's schedule."
        ),
        "morning_off": "Morning digest turned off.",
        "morning_time_set": "Morning digest now arrives at {stamp}.",
        "morning_invalid_time": (
            "Couldn't parse that time. Example: /morning at 07:30 (00–23 hours, 00–59 minutes)."
        ),
        "digest_on": "on",
        "digest_off": "off",
        "subscription_on": "on",
        "subscription_off": "off",
        "status_block": (
            "Group: {group}\n"
            "Subscription: {subscription}\n"
            "Morning digest: {digest} ({digest_time})\n"
            "Language: {language}\n"
            "Last loaded: {stamp}\nTimezone: {tz}\n"
            "Interval: {interval} min\n"
            "Source: {source_status}"
        ),
        "status_not_selected": "none selected",
        "status_never_loaded": "not loaded yet since startup",
        "status_source_error": "error — showing the last good copy",
        "status_source_ok": "OK",
        "unknown_command": "Unknown command. See /help",
        "generic_error": "Couldn't process that. Please try again.",
        "groups_not_found": "No groups found.",
        "groups_title": "Available groups",
        "language_usage": "Choose a language: /language ru or /language en. Current: {current}.",
        "language_set": "Language switched to {name}.",
    },
}


def normalize_language(value: str | None) -> str:
    value = (value or "").strip().lower()
    return value if value in LANGUAGES else DEFAULT_LANGUAGE


def t(lang: str | None, key: str, **kwargs) -> str:
    text = _STRINGS[normalize_language(lang)][key]
    return text.format(**kwargs) if kwargs else text
