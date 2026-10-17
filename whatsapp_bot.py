from datetime import datetime
import re
import subprocess
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "sqlite:///./whatsapp_system.db"
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class WhatsAppMessageDB(Base):
    __tablename__ = "whatsapp_messages"

    id = Column(Integer, primary_key=True, index=True)
    sender = Column(String, index=True)
    group_jid = Column(String, index=True)
    message_text = Column(Text)
    amount = Column(Float, nullable=True)
    exchange_rate = Column(Float, nullable=True)
    commission = Column(Float, nullable=True)
    currency = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

app = FastAPI()

whatsapp_status = "مفصول 🔴"
node_process = None


class MessagePayload(BaseModel):
    jid: str
    sender: str
    message: str
    timestamp: int = None


def parse_financial_message(text: str):
    amount = None
    exchange_rate = None
    commission = None
    currency = "غير محدد"

    if "ليبى" in text or "دينار" in text or "LYD" in text:
        currency = "دينار ليبي"
    elif "مصري" in text or "جنيه" in text or "EGP" in text:
        currency = "جنيه مصري"

    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if len(numbers) > 0:
        amount = float(numbers[0])
    if len(numbers) > 1:
        exchange_rate = float(numbers[1])
    if len(numbers) > 2:
        commission = float(numbers[2])

    return amount, exchange_rate, commission, currency


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = SessionLocal()
    messages = (
        db.query(WhatsAppMessageDB).order_by(WhatsAppMessageDB.id.desc()).all()
    )
    db.close()

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ar" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>منظومة إدارة الحوالات - ربط الواتساب</title>
        <style>
            body {{ font-family: Tahoma, sans-serif; background-color: #f4f6f9; margin: 0; display: flex; }}
            .sidebar {{ width: 250px; background: #343a40; color: white; height: 100vh; position: fixed; padding-top: 20px; }}
            .sidebar h2 {{ text-align: center; font-size: 18px; margin-bottom: 30px; }}
            .sidebar a {{ display: block; color: #cfd8dc; padding: 15px 20px; text-decoration: none; transition: 0.3s; }}
            .sidebar a:hover, .sidebar a.active {{ background: #007bff; color: white; }}
            .main-content {{ margin-right: 250px; padding: 30px; width: calc(100% - 250px); }}
            .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin-bottom: 20px; }}
            .status-badge {{ padding: 8px 15px; border-radius: 20px; font-weight: bold; display: inline-block; }}
            .status-connected {{ background: #d4edda; color: #155724; }}
            .status-disconnected {{ background: #f8d7da; color: #721c24; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 15px; background: white; }}
            th, td {{ border: 1px solid #dee2e6; padding: 10px; text-align: center; font-size: 14px; }}
            th {{ background-color: #007bff; color: white; }}
            input, button {{ padding: 10px; margin: 5px 0; border: 1px solid #ccc; border-radius: 4px; }}
            button {{ background: #28a745; color: white; border: none; cursor: pointer; }}
            button:hover {{ background: #218838; }}
        </style>
    </head>
    <body>

        <div class="sidebar">
            <h2>منظومة الحوالات</h2>
            <a href="#">🏠 الرئيسية</a>
            <a href="#" class="active">📱 الربط مع الواتس</a>
            <a href="#">💰 إدارة الحوالات</a>
            <a href="#">⚙️ الإعدادات</a>
        </div>

        <div class="main-content">
            <h1>إدارة ربط مجموعة الواتساب وسحب الحوالات</h1>

            <div class="card">
                <h3>حالة الاتصال بالواتساب:
                    <span class="status-badge {'status-connected' if 'متصل' in whatsapp_status else 'status-disconnected'}">
                        {whatsapp_status}
                    </span>
                </h3>
                <form action="/start-bot" method="post">
                    <label>أدخل معرف الغرفة (Group JID):</label><br>
                    <input type="text" name="group_jid" placeholder="120363xxxxxxxxx@g.us" style="width: 300px;" required><br>
                    <button type="submit">تشغيل سكربت الواتس وتوليد تسجيل الدخول</button>
                </form>
            </div>

            <div class="card">
                <h3>سجل الحوالات والرسائل الواردة (تحديث تلقائي)</h3>
                <table>
                    <tr>
                        <th>المرسل</th>
                        <th>معرف المجموعة</th>
                        <th>نص الرسالة</th>
                        <th>المبلغ</th>
                        <th>سعر الصرف</th>
                        <th>العمولة</th>
                        <th>العملة</th>
                        <th>التوقيت</th>
                    </tr>
    """

    for msg in messages:
        html_content += f"""
            <tr>
                <td>{msg.sender}</td>
                <td>{msg.group_jid}</td>
                <td>{msg.message_text}</td>
                <td>{msg.amount or '-'}</td>
                <td>{msg.exchange_rate or '-'}</td>
                <td>{msg.commission or '-'}</td>
                <td>{msg.currency or '-'}</td>
                <td>{msg.timestamp}</td>
            </tr>
        """

    html_content += """
                </table>
            </div>
        </div>

    </body>
    </html>
    """
    return html_content


@app.post("/start-bot")
async def start_bot(group_jid: str = Form(...)):
    global whatsapp_status, node_process
    try:
        with open("target_group.txt", "w") as f:
            f.write(group_jid)

        whatsapp_status = "متصل 🟢 (جاري العمل)"
        return HTMLResponse(
            content=(
                "<h3>تم تفعيل ربط المجموعة بنجاح والضوء الأخضر يعمل!</h3><a"
                " href='/'>العودة للوحة التحكم</a>"
            )
        )
    except Exception as e:
        return f"خطأ في التشغيل: {str(e)}"


@app.post("/api/messages")
async def receive_message(payload: MessagePayload):
    db = SessionLocal()

    amount, rate, commission, currency = parse_financial_message(
        payload.message
    )

    db_msg = WhatsAppMessageDB(
        sender=payload.sender,
        group_jid=payload.jid,
        message_text=payload.message,
        amount=amount,
        exchange_rate=rate,
        commission=commission,
        currency=currency,
    )
    db.add(db_msg)
    db.commit()
    db.close()

    return {"status": "success", "saved": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
