import asyncio
import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from database import (
    AnswerOption,
    Question,
    QuizAttempt,
    User,
    get_session,
    init_db,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class QuizState:
    question_ids: list[int]
    current_index: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    answered: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


active_quizzes: dict[int, QuizState] = {}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Testni boshlash", callback_data="start_quiz")],
            [InlineKeyboardButton(text="Statistika", callback_data="stats")],
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
            [InlineKeyboardButton(text="Ortga", callback_data="back_main")],
        ]
    )


def answer_keyboard(options: list[AnswerOption]) -> InlineKeyboardMarkup:
    letters = ["A", "B", "C", "D"]
    rows = []
    for letter, option in zip(letters, options):
        rows.append([InlineKeyboardButton(text=letter, callback_data=f"answer:{option.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def next_keyboard(is_finished: bool = False) -> InlineKeyboardMarkup:
    text = "Natijani ko'rish" if is_finished else "Keyingi savol"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data="next_question")]]
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
    await bot.send_message(chat_id, text, reply_markup=answer_keyboard(options))


async def finish_quiz(bot: Bot, chat_id: int, user_id: int) -> None:
    state = active_quizzes.pop(user_id, None)
    if state is None:
        await bot.send_message(chat_id, "Faol test topilmadi.", reply_markup=main_menu_keyboard())
        return

    total = len(state.question_ids)
    percent = round((state.correct_count / total) * 100, 1) if total else 0
    finished_at = datetime.now(timezone.utc)

    with get_session() as session:
        db_user = session.query(User).filter(User.telegram_id == user_id).one()
        db_user.total_attempts += 1
        db_user.total_questions += total
        db_user.total_correct += state.correct_count
        db_user.total_wrong += state.wrong_count
        db_user.last_seen_at = finished_at
        session.add(
            QuizAttempt(
                user_id=db_user.id,
                question_count=total,
                correct_count=state.correct_count,
                wrong_count=state.wrong_count,
                started_at=state.started_at,
                finished_at=finished_at,
            )
        )
        session.commit()

    await bot.send_message(
        chat_id,
        "Test yakunlandi!\n\n"
        f"Jami savol: {total}\n"
        f"To'g'ri javoblar: {state.correct_count}\n"
        f"Noto'g'ri javoblar: {state.wrong_count}\n"
        f"Natija: {percent}%",
        reply_markup=main_menu_keyboard(),
    )


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
            "Sizning statistikangiz:\n\n"
            f"Urinishlar: {user.total_attempts}\n"
            f"Jami savollar: {user.total_questions}\n"
            f"To'g'ri javoblar: {user.total_correct}\n"
            f"Noto'g'ri javoblar: {user.total_wrong}\n"
            f"Umumiy foiz: {percent}%"
        )
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    await callback.answer()


async def on_start(message: Message) -> None:
    await upsert_user(message)
    await message.answer(
        "Assalomu alaykum! Test botga xush kelibsiz.",
        reply_markup=main_menu_keyboard(),
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
            reply_markup=main_menu_keyboard(),
        )
        await callback.answer()
        return

    active_quizzes[callback.from_user.id] = QuizState(
        question_ids=question_ids,
        started_at=datetime.now(timezone.utc),
    )
    await callback.message.edit_text("Test boshlandi. Omad!")
    await send_question(callback.bot, callback.message.chat.id, callback.from_user.id)
    await callback.answer()


async def on_answer(callback: CallbackQuery) -> None:
    state = active_quizzes.get(callback.from_user.id)
    if state is None:
        await callback.answer("Avval testni boshlang.", show_alert=True)
        return
    if state.answered:
        await callback.answer("Bu savolga javob berib bo'lgansiz.", show_alert=True)
        return

    selected_id = int(callback.data.split(":")[1])
    with get_session() as session:
        selected = session.get(AnswerOption, selected_id)
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

    state.current_index += 1
    is_finished = state.current_index >= len(state.question_ids)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(result, reply_markup=next_keyboard(is_finished))
    await callback.answer()


async def on_next_question(callback: CallbackQuery) -> None:
    state = active_quizzes.get(callback.from_user.id)
    if state is None:
        await callback.message.edit_text("Test yakunlangan.", reply_markup=main_menu_keyboard())
        await callback.answer()
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await send_question(callback.bot, callback.message.chat.id, callback.from_user.id)
    await callback.answer()


async def on_back_main(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Bosh menyu", reply_markup=main_menu_keyboard())
    await callback.answer()


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable is required")

    init_db()

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.message.register(on_start, CommandStart())
    dp.callback_query.register(on_start_quiz, F.data == "start_quiz")
    dp.callback_query.register(on_quiz_size, F.data.startswith("quiz_size:"))
    dp.callback_query.register(on_answer, F.data.startswith("answer:"))
    dp.callback_query.register(on_next_question, F.data == "next_question")
    dp.callback_query.register(show_stats, F.data == "stats")
    dp.callback_query.register(on_back_main, F.data == "back_main")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
