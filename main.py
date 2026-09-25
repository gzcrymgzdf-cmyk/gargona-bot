import asyncio
import logging
import os
import secrets
import string
from datetime import datetime, timedelta

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_TOKEN_HERE")
BOT_USERNAME = os.getenv("BOT_USERNAME", "GargonaGarantBot")
SUPPORT = "@GargonaSupport"
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()}
DB_PATH = "gargona.db"
MIN_UNLOCK = 500.0

WALLETS = {
    "usdt_trc20": {
        "name": "USDT (TRC20)",
        "ticker": "USDT",
        "address": "TGWPxk3LLpMYzw83YFtYbDZ8w34RJavfgz",
        "rate": 1.0,
    },
    "btc": {
        "name": "Bitcoin (BTC)",
        "ticker": "BTC",
        "address": "bc1q6cs82cvhxw7pgup9kngc5vmnrgya2c3ppcx4rg",
        "rate": 60000.0,
    },
    "eth": {
        "name": "Ethereum (ETH)",
        "ticker": "ETH",
        "address": "0xC35fd38625fb317847719e8002Fee707D9c45D0A",
        "rate": 3000.0,
    },
}

logging.basicConfig(level=logging.INFO)
router = Router()
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)


class DepositFSM(StatesGroup):
    enter_amount = State()


class DealFSM(StatesGroup):
    enter_amount = State()


async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            code TEXT UNIQUE,
            balance REAL DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS deposits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            crypto TEXT,
            address TEXT,
            amount REAL,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS deals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            creator_id INTEGER,
            partner_id INTEGER,
            amount REAL,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            expires_at TEXT,
            creator_sent INTEGER DEFAULT 0,
            partner_received INTEGER DEFAULT 0
        );
        """)
        await db.commit()


def gen_code(n=8):
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(n))


async def get_or_create_user(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, code, balance FROM users WHERE user_id=?", (user_id,)
        )
        row = await cur.fetchone()
        if row:
            return {"user_id": row[0], "code": row[1], "balance": row[2]}

        code = gen_code(8)
        while True:
            c = await db.execute("SELECT 1 FROM users WHERE code=?", (code,))
            if not await c.fetchone():
                break
            code = gen_code(8)

        await db.execute(
            "INSERT INTO users(user_id, code, balance, created_at) VALUES(?,?,?,?)",
            (user_id, code, 0.0, datetime.utcnow().isoformat()),
        )
        await db.commit()
        return {"user_id": user_id, "code": code, "balance": 0.0}


def main_kb(user: dict) -> InlineKeyboardMarkup:
    rows = []
    if user["balance"] >= MIN_UNLOCK:
        rows.append([InlineKeyboardButton(text="🔗 Создать сделку", callback_data="deal:create")])
        rows.append([InlineKeyboardButton(text="📊 Профиль", callback_data="profile")])
    rows.append([InlineKeyboardButton(text="💰 Пополнить депозит", callback_data="dep:start")])
    rows.append([InlineKeyboardButton(
        text="🆘 Поддержка 24/7",
        url=f"https://t.me/{SUPPORT.lstrip('@')}"
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def menu_text(user: dict) -> str:
    txt = (
        f"🛡 <b>Gargona — безопасные сделки</b>\n\n"
       дел f"Ваш ID: <code>{user['code']}</code>\n"
        f"Баланс: <b>{user['balance']:.2f} USDT</b>\n\n"
    )
    if user["balance"] < MIN_UNLOCK:
        txt += (
            f"⚠️ Для доступа к сделкам пополните депозит "
            f"минимум на <b>{MIN_UNLOCK:.0f} USDT</b>."
        )
    else:
        txt += "✅ Доступ к скам открыт."
    return txt


async def safe_edit(message: Message, text: str, kb: InlineKeyboardMarkup):
    try:
        await message.edit_text(text, reply_markup=kb)
    except Exception:
        await message.answer(text, reply_markup=kb)


@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    parts = (msg.text or "").split(maxsplit=1)
    payload = parts[1] if len(parts) > 1 else ""
    user = await get_or_create_user(msg.from_user.id)

    if payload.startswith("deal_"):
        await show_deal_invite(msg, user, payload[5:])
        return

    await msg.answer(
        f"👋 <b>Добро пожаловать в Gargona!</b>\n\n"
        f"🛡 Безопасные сделки под защитой посредника.\n\n" + menu_text(user),
        reply_markup=main_kb(user),
    )


@router.callback_query(F.data == "menu")
async def menu_cb(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(cb.from_user.id)
    await safe_edit(cb.message, menu_text(user), main_kb(user))


@router.callback_query(F.data == "profile")
async def profile_cb(cb: CallbackQuery):
    user = await get_or_create_user(cb.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅ Назад", callback_data="menu")]
    ])
    await safe_edit(
        cb.message,
        f"👤 <b>Профиль</b>\n\n"
        f"Уникальный ID: <code>{user['code']}</code>\n"
        f"Telegram ID: <code>{user['user_id']}</code>\n"
        f"Баланс: <b>{user['balance']:.2f} USDT</b>",
        kb,
    )


@router.callback_query(F.data == "dep:start")
async def dep_start(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        *[[InlineKeyboardButton(text=w["name"], callback_data=f"dep:crypto:{k}")]
          for k, w in WALLETS.items()],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="menu")],
    ])
    await safe_edit(cb.message, "💳 <b>Пополнение депозита</b>\n\nВыберите криптовалюту:", kb)


@router.callback_query(F.data.startswith("dep:crypto:"))
async def dep_choose(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split(":")[2]
    if key not in WALLETS:
        await cb.answer("Неизвестная валюта", show_alert=True)
        return
    w = WALLETS[key]
    await state.update_data(crypto=key)
    await state.set_state(DepositFSM.enter_amount)
    await safe_edit(
        cb.message,
        f"Выбрано: <b>{w['name']}</b>\n\n"
        f"Введите сумму в <b>{w['ticker']}</b> для пополнения:",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅ Отмена", callback_data="menu")]
        ]),
    )


@router.message(DepositFSM.enter_amount)
async def dep_amount(msg: Message, state: FSMContext):
    try:
        amount = float((msg.text or "").replace(",", ".").strip())
        if amount <= 0:
            raise ValueError
    except Exception:
        await msg.answer("❌ Введите корректное число.")
        return

    data = await state.get_data()
    key = data.get("crypto")
    if key not in WALLETS:
        await state.clear()
        return
    w = WALLETS[key]
    usdt_equiv = amount * w["rate"]
    now = datetime.utcnow()
    expires = now + timedelta(minutes=30)

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO deposits(user_id, crypto, address, amount, status, created_at, expires_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (msg.from_user.id, key, w["address"], usdt_equiv, "pending",
             now.isoformat(), expires.isoformat()),
        )
        dep_id = cur.lastrowid
        await db.commit()

    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"dep:paid:{dep_id}")],
        [InlineKeyboardButton(text="⬅ Назад", callback_data="menu")],
    ])
    await msg.answer(
        f"💳 <b>Депозит #{dep_id}</b>\n\n"
        f"Сеть: <b>{w['name']}</b>\n"
        f"Сумма: <code>{amount:.8f} {w['ticker']}</code>\n"
        f"≈ <b>{usdt_equiv:.2f} USDT</b>\n\n"
        f"Адрес для оплаты:\n<code>{w['address']}</code>\n\n"
        f"⏱ <b>На оплату 30 минут.</b> По истечении таймер отменит платёж.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("dep:paid:"))
async def dep_paid(cb: CallbackQuery):
    dep_id = int(cb.data.split(":")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, crypto, amount, status FROM deposits WHERE id=?", (dep_id,)
        )
        row = await cur.fetchone()
        if not row or row[0] != cb.from_user.id:
            await cb.answer("Заявка не найдена", show_alert=True)
            return
        if row[3] != "pending":
            await cb.answer(f"Статус: {row[3]}", show_alert=True)
            return
        await db.execute("UPDATE deposits SET status='review' WHERE id=?", (dep_id,))
        await db.commit()
        crypto, amount = row[1], row[2]

    await safe_edit(
        cb.message,
        f"⏳ Заявка #{dep_id} отправлена на проверку.\n"
        f"Менеджер подтвердит оплату в течение 5–15 минут.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🆘 Поддержка", url=f"https://t.me/{SUPPORT.lstrip('@')}")]
        ]),
    )

    for admin in ADMIN_IDS:
        try:
            await bot.send_message(
                admin,
                f"🔔 <b>Новый депозит #{dep_id}</b>\n"
                f"Юзер: <code>{cb.from_user.id}</code>\n"
                f"Сеть: {WALLETS[crypto]['name']}\n"
                f"Сумма: {amount:.2f} USDT\n\n"
                f"Подтвердить: <code>/confirm {dep_id}</code>"
            )
        except Exception:
            pass


@router.message(Command("confirm"))
async def admin_confirm(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    parts = (msg.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await msg.answer("Использование: <code>/confirm &lt;deposit_id&gt;</code>")
        return
    dep_id = int(parts[1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, amount, status FROM deposits WHERE id=?", (dep_id,)
        )
        row = await cur.fetchone()
        if not row or row[2] not in ("pending", "review"):
            await msg.answer("Не найдено или уже обработано")
            return
        user_id, amount, _ = row
        await db.execute("UPDATE deposits SET status='confirmed' WHERE id=?", (dep_id,))
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?",
                         (amount, user_id))
        await db.commit()

    await msg.answer(f"✅ Депозит #{dep_id} зачислен: {amount:.2f} USDT")
    try:
        u = await get_or_create_user(user_id)
        txt = f"✅ <b>Депозит #{dep_id} зачислен</b>\n+{amount:.2f} USDT\n\n" + menu_text(u)
        await bot.send_message(user_id, txt, reply_markup=main_kb(u))
    except Exception:
        pass


@router.callback_query(F.data == "deal:create")
async def deal_create(cb: CallbackQuery, state: FSMContext):
    user = await get_or_create_user(cb.from_user.id)
    if user["balance"] < MIN_UNLOCK:
        await cb.answer(f"Нужен депозит от {MIN_UNLOCK:.0f} USDT", show_alert=True)
        return
    await state.set_state(DealFSM.enter_amount)
    await safe_edit(
        cb.message,
        f"🔗 <b>Создание сделки</b>\n\n"
        f"Введите сумму сделки в USDT.\n"
        f"Доступно: <b>{user['balance']:.2f} USDT</b>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅ Отмена", callback_data="menu")]
        ]),
    )


@router.message(DealFSM.enter_amount)
async def deal_amount(msg: Message, state: FSMContext):
    try:
        amount = float((msg.text or "").replace(",", ".").strip())
        if amount <= 0:
            raise ValueError
    except Exception:
        await msg.answer("❌ Введите корректное число.")
        return

    user = await get_or_create_user(msg.from_user.id)
    if amount > user["balance"]:
        await msg.answer("❌ Недостаточно средств на балансе.")
        return

    code = gen_code(10)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?",
                         (amount, user["user_id"]))
        await db.execute(
            "INSERT INTO deals(code, creator_id, amount, status, created_at) VALUES(?,?,?,?,?)",
            (code, user["user_id"], amount, "pending", datetime.utcnow().isoformat()),
        )
        await db.commit()

    link = f"https://t.me/{BOT_USERNAME}?start=deal_{code}"
    await state.clear()
    await msg.answer(
        f"✅ <b>Сделка создана!</b>\n\n"
        f"Сумма: <b>{amount:.2f} USDT</b> (заблокирована)\n"
        f"Код сделки: <code>{code}</code>\n\n"
        f"📨 Отправьте партнёру ссылку:\n{link}\n\n"
        f"⏱ После принятия у сторон будет 60 минут на завершение.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 В меню", callback_data="menu")]
        ]),
    )


async def show_deal_invite(msg: Message, user: dict, code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, creator_id, amount, status FROM deals WHERE code=?", (code,)
        )
        row = await cur.fetchone()
    if not row:
        await msg.answer("❌ Сделка не найдена.")
        return
    _, creator_id, amount, status = row
    if status != "pending":
        await msg.answer(f"Сделка недоступна (статус: {status}).")
        return
    if creator_id == user["user_id"]:
        await msg.answer("Это ваша сделка. Отправьте ссылку партнёру.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять сделку", callback_data=f"deal:accept:{code}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"deal:decline:{code}")],
    ])
    await msg.answer(
        f"🤝 <b>Вам предложена сделка</b>\n\n"
        f"Сумма: <b>{amount:.2f} USDT</b>\n"
        f"Код: <code>{code}</code>\n\n"
        f"Средства продавца уже заблокированы в Gargona.\n"
        f"Примите сделку, чтобы продолжить.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("deal:accept:"))
async def deal_accept(cb: CallbackQuery):
    code = cb.data.split(":")[2]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, creator_id, amount, status FROM deals WHERE code=?", (code,)
        )
        row = await cur.fetchone()
        if not row:
            await cb.answer("Сделка не найдена", show_alert=True)
            return
        did, creator_id, amount, status = row
        if status != "pending":
            await cb.answer(f"Статус: {status}", show_alert=True)
            return
        if cb.from_user.id == creator_id:
            await cb.answer("Нельзя принять свою сделку", show_alert=True)
            return

        expires = datetime.utcnow() + timedelta(minutes=60)
        await db.execute(
            "UPDATE deals SET partner_id=?, status='active', expires_at=? WHERE id=?",
            (cb.from_user.id, expires.isoformat(), did),
        )
        await db.commit()

    await safe_edit(
        cb.message,
        f"✅ Сделка <code>{code}</code> активирована.\n⏱ На завершение 60 минут.",
        InlineKeyboardMarkup(inline_keyboard=[]),
    )

    kb_creator = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Я передал товар/услугу",
                              callback_data=f"deal:sent:{code}")]
    ])
    kb_partner = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я получил", callback_data=f"deal:received:{code}")],
        [InlineKeyboardButton(text="⚠️ Проблема",
                              url=f"https://t.me/{SUPPORT.lstrip('@')}")],
    ])

    try:
        await bot.send_message(
            creator_id,
            f"🤝 Партнёр принял сделку <code>{code}</code>!\n"
            f"⏱ 60 минут на завершение.",
            reply_markup=kb_creator,
        )
    except Exception:
        pass
    try:
        await bot.send_message(
            cb.from_user.id,
            f"Вы приняли сделку <code>{code}</code>.\n"
            f"Дождитесь передачи и нажмите «Я получил».",
            reply_markup=kb_partner,
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("deal:decline:"))
async def deal_decline(cb: CallbackQuery):
    code = cb.data.split(":")[2]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, creator_id, amount, status FROM deals WHERE code=?", (code,)
        )
        row = await cur.fetchone()
        if not row or row[3] != "pending":
            await cb.answer("Недоступно", show_alert=True)
            return
        did, creator_id, amount, _ = row
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?",
                         (amount, creator_id))
        await db.execute("UPDATE deals SET status='declined' WHERE id=?", (did,))
        await db.commit()

    await safe_edit(cb.message, "❌ Сделка отклонена.", InlineKeyboardMarkup(inline_keyboard=[]))
    try:
        await bot.send_message(
            creator_id,
            f"❌ Партнёр отклонил сделку <code>{code}</code>.\nСредства возвращены на баланс.",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("deal:sent:"))
async def deal_sent(cb: CallbackQuery):
    code = cb.data.split(":")[2]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT creator_id, partner_id, status FROM deals WHERE code=?", (code,)
        )
        row = await cur.fetchone()
        if not row or row[2] != "active":
            await cb.answer("Сделка недоступна", show_alert=True)
            return
        if cb.from_user.id != row[0]:
            await cb.answer("Только продавец", show_alert=True)
            return
        await db.execute("UPDATE deals SET creator_sent=1 WHERE code=?", (code,))
        await db.commit()

    await safe_edit(cb.message, "📦 Отмечено: товар/услуга передана.",
                    InlineKeyboardMarkup(inline_keyboard=[]))
    try:
        await bot.send_message(row[1], "📦 Продавец отметил передачу. Подтвердите получение.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("deal:received:"))
async def deal_received(cb: CallbackQuery):
    code = cb.data.split(":")[2]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, creator_id, partner_id, amount, status FROM deals WHERE code=?",
            (code,),
        )
        row = await cur.fetchone()
        if not row or row[4] != "active":
            await cb.answer("Недоступно", show_alert=True)
            return
        did, creator_id, partner_id, amount, _ = row
        if cb.from_user.id != partner_id:
            await cb.answer("Только покупатель", show_alert=True)
            return

        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?",
                         (amount, partner_id))
        await db.execute("UPDATE deals SET status='completed' WHERE id=?", (did,))
        await db.commit()

    await safe_edit(
        cb.message,
        f"✅ Сделка <code>{code}</code> завершена.\n"
        f"Средства зачислены на ваш баланс: <b>{amount:.2f} USDT</b>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 В меню", callback_data="menu")]
        ]),
    )
    try:
        await bot.send_message(
            creator_id,
            f"✅ Покупатель подтвердил получение.\n"
            f"Сделка <code>{code}</code> завершена.",
        )
    except Exception:
        pass


async def expiry_checker():
    while True:
        await asyncio.sleep(60)
        now = datetime.utcnow().isoformat()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT id, user_id FROM deposits "
                    "WHERE status='pending' AND expires_at < ?",
                    (now,),
                )
                expired_deps = await cur.fetchall()
                for dep_id, user_id in expired_deps:
                    await db.execute(
                        "UPDATE deposits SET status='rejected' WHERE id=?", (dep_id,)
                    )
                    await db.commit()
                    try:
                        await bot.send_message(
                            user_id,
                            f"❌ <b>Платёж #{dep_id} отклонён</b> — истекли 30 минут.\n"
                            f"Создайте новый депозит.",
                        )
                    except Exception:
                        pass

                cur = await db.execute(
                    "SELECT id, code, creator_id, partner_id, amount FROM deals "
                    "WHERE status='active' AND expires_at < ?",
                    (now,),
                )
                expired_deals = await cur.fetchall()
                for did, code, creator_id, partner_id, amount in expired_deals:
                    await db.execute(
                        "UPDATE users SET balance = balance + ? WHERE user_id=?",
                        (amount, creator_id),
                    )
                    await db.execute(
                        "UPDATE deals SET status='expired' WHERE id=?", (did,)
                    )
                    await db.commit()
                    for uid in (creator_id, partner_id):
                        if not uid:
                            continue
                        try:
                            await bot.send_message(
                                uid,
                                f"⏱ <b>Сделка <code>{code}</code> истекла.</b>\n"
                                f"Время (60 минут) вышло, средства возвращены продавцу.",
                            )
                        except Exception:
                            pass
        except Exception as e:
            logging.exception("expiry_checker error: %s", e)


@router.message()
async def catch_all(msg: Message, state: FSMContext):
    user = await get_or_create_user(msg.from_user.id)
    if user["balance"] < MIN_UNLOCK:
        await msg.answer(
            f"🔒 <b>Доступ закрыт</b>\n\n"
            f"Пополните депозит минимум на <b>{MIN_UNLOCK:.0f} USDT</b>, "
            f"чтобы открыть сделки.\n\n"
            f"🆘 Поддержка: {SUPPORT}",
            reply_markup=main_kb(user),
        )
    else:
        await msg.answer("Выберите действие:", reply_markup=main_kb(user))


async def main():
    await db_init()
    asyncio.create_task(expiry_checker())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
