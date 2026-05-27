import asyncio
import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv

from database import (
    AnswerOption,
    Question,
    QuizAnswer,
    QuizAttempt,
    User,
    get_session,
    init_db,
)
from import_tests import import_tests

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
DEFAULT_TEST_FILE = Path("tests_2025_26.json")
FINISH_TEST_TEXT = "Testni yakunlash"


def admin_ids() -> set[int]:
    raw_ids = os.getenv("ADMIN_IDS", "")
    ids = set()
    for raw_id in raw_ids.replace(";", ",").split(","):
        raw_id = raw_id.strip()
        if raw_id.isdigit():
            ids.add(int(raw_id))
    return ids


def is_admin(user_id: int) -> bool:
    return user_id in admin_ids()


@dataclass
class AnswerRecord:
    question_id: int
    selected_option_id: int
    correct_option_id: int
    is_correct: bool
    answered_at: datetime


@dataclass
class QuizState:
    question_ids: list[int]
    current_index: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    answered: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    answers: list[AnswerRecord] = field(default_factory=list)


active_quizzes: dict[int, QuizState] = {}


def main_menu_keyboard(user_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Testni boshlash", callback_data="start_quiz")],
        [InlineKeyboardButton(text="Natijalarim", callback_data="stats")],
    ]
    if user_id is not None and is_admin(user_id):
        rows.append([InlineKeyboardButton(text="Admin panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reset_stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Ha, tozalash", callback_data="reset_stats"),
                InlineKeyboardButton(text="Bekor qilish", callback_data="stats"),
            ]
        ]
    )


def results_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Statistikani tozalash", callback_data="reset_stats_confirm")],
            [InlineKeyboardButton(text="Bosh menyu", callback_data="back_main")],
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Userlar ro'yxati", callback_data="admin_users:0")],
            [InlineKeyboardButton(text="Bosh menyu", callback_data="back_main")],
        ]
    )


def quiz_size_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="10 talik", callback_data="quiz_size:10"),
                InlineKeyboardButton(text="20 talik", callback_data="quiz_size:20"),
                InlineKeyboardButton(text="30 talik", callback_data="quiz_size:30"),
            ],
            [
                InlineKeyboardButton(text="1-variant (50 ta)", callback_data="quiz_variant:1"),
                InlineKeyboardButton(text="2-variant (50 ta)", callback_data="quiz_variant:2"),
            ],
            [
                InlineKeyboardButton(text="3-variant (50 ta)", callback_data="quiz_variant:3"),
                InlineKeyboardButton(text="4-variant (50 ta)", callback_data="quiz_variant:4"),
            ],
            [InlineKeyboardButton(text="Ortga", callback_data="back_main")],
        ]
    )


def answer_keyboard(question_id: int, options: list[AnswerOption]) -> InlineKeyboardMarkup:
    letters = ["A", "B", "C", "D"]
    rows = []
    for letter, option in zip(letters, options):
        rows.append(
            [
                InlineKeyboardButton(
                    text=letter,
                    callback_data=f"answer:{question_id}:{option.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def next_keyboard(is_finished: bool = False) -> InlineKeyboardMarkup:
    text = "Natijani ko'rish" if is_finished else "Keyingi savol"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data="next_question")]]
    )


def quiz_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=FINISH_TEST_TEXT)]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def upsert_user(message_or_callback: Message | CallbackQuery) -> User:
    tg_user = message_or_callback.from_user
    with get_session() as session:
        user = session.query(User).filter(User.telegram_id == tg_user.id).one_or_none()
        if user is None:
            user = User(telegram_id=tg_user.id)
            session.add(user)
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.last_name = tg_user.last_name
        user.last_seen_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(user)
        return user


async def get_random_question_ids(limit: int) -> list[int]:
    with get_session() as session:
        ids = [row[0] for row in session.query(Question.id).all()]
    random.shuffle(ids)
    return ids[:limit]


async def get_variant_question_ids(variant_number: int, variant_size: int = 50) -> list[int]:
    offset = (variant_number - 1) * variant_size
    with get_session() as session:
        return [
            row[0]
            for row in (
                session.query(Question.id)
                .order_by(Question.id.asc())
                .offset(offset)
                .limit(variant_size)
                .all()
            )
        ]


def seed_tests_if_empty() -> None:
    with get_session() as session:
        question_count = session.query(Question).count()

    if question_count > 0 or not DEFAULT_TEST_FILE.exists():
        return

    imported_count = import_tests(DEFAULT_TEST_FILE, replace=False)
    logger.info("Seeded %s tests from %s", imported_count, DEFAULT_TEST_FILE)


async def send_question(bot: Bot, chat_id: int, user_id: int) -> None:
    state = active_quizzes[user_id]
    if state.current_index >= len(state.question_ids):
        await finish_quiz(bot, chat_id, user_id)
        return

    question_id = state.question_ids[state.current_index]
    with get_session() as session:
        question = session.get(Question, question_id)
        options = list(question.options)

    random.shuffle(options)
    variants = "\n".join(
        f"{letter}) {option.text}" for letter, option in zip(["A", "B", "C", "D"], options)
    )
    text = (
        f"Savol {state.current_index + 1}/{len(state.question_ids)}\n\n"
        f"{question.text}\n\n"
        f"{variants}"
    )
    state.answered = False
    await bot.send_message(chat_id, text, reply_markup=answer_keyboard(question_id, options))


async def finish_quiz(bot: Bot, chat_id: int, user_id: int, manually_finished: bool = False) -> None:
    state = active_quizzes.pop(user_id, None)
    if state is None:
        await bot.send_message(
            chat_id,
            "Faol test topilmadi.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await bot.send_message(chat_id, "Bosh menyu", reply_markup=main_menu_keyboard(user_id))
        return

    total = len(state.answers) if manually_finished else len(state.question_ids)
    if total == 0:
        await bot.send_message(
            chat_id,
            "Test yakunlandi. Hali hech bir savolga javob berilmagan.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await bot.send_message(chat_id, "Bosh menyu", reply_markup=main_menu_keyboard(user_id))
        return

    percent = round((state.correct_count / total) * 100, 1) if total else 0
    finished_at = datetime.now(timezone.utc)

    with get_session() as session:
        db_user = session.query(User).filter(User.telegram_id == user_id).one()
        db_user.total_attempts += 1
        db_user.total_questions += total
        db_user.total_correct += state.correct_count
        db_user.total_wrong += state.wrong_count
        db_user.last_seen_at = finished_at
        attempt = QuizAttempt(
            user_id=db_user.id,
            question_count=total,
            correct_count=state.correct_count,
            wrong_count=state.wrong_count,
            started_at=state.started_at,
            finished_at=finished_at,
        )
        session.add(attempt)
        session.flush()
        for answer in state.answers:
            session.add(
                QuizAnswer(
                    attempt_id=attempt.id,
                    question_id=answer.question_id,
                    selected_option_id=answer.selected_option_id,
                    correct_option_id=answer.correct_option_id,
                    is_correct=answer.is_correct,
                    answered_at=answer.answered_at,
                )
            )
        session.commit()

    await bot.send_message(
        chat_id,
        "Test yakunlandi!\n\n"
        f"Ishlangan savol: {total}\n"
        f"To'g'ri javoblar: {state.correct_count}\n"
        f"Noto'g'ri javoblar: {state.wrong_count}\n"
        f"Natija: {percent}%",
        reply_markup=ReplyKeyboardRemove(),
    )
    await bot.send_message(chat_id, "Bosh menyu", reply_markup=main_menu_keyboard(user_id))


async def show_stats(callback: CallbackQuery) -> None:
    await upsert_user(callback)
    with get_session() as session:
        user = session.query(User).filter(User.telegram_id == callback.from_user.id).one()
        percent = (
            round((user.total_correct / user.total_questions) * 100, 1)
            if user.total_questions
            else 0
        )
        text = (
            "Natijalarim:\n\n"
            f"Ishlangan testlar: {user.total_attempts}\n"
            f"Jami savollar: {user.total_questions}\n"
            f"To'g'ri javoblar: {user.total_correct}\n"
            f"Noto'g'ri javoblar: {user.total_wrong}\n"
            f"O'rtacha yechilish: {percent}%"
        )
    await callback.message.edit_text(text, reply_markup=results_keyboard())
    await callback.answer()


async def confirm_reset_stats(callback: CallbackQuery) -> None:
    await upsert_user(callback)
    await callback.message.edit_text(
        "Statistikangizni tozalaysizmi? Bu amal test urinishlari tarixini ham o'chiradi.",
        reply_markup=reset_stats_keyboard(),
    )
    await callback.answer()


async def reset_stats(callback: CallbackQuery) -> None:
    await upsert_user(callback)
    with get_session() as session:
        user = session.query(User).filter(User.telegram_id == callback.from_user.id).one()
        attempt_ids = [attempt_id for (attempt_id,) in session.query(QuizAttempt.id).filter(QuizAttempt.user_id == user.id)]
        if attempt_ids:
            session.query(QuizAnswer).filter(QuizAnswer.attempt_id.in_(attempt_ids)).delete(
                synchronize_session=False
            )
            session.query(QuizAttempt).filter(QuizAttempt.id.in_(attempt_ids)).delete(
                synchronize_session=False
            )
        user.total_attempts = 0
        user.total_questions = 0
        user.total_correct = 0
        user.total_wrong = 0
        user.last_seen_at = datetime.now(timezone.utc)
        session.commit()

    await callback.message.edit_text(
        "Statistikangiz tozalandi.",
        reply_markup=main_menu_keyboard(callback.from_user.id),
    )
    await callback.answer()


def format_user_line(user: User, number: int) -> str:
    username = f"@{user.username}" if user.username else "username yo'q"
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part) or "ism yo'q"
    last_seen = user.last_seen_at.strftime("%Y-%m-%d %H:%M UTC") if user.last_seen_at else "noma'lum"
    return (
        f"{number}. {username} | {full_name}\n"
        f"ID: {user.telegram_id} | Oxirgi faollik: {last_seen}\n"
        f"Testlar: {user.total_attempts}, Savollar: {user.total_questions}, To'g'ri: {user.total_correct}"
    )


async def admin_panel(callback: CallbackQuery) -> None:
    await upsert_user(callback)
    if not is_admin(callback.from_user.id):
        await callback.answer("Siz admin emassiz.", show_alert=True)
        return

    with get_session() as session:
        users_count = session.query(User).count()
        attempts_count = session.query(QuizAttempt).count()

    await callback.message.edit_text(
        "Admin panel\n\n"
        f"Userlar soni: {users_count}\n"
        f"Test urinishlari: {attempts_count}",
        reply_markup=admin_keyboard(),
    )
    await callback.answer()


async def admin_users(callback: CallbackQuery) -> None:
    await upsert_user(callback)
    if not is_admin(callback.from_user.id):
        await callback.answer("Siz admin emassiz.", show_alert=True)
        return

    page = int(callback.data.split(":")[1])
    page_size = 10
    offset = page * page_size
    with get_session() as session:
        users_count = session.query(User).count()
        users = (
            session.query(User)
            .order_by(User.last_seen_at.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )

    if not users:
        text = "Hali userlar yo'q."
    else:
        lines = [format_user_line(user, offset + index) for index, user in enumerate(users, start=1)]
        text = f"Userlar ({users_count} ta)\n\n" + "\n\n".join(lines)

    rows = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="Oldingi", callback_data=f"admin_users:{page - 1}"))
    if offset + page_size < users_count:
        nav.append(InlineKeyboardButton(text="Keyingi", callback_data=f"admin_users:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="Admin panel", callback_data="admin_panel")])
    rows.append([InlineKeyboardButton(text="Bosh menyu", callback_data="back_main")])

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def on_start(message: Message) -> None:
    await upsert_user(message)
    await message.answer(
        "Assalomu alaykum! Test botga xush kelibsiz.",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )


async def on_start_quiz(callback: CallbackQuery) -> None:
    await upsert_user(callback)
    await callback.message.edit_text("Nechta savolli test ishlamoqchisiz?", reply_markup=quiz_size_keyboard())
    await callback.answer()


async def on_quiz_size(callback: CallbackQuery) -> None:
    await upsert_user(callback)
    size = int(callback.data.split(":")[1])
    question_ids = await get_random_question_ids(size)

    if len(question_ids) < size:
        await callback.message.edit_text(
            f"Bazada {size} ta savol yetarli emas. Hozir bor savollar soni: {len(question_ids)}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    active_quizzes[callback.from_user.id] = QuizState(
        question_ids=question_ids,
        started_at=datetime.now(timezone.utc),
    )
    await callback.message.edit_text("Test boshlandi. Omad!")
    await callback.message.answer(
        "Test davomida pastdagi tugma orqali testni yakunlashingiz mumkin.",
        reply_markup=quiz_reply_keyboard(),
    )
    await send_question(callback.bot, callback.message.chat.id, callback.from_user.id)
    await callback.answer()


async def on_quiz_variant(callback: CallbackQuery) -> None:
    await upsert_user(callback)
    variant_number = int(callback.data.split(":")[1])
    question_ids = await get_variant_question_ids(variant_number)

    if len(question_ids) < 50:
        await callback.message.edit_text(
            f"{variant_number}-variant uchun 50 ta savol yetarli emas. Hozir bor savollar soni: {len(question_ids)}",
            reply_markup=main_menu_keyboard(callback.from_user.id),
        )
        await callback.answer()
        return

    active_quizzes[callback.from_user.id] = QuizState(
        question_ids=question_ids,
        started_at=datetime.now(timezone.utc),
    )
    await callback.message.edit_text(f"{variant_number}-variant boshlandi. Omad!")
    await callback.message.answer(
        "Test davomida pastdagi tugma orqali testni yakunlashingiz mumkin.",
        reply_markup=quiz_reply_keyboard(),
    )
    await send_question(callback.bot, callback.message.chat.id, callback.from_user.id)
    await callback.answer()


async def on_finish_test(message: Message) -> None:
    await upsert_user(message)
    if message.from_user.id not in active_quizzes:
        await message.answer(
            "Hozir faol test yo'q.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer("Bosh menyu", reply_markup=main_menu_keyboard(message.from_user.id))
        return

    await finish_quiz(message.bot, message.chat.id, message.from_user.id, manually_finished=True)


async def on_answer(callback: CallbackQuery) -> None:
    state = active_quizzes.get(callback.from_user.id)
    if state is None:
        await callback.answer("Avval testni boshlang.", show_alert=True)
        return
    if state.answered:
        await callback.answer("Bu savolga javob berib bo'lgansiz.", show_alert=True)
        return

    _, question_id_raw, selected_id_raw = callback.data.split(":")
    question_id = int(question_id_raw)
    selected_id = int(selected_id_raw)
    current_question_id = state.question_ids[state.current_index]
    if question_id != current_question_id:
        await callback.answer("Bu eski savol tugmasi. Joriy savolga javob bering.", show_alert=True)
        return

    with get_session() as session:
        selected = session.get(AnswerOption, selected_id)
        if selected is None or selected.question_id != question_id:
            await callback.answer("Javob varianti topilmadi.", show_alert=True)
            return
        question = session.get(Question, selected.question_id)
        correct = (
            session.query(AnswerOption)
            .filter(AnswerOption.question_id == selected.question_id, AnswerOption.is_correct.is_(True))
            .one()
        )

    state.answered = True
    if selected.is_correct:
        state.correct_count += 1
        result = "To'g'ri!"
    else:
        state.wrong_count += 1
        result = f"Noto'g'ri.\nTo'g'ri javob: {correct.text}"

    if question.info:
        result = f"{result}\n\nInfo: {question.info}"

    state.answers.append(
        AnswerRecord(
            question_id=question.id,
            selected_option_id=selected.id,
            correct_option_id=correct.id,
            is_correct=selected.is_correct,
            answered_at=datetime.now(timezone.utc),
        )
    )
    state.current_index += 1
    is_finished = state.current_index >= len(state.question_ids)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(result, reply_markup=next_keyboard(is_finished))
    await callback.answer()


async def on_next_question(callback: CallbackQuery) -> None:
    state = active_quizzes.get(callback.from_user.id)
    if state is None:
        await callback.message.edit_text("Test yakunlangan.", reply_markup=main_menu_keyboard(callback.from_user.id))
        await callback.answer()
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await send_question(callback.bot, callback.message.chat.id, callback.from_user.id)
    await callback.answer()


async def on_back_main(callback: CallbackQuery) -> None:
    await upsert_user(callback)
    await callback.message.edit_text("Bosh menyu", reply_markup=main_menu_keyboard(callback.from_user.id))
    await callback.answer()


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable is required")

    init_db()
    seed_tests_if_empty()

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.message.register(on_start, CommandStart())
    dp.message.register(on_finish_test, F.text == FINISH_TEST_TEXT)
    dp.callback_query.register(on_start_quiz, F.data == "start_quiz")
    dp.callback_query.register(on_quiz_size, F.data.startswith("quiz_size:"))
    dp.callback_query.register(on_quiz_variant, F.data.startswith("quiz_variant:"))
    dp.callback_query.register(on_answer, F.data.startswith("answer:"))
    dp.callback_query.register(on_next_question, F.data == "next_question")
    dp.callback_query.register(show_stats, F.data == "stats")
    dp.callback_query.register(confirm_reset_stats, F.data == "reset_stats_confirm")
    dp.callback_query.register(reset_stats, F.data == "reset_stats")
    dp.callback_query.register(admin_panel, F.data == "admin_panel")
    dp.callback_query.register(admin_users, F.data.startswith("admin_users:"))
    dp.callback_query.register(on_back_main, F.data == "back_main")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
