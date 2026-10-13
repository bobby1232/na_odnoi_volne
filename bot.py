import asyncio, os, secrets
from collections import defaultdict
from pathlib import Path
from html import escape
from io import BytesIO
from aiogram.types import BufferedInputFile
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from questions import QUESTIONS, CRITICAL

TOKEN=os.getenv("BOT_TOKEN")
DB=os.getenv("DATABASE_PATH","/data/couples.db")
if not TOKEN: raise RuntimeError("Set BOT_TOKEN in Railway Variables")
bot=Bot(TOKEN); dp=Dispatcher()
def keyboard(pid,q,opts):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=x,callback_data=f"a:{pid}:{q}:{i}")] for i,x in enumerate(opts)])
async def init():
    Path(DB).parent.mkdir(parents=True,exist_ok=True)
    async with aiosqlite.connect(DB) as c:
        await c.executescript("CREATE TABLE IF NOT EXISTS pairs(id TEXT PRIMARY KEY,first_id INTEGER NOT NULL,second_id INTEGER); CREATE TABLE IF NOT EXISTS answers(pair_id TEXT,user_id INTEGER,q INTEGER,a INTEGER,PRIMARY KEY(pair_id,user_id,q));")
        await c.commit()
async def pair(uid):
    async with aiosqlite.connect(DB) as c:
        async with c.execute("SELECT id,first_id,second_id FROM pairs WHERE first_id=? OR second_id=?",(uid,uid)) as cur: return await cur.fetchone()
async def progress(pid,uid):
    async with aiosqlite.connect(DB) as c:
        async with c.execute("SELECT q,a FROM answers WHERE pair_id=? AND user_id=?",(pid,uid)) as cur: return dict(await cur.fetchall())
async def ask(uid,pid):
    done=await progress(pid,uid)
    q=next((i for i in range(len(QUESTIONS)) if i not in done),None)
    if q is None:
        await bot.send_message(uid,"✅ Вы завершили опрос. Ответы будут сопоставлены после прохождения партнёром. /status")
        await report(pid); return
    cat,title,opts=QUESTIONS[q]
    await bot.send_message(uid,f"Вопрос {q+1}/{len(QUESTIONS)} · {cat}\n\n{title}",reply_markup=keyboard(pid,q,opts))
async def completed_pair(pid):
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT first_id,second_id FROM pairs WHERE id=?",(pid,)) as cur:
            p=await cur.fetchone()
    if not p or not p[1]: return None
    a,b=await asyncio.gather(progress(pid,p[0]),progress(pid,p[1]))
    if len(a)!=len(QUESTIONS) or len(b)!=len(QUESTIONS): return None
    return p,a,b

def report_keyboard(pid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Полный отчёт",callback_data=f"full:{pid}")],
        [InlineKeyboardButton(text="❌ Только расхождения",callback_data=f"diff:{pid}")]
    ])

def full_report_text(a,b,only_diff=False):
    lines=["📋 ПОЛНЫЙ ОТЧЁТ ПАРЫ","Партнёр 1 / Партнёр 2",""]
    current=None
    for i,(cat,title,opts) in enumerate(QUESTIONS):
        if only_diff and a[i]==b[i]: continue
        if cat!=current:
            current=cat; lines.append(f"\n📌 {cat}")
        mark="✅" if a[i]==b[i] else "❌"
        lines.append(f"{mark} {i+1}. {title}")
        lines.append("  1: "+opts[a[i]])
        lines.append("  2: "+opts[b[i]])
        lines.append("  Все варианты: "+ " | ".join(opts))
        lines.append("")
    if only_diff and all(a[i]==b[i] for i in a): lines.append("Все ответы совпали.")
    return "\n".join(lines)

async def send_full_report(uid,pid,only_diff=False):
    result=await completed_pair(pid)
    if not result:
        await bot.send_message(uid,"Отчёт станет доступен после завершения опроса обоими партнёрами.")
        return
    _,a,b=result
    content=full_report_text(a,b,only_diff)
    title="Только расхождения" if only_diff else "Полный отчёт"
    # Send a UTF-8 attachment to avoid Telegram's 4096-character message limit.
    file=BufferedInputFile(content.encode("utf-8"),filename="rasxozhdeniya.txt" if only_diff else "polny_otchet.txt")
    await bot.send_document(uid,file,caption=f"📋 {title}: ответы обоих партнёров, все варианты и отметки ✅/❌.",reply_markup=report_keyboard(pid))

@dp.callback_query(F.data.startswith(("full:","diff:")))
async def detailed_callback(c):
    mode,pid=c.data.split(":",1)
    p=await pair(c.from_user.id)
    if not p or p[0]!=pid:
        await c.answer("Нет доступа к отчёту",show_alert=True); return
    await c.answer("Формирую отчёт")
    await send_full_report(c.from_user.id,pid,mode=="diff")

@dp.message(Command("fullreport"))
async def fullreport_command(m):
    p=await pair(m.from_user.id)
    if not p:
        await m.answer("Начните с /start"); return
    await send_full_report(m.from_user.id,p[0])

async def report(pid):
    async with aiosqlite.connect(DB) as c:
        async with c.execute("SELECT first_id,second_id FROM pairs WHERE id=?",(pid,)) as cur: p=await cur.fetchone()
    if not p or not p[1]: return
    a,b=await asyncio.gather(progress(pid,p[0]),progress(pid,p[1]))
    if len(a)!=len(QUESTIONS) or len(b)!=len(QUESTIONS): return
    groups=defaultdict(list); critical=[]
    for i,(cat,title,opts) in enumerate(QUESTIONS):
        match=a[i]==b[i]
        groups[cat].append(match)
        if not match and i in CRITICAL: critical.append(f"• {title}: {opts[a[i]]} / {opts[b[i]]}")
    score=round(100*sum(a[i]==b[i] for i in a)/len(QUESTIONS))
    lines=[f"💞 Совпадение ответов: {score}%","Это не научная оценка совместимости, а доля одинаковых вариантов.","", "По направлениям:"]
    lines += [f"• {cat}: {round(100*sum(v)/len(v))}%" for cat,v in groups.items()]
    lines += ["","⚠️ Ключевые расхождения:"]+(critical or ["По критическим вопросам ответы совпали."])
    lines += ["","Обсудите различия без давления. Не все варианты равноудалены по смыслу."]
    for uid in p:
        try: await bot.send_message(uid,"\n".join(lines))
        except Exception: pass
@dp.message(CommandStart())
async def start(m):
    uid=m.from_user.id
    p=await pair(uid)
    if p: await m.answer("Вы уже участвуете в паре. /continue /status"); return
    parts=(m.text or "").split(maxsplit=1)
    if len(parts)>1 and parts[1].startswith("pair_"):
        pid=parts[1][5:]
        async with aiosqlite.connect(DB) as c:
            async with c.execute("SELECT first_id,second_id FROM pairs WHERE id=?",(pid,)) as cur: found=await cur.fetchone()
            if not found or found[1] is not None or found[0]==uid:
                await m.answer("Ссылка недействительна или пара уже заполнена."); return
            await c.execute("UPDATE pairs SET second_id=? WHERE id=? AND second_id IS NULL",(uid,pid)); await c.commit()
        await m.answer("Вы присоединились к паре! Ответы скрыты до завершения обоими.")
        await ask(uid,pid)
        try: await bot.send_message(found[0],"❤️ Партнёр присоединился к опросу.")
        except Exception: pass
        return
    pid=secrets.token_urlsafe(12)
    async with aiosqlite.connect(DB) as c:
        await c.execute("INSERT INTO pairs(id,first_id) VALUES(?,?)",(pid,uid)); await c.commit()
    me=await bot.get_me()
    await m.answer(f"💞 На одной волне\n\nСначала ответьте на вопросы. Затем отправьте партнёру ссылку:\nhttps://t.me/{me.username}?start=pair_{pid}\n\n/continue — продолжить; /status — статус")
    await ask(uid,pid)
@dp.message(Command("continue"))
async def cont(m):
    p=await pair(m.from_user.id)
    if not p: await m.answer("Начните с /start"); return
    await ask(m.from_user.id,p[0])
@dp.message(Command("status"))
async def status(m):
    p=await pair(m.from_user.id)
    if not p: await m.answer("Начните с /start"); return
    a=await progress(p[0],p[1]); b=await progress(p[0],p[2]) if p[2] else {}
    me=await bot.get_me()
    await m.answer(f"Ваш прогресс: {len(a)}/{len(QUESTIONS)}\nПартнёр: {len(b)}/{len(QUESTIONS)}\nСсылка: https://t.me/{me.username}?start=pair_{p[0]}")
    if len(a)==len(b)==len(QUESTIONS): await report(p[0])
@dp.callback_query(F.data.startswith("a:"))
async def answer(c):
    try:
        _,pid,qs,ans=c.data.split(":"); q=int(qs); ans=int(ans)
        p=await pair(c.from_user.id)
        if not p or p[0]!=pid or not (0<=q<len(QUESTIONS)) or not (0<=ans<len(QUESTIONS[q][2])): await c.answer("Некорректный ответ"); return
        done=await progress(pid,c.from_user.id)
        if q in done: await c.answer("Ответ уже сохранён"); return
        if q!=len(done): await c.answer("Ответьте на текущий вопрос"); return
        async with aiosqlite.connect(DB) as db:
            await db.execute("INSERT OR IGNORE INTO answers VALUES(?,?,?,?)",(pid,c.from_user.id,q,ans)); await db.commit()
        await c.answer("Сохранено")
        await c.message.edit_reply_markup(reply_markup=None)
        await ask(c.from_user.id,pid)
    except Exception:
        await c.answer("Ошибка обработки ответа")
async def main():
    await init()
    await dp.start_polling(bot)
if __name__=="__main__": asyncio.run(main())
