import json
import os
import csv
import re
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from django.db import models
from django.db.models import Sum, Count
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from functools import wraps
from .models import Database, Capital, Expense, T1Summary, ClientBalance, FromSource, TransferType, OfficeName, InternalTransfer, DeliveryArea, SystemUser, ImportAlert, BalanceType, CurrencyCapital, OrderType, Message, SystemSetting, Dbcash, WhatsAppMessage, WhatsAppQRState
from .forms import DatabaseForm, CapitalForm, CapitalDepositForm, ExpenseForm, InternalTransferForm
from .whatsapp_parser import parse_whatsapp_text
import openpyxl

BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")

WA_TZ = ZoneInfo("Africa/Tripoli")


def _bot_dir():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "whatsapp-bot")


def _wa_ts_to_naive(ts):
    try:
        t = float(ts)
        if t > 1e12:
            t = t / 1000.0
        if t <= 0:
            raise ValueError
        return datetime.fromtimestamp(t, WA_TZ).replace(tzinfo=None)
    except Exception:
        return timezone.now()


def _naive_to_unix(dt):
    try:
        return int(dt.replace(tzinfo=WA_TZ).timestamp())
    except Exception:
        return 0


def _as_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _normalize_group_jid(jid):
    jid = (jid or "").strip()
    if not jid:
        return ""
    if "@g.us" in jid:
        return jid.split("@g.us")[0] + "@g.us"
    if jid.endswith("@id") or jid.endswith("@lid"):
        return jid.rsplit("@", 1)[0] + "@g.us"
    return jid


def _wa_is_duplicate(wamsg_id, sender, jid, text):
    if wamsg_id:
        return WhatsAppMessage.objects.filter(wamsg_id=wamsg_id).exists()
    return WhatsAppMessage.objects.filter(
        wamsg_id="", sender=sender, group_jid=jid, message_text=text
    ).exists()


def _wa_rate_timeline():
    """أحداث تغيير السعر في غرفة الزاجل: رسائل بوستر (بلا مبلغ) حملت سعراً — أي إعلان يسري على كل الحوالات."""
    return list(
        WhatsAppMessage.objects.filter(
            group_jid=WA_ZAJEL_GROUP,
            amount__isnull=True,
            exchange_rate__isnull=False,
            exchange_rate__gte=1,
        ).order_by("timestamp", "id").values_list("timestamp", "exchange_rate")
    )


def _wa_rate_at(ts, timeline=None):
    """آخر سعر معلن قبل/عند لحظة ts — يُطبّق على كل الحوالات حتى يتغيّر السعر.
    وإن كانت قبل أول إعلان في يومها فتأخذ أول إعلان ذلك اليوم."""
    if ts is None:
        return None
    if timeline is None:
        timeline = _wa_rate_timeline()
    best = None
    for ets, rate in timeline:
        if ets <= ts:
            best = rate
        else:
            break
    if best is not None:
        return best
    day = ts.date() if hasattr(ts, "date") else ts
    for ets, rate in timeline:
        if ets.date() == day:
            return rate
    return None


def _write_sync_request(payload):
    path = os.path.join(_bot_dir(), "sync_request")
    stale = os.path.join(_bot_dir(), "sync_result")
    if os.path.exists(stale):
        try:
            os.remove(stale)
        except OSError:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def _consume_sync_result():
    path = os.path.join(_bot_dir(), "sync_result")
    if not os.path.exists(path):
        return None
    data = None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        pass
    try:
        os.remove(path)
    except Exception:
        pass
    return data


def _sync_result_message(result):
    """يحوّل نتيجة السحب إلى (مستوى, نص) لعرضها كرسالة أو JSON."""
    if result.get("ok"):
        if str(result.get("mode", "")).startswith("history"):
            return "success", (
                f"وصلت مزامنة تاريخ واتساب: وُجد {result.get('found', 0)} | "
                f"جديد {result.get('saved', 0)} | مكرر {result.get('duplicates', 0)}"
            )
        if result.get("mode") == "catchup":
            return "success", (
                result.get("note")
                or "تمت إعادة اتصال الواتساب — الرسائل الفائتة تصل تلقائيًا خلال ثوانٍ"
            )
        if result.get("mode") == "gapfill":
            if not result.get("found"):
                return "success", "السحب العكسي الذكي: لا توجد رسائل أحدث غير محفوظة — سجلك محدّث"
            return "success", (
                f"السحب العكسي الذكي اكتمل: وُجد {result.get('found', 0)} | "
                f"جديد {result.get('saved', 0)} | مكرر {result.get('duplicates', 0)} — "
                "توقف عند أول رسالة محفوظة مسبقاً"
            )
        return "success", (
            f"اكتمل السحب: وُجد {result.get('found', 0)} | جديد {result.get('saved', 0)} | "
            f"مكرر {result.get('duplicates', 0)} | جولات {result.get('rounds', 0)}"
        )
    err = result.get("error", "خطأ غير معروف")
    if any(k in err for k in ("لا يوجد رد", "انتهت مهلة", "لم يرد")):
        err += " | جرّب تفعيل مزامنة التاريخ الكامل ثم أعد ربط الواتساب"
    return "error", f"فشل السحب: {err}"


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _backup_transactions(label="auto"):
    _ensure_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"transactions_{label}_{timestamp}.csv"
    filepath = os.path.join(BACKUP_DIR, filename)
    qs = Database.objects.all().order_by("id")
    if not qs.exists():
        return None
    field_names = [f.name for f in Database._meta.get_fields() if hasattr(f, 'column')]
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(field_names)
        for obj in qs:
            writer.writerow([getattr(obj, fn, '') for fn in field_names])
    return filename


def _backup_capital(label="auto"):
    _ensure_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"capital_{label}_{timestamp}.csv"
    filepath = os.path.join(BACKUP_DIR, filename)
    qs = Capital.objects.all().order_by("id")
    if not qs.exists():
        return None
    field_names = [f.name for f in Capital._meta.get_fields() if hasattr(f, 'column')]
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(field_names)
        for obj in qs:
            writer.writerow([getattr(obj, fn, '') for fn in field_names])
    return filename


def _backup_all(label="manual"):
    _ensure_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"full_backup_{label}_{timestamp}.csv"
    filepath = os.path.join(BACKUP_DIR, filename)

    all_models = [
        ("Database", Database),
        ("Capital", Capital),
        ("ClientBalance", ClientBalance),
        ("Expense", Expense),
        ("InternalTransfer", InternalTransfer),
        ("BalanceType", BalanceType),
        ("CurrencyCapital", CurrencyCapital),
        ("OfficeName", OfficeName),
        ("OrderType", OrderType),
        ("TransferType", TransferType),
        ("FromSource", FromSource),
        ("DeliveryArea", DeliveryArea),
        ("SystemUser", SystemUser),
        ("Message", Message),
        ("ImportAlert", ImportAlert),
        ("SystemSetting", SystemSetting),
        ("T1Summary", T1Summary),
        ("Dbcash", Dbcash),
    ]

    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(["TABLE", "ID", "DATA"])
        for model_name, model in all_models:
            for obj in model.objects.all():
                data = {}
                for field in model._meta.get_fields():
                    if hasattr(field, 'column'):
                        val = getattr(obj, field.name, '')
                        if hasattr(val, 'isoformat'):
                            val = val.isoformat()
                        elif hasattr(val, 'pk'):
                            val = val.pk if val else ''
                        data[field.name] = val
                writer.writerow([model_name, obj.pk, json.dumps(data, ensure_ascii=False)])
    return filename


def _restore_transactions(filepath):
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            data = {}
            for k, v in row.items():
                if k == 'id':
                    continue
                if k in ('date', 'time'):
                    try:
                        data[k] = datetime.strptime(v, "%Y-%m-%d").date() if k == 'date' else datetime.strptime(v, "%H:%M:%S").time() if v else None
                    except (ValueError, TypeError):
                        data[k] = None
                elif k in ('transfer_amount', 'transfered_amount', 'exchange_rate'):
                    try:
                        data[k] = float(v) if v else 0
                    except ValueError:
                        data[k] = 0
                else:
                    data[k] = v if v else ''
            try:
                Database.objects.create(**data)
                count += 1
            except Exception:
                pass
        return count


def _restore_capital(filepath):
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            data = {}
            for k, v in row.items():
                if k == 'id':
                    continue
                if k in ('date', 'time'):
                    try:
                        data[k] = datetime.strptime(v, "%Y-%m-%d").date() if k == 'date' else datetime.strptime(v, "%H:%M:%S").time() if v else None
                    except (ValueError, TypeError):
                        data[k] = None
                elif k in ('cash_in', 'cash_out', 'libyan_cash', 'libyan_withdraw', 'exchange_rate'):
                    try:
                        data[k] = float(v) if v else 0
                    except ValueError:
                        data[k] = 0
                else:
                    data[k] = v if v else ''
            try:
                Capital.objects.create(**data)
                count += 1
            except Exception:
                pass
        return count


def _restore_all(filepath):
    model_map = {
        "Database": Database,
        "Capital": Capital,
        "ClientBalance": ClientBalance,
        "Expense": Expense,
        "InternalTransfer": InternalTransfer,
        "BalanceType": BalanceType,
        "CurrencyCapital": CurrencyCapital,
        "OfficeName": OfficeName,
        "OrderType": OrderType,
        "TransferType": TransferType,
        "FromSource": FromSource,
        "DeliveryArea": DeliveryArea,
        "SystemUser": SystemUser,
        "Message": Message,
        "ImportAlert": ImportAlert,
        "SystemSetting": SystemSetting,
        "T1Summary": T1Summary,
        "Dbcash": Dbcash,
    }

    float_fields_map = {
        "Database": ('transfer_amount', 'transfered_amount', 'exchange_rate'),
        "Capital": ('cash_in', 'cash_out', 'libyan_cash', 'libyan_withdraw', 'exchange_rate'),
        "ClientBalance": ('egp_balance', 'lyd_balance'),
        "Expense": ('amount',),
        "InternalTransfer": ('amount',),
        "T1Summary": ('amount',),
        "Dbcash": ('amount',),
    }

    date_fields = ('date', 'time', 'last_updated', 'created_at')

    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader, None)
        count = 0
        for row in reader:
            if len(row) < 3:
                continue
            table_name = row[0]
            model = model_map.get(table_name)
            if not model:
                continue
            try:
                data = json.loads(row[2])
            except Exception:
                continue
            for k in list(data.keys()):
                if k == 'id':
                    del data[k]
                    continue
                v = data[k]
                if k in date_fields:
                    if v and isinstance(v, str):
                        try:
                            data[k] = datetime.fromisoformat(v).date() if 'date' in k else datetime.fromisoformat(v).time() if k == 'time' else datetime.fromisoformat(v)
                        except (ValueError, TypeError):
                            data[k] = None
                    else:
                        data[k] = None
                elif k in float_fields_map.get(table_name, ()):
                    try:
                        data[k] = float(v) if v else 0
                    except (ValueError, TypeError):
                        data[k] = 0
                elif k.endswith('_id'):
                    try:
                        data[k] = int(v) if v else None
                    except (ValueError, TypeError):
                        data[k] = None
            try:
                model.objects.create(**data)
                count += 1
            except Exception:
                pass
        return count


def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = SystemUser.objects.filter(username=username).first()
        if user and user.check_password(password):
            if not user.is_active:
                messages.error(request, "تم تعطيل هذا الحساب. تواصل مع المدير.")
                return render(request, "login.html")
            request.session["user_id"] = user.id
            request.session["username"] = user.username
            request.session["is_admin"] = user.is_admin
            return redirect("welcome")
        messages.error(request, "اسم المستخدم أو كلمة المرور غير صحيحة")
    return render(request, "login.html")


def welcome_view(request):
    if "user_id" not in request.session:
        return redirect("login")
    return render(request, "welcome.html", {
        "username": request.session.get("username", ""),
        "is_admin": request.session.get("is_admin", False),
    })


def logout_view(request):
    request.session.flush()
    return redirect("login")


def login_required_custom(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if "user_id" not in request.session:
            return redirect("login")
        return view_func(request, *args, **kwargs)
    return wrapper


def get_current_user(request):
    user_id = request.session.get("user_id")
    if user_id:
        return SystemUser.objects.filter(id=user_id).first()
    return None


def has_perm(request, perm_name):
    user = get_current_user(request)
    if user:
        return user.has_perm(perm_name)
    return False


def _normalize_arabic(s):
    if not s:
        return ""
    replacements = {'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ة': 'ه', 'ى': 'ي', 'ؤ': 'و', 'ئ': 'ي'}
    for old, new in replacements.items():
        s = s.replace(old, new)
    return s.strip()

def _get_clients_with_remaining():
    clients_qs = ClientBalance.objects.all().order_by("name")
    all_transfers = list(Database.objects.values_list("sender_name", "transfered_amount"))
    transfer_map = {}
    for sn, amt in all_transfers:
        norm = _normalize_arabic(sn or "")
        transfer_map[norm] = transfer_map.get(norm, 0) + (amt or 0)

    clients = []
    total_egp = 0
    for c in clients_qs:
        deposited = Capital.objects.filter(client=c).exclude(in_type="ترحيل").aggregate(total=Sum("cash_in"))["total"] or 0
        norm_name = _normalize_arabic(c.name)
        transferred = transfer_map.get(norm_name, 0)
        remaining = deposited - transferred
        c.remaining_egp = remaining
        total_egp += remaining
        clients.append(c)
    return clients, total_egp


@login_required_custom
def home(request):
    db_count = Database.objects.count()
    cap_count = Capital.objects.count()
    exp_count = Expense.objects.count()
    client_count = ClientBalance.objects.count()

    agg = Database.objects.aggregate(
        total_lyd=Sum("transfer_amount"),
        total_egp=Sum("transfered_amount"),
    )
    db_total_lyd = agg["total_lyd"] or 0
    db_total_egp = agg["total_egp"] or 0
    avg_rate = round(db_total_egp / db_total_lyd, 3) if db_total_lyd else 0

    cap_agg = Capital.objects.aggregate(
        total_egp=Sum("cash_in"),
        total_lyd=Sum("libyan_cash"),
    )
    cap_total_egp = cap_agg["total_egp"] or 0
    cap_total_lyd = cap_agg["total_lyd"] or 0
    cap_avg_rate = round(cap_total_egp / cap_total_lyd, 3) if cap_total_lyd else 0

    exp_agg = Expense.objects.aggregate(total=Sum("amount"))
    exp_total = exp_agg["total"] or 0

    remaining_egp = cap_total_egp - db_total_egp

    clients, total_egp_clients = _get_clients_with_remaining()

    latest = list(Database.objects.order_by("-date")[:10])
    latest_capital = list(Capital.objects.select_related("client", "from_source").order_by("-date")[:10])

    return render(request, "home.html", {
        "db_count": db_count,
        "cap_count": cap_count,
        "exp_count": exp_count,
        "client_count": client_count,
        "db_total_lyd": db_total_lyd,
        "db_total_egp": db_total_egp,
        "avg_rate": avg_rate,
        "cap_total_egp": cap_total_egp,
        "cap_total_lyd": cap_total_lyd,
        "cap_avg_rate": cap_avg_rate,
        "remaining_egp": remaining_egp,
        "latest": latest,
        "latest_capital": latest_capital,
        "clients": clients,
        "total_egp_clients": total_egp_clients,
        "exp_total": exp_total,
        "today": datetime.now(),
    })


def transactions(request):
    order = request.GET.get("order", "desc")
    order_field = "-id" if order == "desc" else "id"
    qs = Database.objects.select_related("from_source", "office_name", "order_type", "transfer_type").all().order_by(order_field)
    q = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    year = request.GET.get("year", "").strip()
    month_from = request.GET.get("month_from", "").strip()
    month_to = request.GET.get("month_to", "").strip()

    if q:
        qs = qs.filter(
            models.Q(sender_name__icontains=q) |
            models.Q(receiver_name__icontains=q) |
            models.Q(order_number__icontains=q) |
            models.Q(receiver_tele__icontains=q) |
            models.Q(sender_tele__icontains=q) |
            models.Q(transfer_amount__icontains=q) |
            models.Q(exchange_rate__icontains=q) |
            models.Q(remarks__icontains=q)
        )
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if year:
        qs = qs.filter(date__year=year)
    if month_from:
        qs = qs.filter(date__month__gte=month_from)
    if month_to:
        qs = qs.filter(date__month__lte=month_to)

    amount_filter = request.GET.get("amount", "").strip()
    if amount_filter:
        try:
            amount_val = float(amount_filter.replace(",", ""))
            qs = qs.filter(transfered_amount=amount_val)
        except ValueError:
            pass

    agg = qs.aggregate(
        total_egp=Sum("transfered_amount"),
        total_lyd=Sum("transfer_amount"),
    )

    total_egp_val = agg["total_egp"] or 0
    total_lyd_val = agg["total_lyd"] or 0
    avg_rate = round(total_egp_val / total_lyd_val, 3) if total_lyd_val else 0
    filtered_count = qs.count()
    total_count = Database.objects.count()

    clients, total_egp_clients = _get_clients_with_remaining()

    is_filtered = bool(q or date_from or date_to or year or month_from or month_to or amount_filter)

    return render(request, "transactions_list.html", {
        "title": "الحوالات",
        "transactions": qs,
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "selected_year": year,
        "selected_month_from": month_from,
        "selected_month_to": month_to,
        "total_egp": total_egp_val,
        "total_lyd": total_lyd_val,
        "avg_rate": avg_rate,
        "clients": clients,
        "total_egp_clients": total_egp_clients,
        "order": order,
        "filtered_count": filtered_count,
        "total_count": total_count,
        "is_filtered": is_filtered,
        "amount_filter": amount_filter,
    })


def _reverse_transaction_balance(obj):
    if "أحمد ياسين" in (obj.sender_name or ""):
        egp = obj.transfered_amount or 0
        lyd = obj.transfer_amount or 0
        if egp > 0:
            ahmed = ClientBalance.objects.filter(name__contains="أحمد ياسين").first()
            if ahmed:
                ahmed.egp_balance += egp
                ahmed.lyd_balance -= lyd
                ahmed.save()


def _reverse_capital_balance(obj):
    client = obj.client
    egp = obj.cash_in or 0
    lyd = obj.libyan_cash or 0
    if client and egp > 0:
        client.egp_balance -= egp
        client.lyd_balance -= lyd
        client.save()


def delete_transaction(request, id):
    obj = get_object_or_404(Database, id=id)
    _reverse_transaction_balance(obj)
    obj.delete()
    messages.success(request, f"✅ تم حذف العملية #{id} واستعادة الرصيد.")
    return redirect("transactions")


def delete_capital(request, id):
    obj = get_object_or_404(Capital, id=id)
    _reverse_capital_balance(obj)
    obj.delete()
    name = obj.client.name if obj.client else "-"
    messages.success(request, f"✅ تم حذف رصيد #{id} من {name} وتحديث الرصيد.")
    return redirect("capital_list")


def bulk_delete_transactions(request):
    if request.method == "POST":
        ids = request.POST.getlist("ids")
        count = 0
        for obj in Database.objects.filter(id__in=ids):
            _reverse_transaction_balance(obj)
            obj.delete()
            count += 1
        messages.success(request, f"✅ تم حذف {count} عملية واستعادة الأرصدة.")
    return redirect("transactions")


def bulk_delete_capital(request):
    if request.method == "POST":
        ids = request.POST.getlist("ids")
        count = 0
        for obj in Capital.objects.filter(id__in=ids):
            _reverse_capital_balance(obj)
            obj.delete()
            count += 1
        messages.success(request, f"✅ تم حذف {count} رصيد وتحديث الأرصدة.")
    return redirect("capital_list")


def clear_all_transactions(request):
    from django.db import transaction
    backup_file = _backup_transactions("before_clear")
    with transaction.atomic():
        ids = list(Database.objects.values_list("id", flat=True))
        for obj in Database.objects.filter(id__in=ids):
            _reverse_transaction_balance(obj)
        count = len(ids)
        Database.objects.filter(id__in=ids).delete()
    msg = f"✅ تم مسح {count} حوالة بالكامل واستعادة جميع الأرصدة."
    if backup_file:
        msg += f" (النسخة الاحتياطية: {backup_file})"
    messages.success(request, msg)
    return redirect("transactions")


def clear_all_capital(request):
    from django.db import transaction
    backup_file = _backup_capital("before_clear")
    with transaction.atomic():
        ids = list(Capital.objects.values_list("id", flat=True))
        for obj in Capital.objects.filter(id__in=ids):
            _reverse_capital_balance(obj)
        count = len(ids)
        Capital.objects.filter(id__in=ids).delete()
    msg = f"✅ تم مسح {count} رصيد بالكامل وتحديث الأرصدة."
    if backup_file:
        msg += f" (النسخة الاحتياطية: {backup_file})"
    messages.success(request, msg)
    return redirect("capital_list")


def capital_list(request):
    order = request.GET.get("order", "desc")
    order_field = "-id" if order == "desc" else "id"
    qs = Capital.objects.select_related("client", "from_source").all().order_by(order_field)
    q = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    client_id = request.GET.get("client", "").strip()
    year = request.GET.get("year", "").strip()
    month_from = request.GET.get("month_from", "").strip()
    month_to = request.GET.get("month_to", "").strip()

    if q:
        qs = qs.filter(
            models.Q(cash_in__icontains=q) |
            models.Q(libyan_cash__icontains=q) |
            models.Q(exchange_rate__icontains=q) |
            models.Q(remarks__icontains=q) |
            models.Q(client__name__icontains=q) |
            models.Q(from_source__from_field__icontains=q)
        )
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if client_id:
        qs = qs.filter(client_id=client_id)
    if year:
        qs = qs.filter(date__year=year)
    if month_from:
        qs = qs.filter(date__month__gte=month_from)
    if month_to:
        qs = qs.filter(date__month__lte=month_to)

    agg = qs.aggregate(
        total_egp=Sum("cash_in"),
        total_lyd=Sum("libyan_cash"),
    )

    total_egp_val = agg["total_egp"] or 0
    total_lyd_val = agg["total_lyd"] or 0
    avg_rate = round(total_egp_val / total_lyd_val, 3) if total_lyd_val else 0
    filtered_count = qs.count()
    total_count = Capital.objects.count()

    clients = ClientBalance.objects.all().order_by("name")

    is_filtered = bool(q or date_from or date_to or client_id or year or month_from or month_to)

    return render(request, "capital_list.html", {
        "title": "الأرصدة",
        "capitals": qs,
        "clients": clients,
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "selected_client": client_id,
        "selected_year": year,
        "selected_month_from": month_from,
        "selected_month_to": month_to,
        "total_egp": total_egp_val,
        "total_lyd": total_lyd_val,
        "avg_rate": avg_rate,
        "order": order,
        "filtered_count": filtered_count,
        "total_count": total_count,
        "is_filtered": is_filtered,
    })


def expenses_list(request):
    from datetime import datetime as dt
    qs = Expense.objects.all().order_by("-date")
    q = request.GET.get("q", "").strip()
    expense_type = request.GET.get("expense_type", "").strip()
    paid_by = request.GET.get("paid_by", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    date_from_month = request.GET.get("date_from_month", "").strip()
    date_to_month = request.GET.get("date_to_month", "").strip()
    amount_from = request.GET.get("amount_from", "").strip()
    amount_to = request.GET.get("amount_to", "").strip()

    if date_from_month:
        try:
            y, m = date_from_month.split("-")
            date_from = f"{y}-{m}-01"
        except:
            pass
    if date_to_month:
        try:
            y, m = date_to_month.split("-")
            import calendar
            last_day = calendar.monthrange(int(y), int(m))[1]
            date_to = f"{y}-{m}-{last_day}"
        except:
            pass

    if q:
        qs = qs.filter(
            models.Q(expense_type__icontains=q) |
            models.Q(notes__icontains=q) |
            models.Q(paid_by__icontains=q)
        )
    if expense_type:
        qs = qs.filter(expense_type__icontains=expense_type)
    if paid_by:
        qs = qs.filter(paid_by__icontains=paid_by)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if amount_from:
        qs = qs.filter(amount__gte=amount_from)
    if amount_to:
        qs = qs.filter(amount__lte=amount_to)

    expense_types = Expense.objects.values_list("expense_type", flat=True).distinct().order_by("expense_type")
    paid_by_list = Expense.objects.values_list("paid_by", flat=True).distinct().order_by("paid_by")
    total = qs.aggregate(t=Sum("amount"))["t"] or 0
    return render(request, "expenses_list.html", {
        "title": "المصروفات",
        "expenses": qs[:500],
        "q": q,
        "expense_filter": expense_type,
        "paid_by_filter": paid_by,
        "date_from": date_from,
        "date_to": date_to,
        "date_from_month": date_from_month,
        "date_to_month": date_to_month,
        "amount_from": amount_from,
        "amount_to": amount_to,
        "expense_types": expense_types,
        "paid_by_list": paid_by_list,
        "total": total,
    })


def add_expense(request):
    if request.method == "POST":
        form = ExpenseForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "✅ تم إضافة المصروف بنجاح.")
            return redirect("expenses_list")
    else:
        form = ExpenseForm(initial={"date": datetime.now().strftime("%Y-%m-%d")})
    expense_types = Expense.objects.values_list("expense_type", flat=True).distinct().order_by("expense_type")
    paid_by_list = Expense.objects.values_list("paid_by", flat=True).distinct().order_by("paid_by")
    return render(request, "add_expense.html", {
        "title": "إضافة مصروف",
        "form": form,
        "expense_types": expense_types,
        "paid_by_list": paid_by_list,
    })


def edit_expense(request, id):
    obj = get_object_or_404(Expense, id=id)
    if request.method == "POST":
        form = ExpenseForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "✅ تم تعديل المصروف بنجاح.")
            return redirect("expenses_list")
    else:
        form = ExpenseForm(instance=obj)
    expense_types = Expense.objects.values_list("expense_type", flat=True).distinct().order_by("expense_type")
    paid_by_list = Expense.objects.values_list("paid_by", flat=True).distinct().order_by("paid_by")
    return render(request, "add_expense.html", {
        "title": "تعديل مصروف",
        "form": form,
        "edit_mode": True,
        "expense_types": expense_types,
        "paid_by_list": paid_by_list,
    })


def delete_expense(request, id):
    obj = get_object_or_404(Expense, id=id)
    obj.delete()
    messages.success(request, f"✅ تم حذف المصروف #{id}.")
    return redirect("expenses_list")


def clear_all_expenses(request):
    count = Expense.objects.count()
    Expense.objects.all().delete()
    messages.success(request, f"✅ تم مسح {count} مصروف بالكامل.")
    return redirect("expenses_list")


def _import_excel(model_class, file, field_map, request):
    wb = openpyxl.load_workbook(file, read_only=True)
    ws = wb.active
    rows_iter = iter(ws.iter_rows(values_only=True))
    header = [str(c).strip() if c else "" for c in next(rows_iter, [])]
    col_index = {h: i for i, h in enumerate(header)}
    created = 0
    errors = []
    for row_idx, row in enumerate(rows_iter, start=2):
        kwargs = {}
        for excel_col, model_field in field_map.items():
            idx = col_index.get(excel_col)
            if idx is not None and idx < len(row):
                kwargs[model_field] = row[idx]
        try:
            model_class.objects.create(**kwargs)
            created += 1
        except Exception as e:
            errors.append(f"الصف {row_idx}: {e}")
    wb.close()
    if created:
        messages.success(request, f"تم استيراد {created} سجل بنجاح.")
    for err in errors:
        messages.error(request, err)


def _next_order_number(office_code=""):
    from django.db.models import Max
    from django.db.models.functions import Cast
    from django.db.models import Value, IntegerField
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"خ-{today}-{office_code}-" if office_code else f"خ-{today}-"
    last = Database.objects.filter(
        order_number__startswith=prefix
    ).annotate(
        num=Cast("order_number", IntegerField())
    ).aggregate(Max("num"))["num__max"]
    if last:
        return str(last + 1)
    return f"{prefix}001"


def add_transaction(request):
    now_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M")
    next_num = _next_order_number()
    form = DatabaseForm(initial={"order_number": next_num, "office_name": 1, "date": now_str, "time": time_str})

    clients, total_egp = _get_clients_with_remaining()
    offices = OfficeName.objects.all()
    if request.method == "POST":
        if "excel_file" in request.FILES:
            field_map = {
                "sender_name": "sender_name",
                "receiver_name": "receiver_name",
                "transfer_amount": "transfer_amount",
                "exchange_rate": "exchange_rate",
                "order_number": "order_number",
                "date": "date",
                "time": "time",
                "sender_tele": "sender_tele",
                "receiver_tele": "receiver_tele",
                "receiver_region": "receiver_region",
                "transfered_amount": "transfered_amount",
                "remarks": "remarks",
            }
            _import_excel(Database, request.FILES["excel_file"], field_map, request)
            return redirect("transactions")
        post_data = request.POST.copy()
        client_id = post_data.get("from_source", "")
        if not client_id:
            messages.error(request, "❌ يجب اختيار 'من رصيد' قبل الحفظ.")
            form = DatabaseForm(post_data)
            return render(request, "form.html", {
                "title": "إضافة حوالة جديدة",
                "form": form,
                "submit_label": "حفظ الحوالة",
                "clients": clients,
                "total_egp": total_egp,
                "offices": offices,
            })
        client_name = ClientBalance.objects.filter(id=client_id).values_list("name", flat=True).first()
        if client_name:
            from_obj, _ = FromSource.objects.get_or_create(from_field=client_name)
            post_data["from_source"] = from_obj.id
        office_id = post_data.get("office_name", "")
        if office_id:
            office = OfficeName.objects.filter(id=office_id).first()
            if office:
                post_data["order_number"] = _next_order_number(office.code)
        form = DatabaseForm(post_data)
        if form.is_valid():
            t = form.save()
            return render(request, "transaction_success.html", {"t": t})
    return render(request, "form.html", {
        "title": "إضافة حوالة جديدة",
        "form": form,
        "submit_label": "حفظ الحوالة",
        "clients": clients,
        "total_egp": total_egp,
        "offices": offices,
    })


def import_whatsapp(request):
    results = []
    raw_text = ""
    use_prev_rate = request.POST.get("use_prev_rate") or request.GET.get("use_prev_rate")
    import_date_str = request.POST.get("import_date", "")
    import_date = None
    if import_date_str:
        try:
            import_date = datetime.strptime(import_date_str, "%Y-%m-%d")
        except ValueError:
            import_date = None
    if request.method == "POST":
        use_prev_rate = request.POST.get("use_prev_rate")
        if "text_file" in request.FILES:
            raw_text = request.FILES["text_file"].read().decode("utf-8-sig")
        elif "pasted_text" in request.POST:
            raw_text = request.POST["pasted_text"]
        elif "raw_text" in request.POST:
            raw_text = request.POST["raw_text"]

        if "filtered_results" in request.POST and request.POST["filtered_results"]:
            try:
                parsed = json.loads(request.POST["filtered_results"])
            except (json.JSONDecodeError, ValueError):
                parsed = []
        elif raw_text:
            parsed = parse_whatsapp_text(raw_text)
            if use_prev_rate and parsed:
                last_rate = None
                for item in parsed:
                    if item.get("exchange_rate"):
                        last_rate = item["exchange_rate"]
                    elif last_rate:
                        item["exchange_rate"] = last_rate
                        if item.get("amount_egp") and last_rate > 0:
                            item["amount_lyd"] = round(item["amount_egp"] / last_rate, 2)
        else:
            parsed = []

        if raw_text and not parsed:
            messages.error(request, "لم يتم التعرف على أي حوالة صالحة. تأكد من تنسيق النص.")
        else:
            if "confirm" in request.POST:
                sender_client_id = request.POST.get("client_id")
                if sender_client_id:
                    ahmed = ClientBalance.objects.filter(id=sender_client_id).first()
                else:
                    ahmed = ClientBalance.objects.filter(name__contains="أحمد ياسين").first()
                from_whatsapp, _ = FromSource.objects.get_or_create(from_field="واتساب")
                created = 0
                errors = []
                created_records = []
                order_num_str = _next_order_number()
                order_num_parts = order_num_str.rsplit("-", 1)
                order_num_seq = int(order_num_parts[-1]) if len(order_num_parts) > 1 else 1
                order_num_prefix = "-".join(order_num_parts[:-1]) + "-" if len(order_num_parts) > 1 else order_num_str
                for item in parsed:
                    try:
                        amount_lyd = item["amount_lyd"]
                        amount_egp = item["amount_egp"]
                        if ahmed:
                            ahmed.egp_balance -= amount_egp
                            ahmed.lyd_balance += amount_lyd
                            ahmed.save()

                        tt_name = item.get("transfer_type", "كاش")
                        tt, _ = TransferType.objects.get_or_create(Transfer_type=tt_name)
                        rec = Database.objects.create(
                            sender_name="أحمد ياسين",
                            receiver_tele=item.get("receiver_tele") or "",
                            transfer_amount=amount_lyd,
                            exchange_rate=item["exchange_rate"],
                            transfered_amount=amount_egp,
                            transfer_type=tt,
                            order_number=f"{order_num_prefix}{order_num_seq:03d}",
                            date=import_date or datetime.now(),
                            time=datetime.now(),
                            from_source=from_whatsapp,
                        )
                        created_records.append(rec)
                        created += 1
                        order_num_seq += 1
                    except Exception as e:
                        errors.append(f"خطأ: {e}")

                if created:
                    client_label = ahmed.name if ahmed else "أحمد ياسين"
                    word = "حوالة" if created == 1 else "حوالات"
                    messages.success(request, f"✅ تم استيراد {created} {word} بنجاح وخصمها من رصيد {client_label}.")
                if not created and not errors:
                    messages.warning(request, "⚠️ لم يتم استيراد أي حوالة. تأكد من صحة البيانات.")
                if errors:
                    ImportAlert.objects.create(
                        import_type="whatsapp",
                        total_items=len(parsed),
                        success_count=created,
                        failed_count=len(errors),
                        failed_details="\n".join(errors),
                    )
                    messages.error(request, f"❌ فشل استيراد {len(errors)} من {len(parsed)} حوالة. السبب: {errors[0]}")
                    if len(errors) > 1:
                        messages.info(request, f"📋 تفاصيل الفشل ({len(errors)} رسالة):")
                        for i, err in enumerate(errors[:5], 1):
                            messages.warning(request, f"  {i}. {err}")
                        if len(errors) > 5:
                            messages.info(request, f"  ... و {len(errors) - 5} أخطاء أخرى. راجع تنبيهات الاستيراد.")
                return render(request, "import_whatsapp.html", {
                    "title": "استيراد حوالات خارجية",
                    "created_records": created_records,
                    "db_count": Database.objects.count(),
                    "db_total_egp": Database.objects.aggregate(t=Sum("transfered_amount"))["t"] or 0,
                    "use_prev_rate": use_prev_rate,
                    "whatsapp_alerts": ImportAlert.objects.filter(import_type="whatsapp")[:10],
                })
            else:
                results = parsed

    whatsapp_alerts = ImportAlert.objects.filter(import_type="whatsapp")[:10]
    return render(request, "import_whatsapp.html", {
        "title": "استيراد حوالات خارجية",
        "results": results,
        "raw_text": raw_text,
        "db_count": Database.objects.count(),
        "db_total_egp": Database.objects.aggregate(t=Sum("transfered_amount"))["t"] or 0,
        "use_prev_rate": use_prev_rate,
        "whatsapp_alerts": whatsapp_alerts,
    })


def add_capital(request):
    form = CapitalDepositForm(initial={"date": datetime.now().strftime("%Y-%m-%d")})
    if request.method == "POST":
        form = CapitalDepositForm(request.POST)
        if form.is_valid():
            client = form.cleaned_data["client"]
            amount_egp = form.cleaned_data["amount_egp"]
            exchange_rate = form.cleaned_data["exchange_rate"]
            amount_lyd = round(amount_egp / exchange_rate, 2) if exchange_rate else 0
            date = form.cleaned_data["date"]
            notes = form.cleaned_data.get("notes", "")

            client.egp_balance += amount_egp
            client.lyd_balance += amount_lyd
            client.save()

            Capital.objects.create(
                cash_in=amount_egp,
                libyan_cash=amount_lyd,
                date=date,
                in_type="إيداع",
                exchange_rate=exchange_rate,
                remarks=notes,
                client=client,
            )

            messages.success(request, f"✅ تم إيداع {amount_egp:,.2f} EGP لحساب {client.name} (مايعادل {amount_lyd:,.2f} LYD)")
            return redirect("capital_list")
    return render(request, "deposit_form.html", {
        "title": "إضافة رصيد",
        "form": form,
        "submit_label": "إيداع الرصيد",
        "back_url": "capital_list",
    })


import re
from datetime import datetime as dt


def _ar_to_western(s):
    """Convert Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩) to Western digits."""
    return s.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))


def _is_skip_line(ln):
    """Return True if line should be skipped (phone, timestamp, noise)."""
    clean = ln.replace(" ", "").replace("-", "").replace("+", "").replace("(", "").replace(")", "")
    # Phone numbers: 10+ digits
    if re.match(r'^[\d\s\-\+\(\)]+$', clean) and len(clean) >= 10:
        return True
    # Time patterns: "3:31", "14:30", "3:31 ص", "3:31 م"
    if re.match(r'^\d{1,2}:\d{2}(\s*[صمم])?$', ln.replace(" ", "")):
        return True
    # Lines that are just dates
    if re.match(r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$', ln.replace(" ", "")):
        return True
    return False


def _clean_number(raw):
    """Clean a number string: handle dot as thousands separator (500.000 = 500000)."""
    raw = raw.strip().rstrip(".")
    # If pattern is like 500.000 (3 digits after dot) -> dot is thousands separator
    if re.match(r'^\d{1,3}\.\d{3}$', raw):
        return float(raw.replace(".", ""))
    # Otherwise remove dots that are thousands separators (e.g., 1.000.000)
    parts = raw.split(".")
    if len(parts) > 2:
        return float("".join(parts))
    # Normal: replace comma with nothing, keep dot as decimal
    return float(raw.replace(",", ""))


def _extract_egp(ln):
    """Try to extract EGP amount from a single line. Returns float or None.
    Returns None if the number has 🇾🇪🇱🇾 flag (meaning it's LYD, not EGP)."""
    # If line has 🇾🇪 or 🇱🇾 flag after number AND has 'ج.م' or 'ج م', it's EGP not LYD
    # Otherwise 🇾🇪 just means "via Yemen channel", not currency - ignore it

    # Pattern 1: "استلام XXX" - extract number after استلام
    m = re.search(r'استلام\s*(\d[\d,\.]*)', ln)
    if m:
        try:
            v = _clean_number(m.group(1))
            if re.search(r'الف', ln) and v < 10000:
                v *= 1000
            elif re.search(r'مليون', ln) and v < 10000:
                v *= 1000000
            if v >= 10:
                return v
        except ValueError:
            pass

    # Pattern 2: "القيمة:XXX" or "القيمة XXX"
    m = re.search(r'القيمو?ه?ة?[:\s]*(\d[\d,\.]*)', ln)
    if m:
        try:
            v = _clean_number(m.group(1))
            if v >= 10:
                return v
        except ValueError:
            pass

    # Pattern 3: "XXX جنيه مصري" or "XXXج.م" or "XXX ج م"
    m = re.search(r'(\d[\d,\.]*)\s*ج[.\s]*م', ln)
    if m:
        try:
            v = _clean_number(m.group(1))
            if v >= 10:
                return v
        except ValueError:
            pass

    # Pattern 4: "XXXالف" or "XXX الف"
    m = re.search(r'(\d[\d,\.]*)\s*الف', ln)
    if m:
        try:
            v = _clean_number(m.group(1)) * 1000
            if v >= 10:
                return v
        except ValueError:
            pass

    # Pattern 5: "XXXمليون" or "XXX مليون"
    m = re.search(r'(\d[\d,\.]*)\s*مليون', ln)
    if m:
        try:
            v = _clean_number(m.group(1)) * 1000000
            if v >= 10:
                return v
        except ValueError:
            pass

    # Pattern 6: "حول له XXX"
    m = re.search(r'حول\s+له?\s+(\d[\d,\.]*)', ln)
    if m:
        try:
            v = _clean_number(m.group(1))
            if v >= 10:
                return v
        except ValueError:
            pass

    # Pattern 7: bare number on its own line (e.g., "296000")
    m = re.match(r'^\s*(\d[\d,\.]*)\s*$', ln)
    if m:
        try:
            v = _clean_number(m.group(1))
            if v >= 50:
                return v
        except ValueError:
            pass

    return None


def _extract_rate(ln):
    """Try to extract exchange rate from a single line. Returns float or None."""
    # Strip flag emojis and normalize Arabic comma before matching
    ln_clean = re.sub(r'[\U0001f1e6-\U0001f1ff]{2}', '', ln).strip()
    ln_clean = ln_clean.replace("،", ".")

    # Pattern 1: "سعر X" or "سعر: X"
    m = re.search(r'سعر[:\s]*(\d[\d,\.]*)', ln_clean)
    if m:
        r_str = m.group(1).replace(",", ".")
        try:
            v = float(r_str)
            if 3 < v < 10:
                return v
        except ValueError:
            pass

    # Pattern 2: "X♻️"
    m = re.search(r'(\d+[\.,]?\d*)\s*♻', ln_clean)
    if m:
        r_str = m.group(1).replace(",", ".")
        try:
            v = float(r_str)
            if 3 < v < 10:
                return v
        except ValueError:
            pass

    # Pattern 3: standalone rate number (when سعر is on separate line)
    m = re.match(r'^\s*(\d+[\.,]?\d*)\s*$', ln_clean)
    if m:
        r_str = m.group(1).replace(",", ".")
        try:
            v = float(r_str)
            if 3 < v < 10:
                return v
        except ValueError:
            pass

    return None


def parse_balance_lines(text):
    """Parse WhatsApp balance messages. Extracts EGP amount and rate from each block.
    
    Format: each message has 'استلام XXX' (EGP amount) and 'سعر X' (rate).
    Numbers may have attached letters like '400الف' (=400,000) or '76200🇾🇪' (=LYD).
    """
    text = _ar_to_western(text)

    # Split into blocks using WhatsApp timestamp patterns
    # Format 1: [12/07, 11:59 pm]  Format 2: [11:59 pm, 12/07/2026]
    blocks = re.split(r'\[?\d{1,2}[/-]\d{1,2},?\s*\d{1,2}:\d{2}(?:\s*(?:am|pm|ص|م))?\]?\s*|\[?\d{1,2}:\d{2}\s*(?:am|pm|ص|م)\s*,\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\]?\s*', text)

    results = []
    for block in blocks:
        block = block.strip()
        if not block or len(block) < 5:
            continue

        lines = block.split('\n')
        egp = None
        rate = None
        found_istalam = False

        for li, line in enumerate(lines):
            ln = line.strip()
            if not ln:
                continue

            # Extract EGP from 'استلام' line
            if egp is None:
                egp = _extract_egp(ln)
                if egp is None and 'استلام' in ln:
                    found_istalam = True
                elif egp is not None:
                    found_istalam = False

            # If استلام found but no number on same line, check next line
            if egp is None and found_istalam and li + 1 < len(lines):
                next_ln = lines[li + 1].strip()
                if next_ln:
                    egp = _extract_egp(next_ln)
                    if egp is not None:
                        found_istalam = False

            # Extract rate from 'سعر' line or standalone number
            if rate is None:
                rate = _extract_rate(ln)

            # Check for bare number with 🇾🇪 flag - treat as EGP (not LYD)
            if egp is None:
                m = re.search(r'(\d[\d,\.]*)\s*🇾🇪', ln)
                if m:
                    try:
                        v = _clean_number(m.group(1))
                        if v >= 10:
                            egp = v
                    except ValueError:
                        pass

        if egp is None or rate is None:
            continue

        # Sanity check - skip nonsensical results
        if egp < 10:
            continue

        final_lyd = round(egp / rate, 2) if rate else 0

        results.append({
            "egp": egp,
            "rate": rate,
            "lyd": final_lyd,
        })

    return results


def import_balance(request):
    from django.shortcuts import get_object_or_404
    clients = ClientBalance.objects.all().order_by("name")
    results = []
    raw_text = ""
    selected_client_id = None
    selected_date = None
    if request.method == "POST":
        if "text_file" in request.FILES:
            raw_text = request.FILES["text_file"].read().decode("utf-8-sig")
        elif "pasted_text" in request.POST:
            raw_text = request.POST["pasted_text"]
        elif "raw_text" in request.POST:
            raw_text = request.POST["raw_text"]
        selected_client_id = request.POST.get("client_id")
        selected_date_str = request.POST.get("import_date", "").strip()
        selected_date = dt.strptime(selected_date_str, "%Y-%m-%d").date() if selected_date_str else None
        has_filtered = "filtered_results" in request.POST and request.POST.get("filtered_results")
        if has_filtered or (raw_text and selected_client_id):
            client_name = ClientBalance.objects.filter(id=selected_client_id).values_list("name", flat=True).first()
            if "filtered_results" in request.POST and request.POST["filtered_results"]:
                try:
                    parsed = json.loads(request.POST["filtered_results"])
                except (json.JSONDecodeError, ValueError):
                    parsed = []
            else:
                parsed = parse_balance_lines(raw_text)
            if not parsed:
                messages.error(request, "لم يتم التعرف على أي بيانات صالحة.")
            else:
                for item in parsed:
                    item["client_name"] = client_name or "-"
                    if not isinstance(item.get("date"), (type(None),)):
                        try:
                            if isinstance(item["date"], str):
                                item["date"] = dt.strptime(item["date"], "%Y-%m-%d").date()
                        except (ValueError, TypeError):
                            item["date"] = selected_date or dt.now().date()
                    else:
                        item["date"] = selected_date or dt.now().date()
                if "confirm" in request.POST:
                    client = get_object_or_404(ClientBalance, id=selected_client_id)
                    created = 0
                    errors = []
                    created_records = []
                    for item in parsed:
                        try:
                            client.egp_balance += item["egp"]
                            client.lyd_balance += item["lyd"]
                            rec = Capital.objects.create(
                                cash_in=item["egp"],
                                libyan_cash=item["lyd"],
                                exchange_rate=item["rate"],
                                date=item["date"],
                                in_type="إيداع",
                                client=client,
                            )
                            created_records.append(rec)
                            created += 1
                        except Exception as e:
                            errors.append(f"خطأ في سطر: EGP={item.get('egp', '?')}, السعر={item.get('rate', '?')} - {e}")
                    client.save()
                    if created:
                        messages.success(request, f"✅ تم استيراد {created} رصيد لحساب {client.name}.")
                    if not created and not errors:
                        messages.warning(request, "⚠️ لم يتم استيراد أي رصيد. تأكد من صحة البيانات.")
                    if errors:
                        ImportAlert.objects.create(
                            import_type="balance",
                            total_items=len(parsed),
                            success_count=created,
                            failed_count=len(errors),
                            failed_details="\n".join(errors),
                        )
                        messages.error(request, f"❌ فشل استيراد {len(errors)} من {len(parsed)} رصيد. السبب: {errors[0]}")
                        if len(errors) > 1:
                            messages.info(request, f"📋 تفاصيل الفشل ({len(errors)} سطر):")
                            for i, err in enumerate(errors[:5], 1):
                                messages.warning(request, f"  {i}. {err}")
                            if len(errors) > 5:
                                messages.info(request, f"  ... و {len(errors) - 5} أخطاء أخرى. راجع تنبيهات الاستيراد.")
                    return render(request, "import_balance.html", {
                        "title": "استيراد أرصدة خارجية",
                        "clients": clients,
                        "created_records": created_records,
                        "selected_client_id": selected_client_id,
                        "selected_date": selected_date,
                        "cap_count": Capital.objects.count(),
                        "cap_total_egp": Capital.objects.aggregate(t=Sum("cash_in"))["t"] or 0,
                        "balance_alerts": ImportAlert.objects.filter(import_type="balance")[:10],
                    })
                else:
                    results = parsed
    balance_alerts = ImportAlert.objects.filter(import_type="balance")[:10]
    return render(request, "import_balance.html", {
        "title": "استيراد أرصدة خارجية",
        "clients": clients,
        "results": results,
        "raw_text": raw_text,
        "selected_client_id": selected_client_id,
        "selected_date": selected_date,
        "cap_count": Capital.objects.count(),
        "cap_total_egp": Capital.objects.aggregate(t=Sum("cash_in"))["t"] or 0,
        "balance_alerts": balance_alerts,
    })


def export_transfers_excel(request):
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="transfers.xlsx"'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "الحوالات"
    headers = ["رقم الطلب", "التاريخ", "الوقت", "المرسل", "هاتف المرسل", "المستلم", "هاتف المستلم", "المبلغ مصري", "سعر الصرف", "المبلغ ليبي", "نوع التحويل", "المكتب", "منطقة المستلم", "ملاحظات"]
    header_font = openpyxl.styles.Font(bold=True, color="FFFFFF", size=11)
    header_fill = openpyxl.styles.PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = openpyxl.styles.Alignment(horizontal="center", vertical="center")
    rows = Database.objects.select_related("transfer_type", "office_name").order_by("-date", "-id")
    for row_idx, t in enumerate(rows, 2):
        ws.cell(row=row_idx, column=1, value=t.order_number or "")
        ws.cell(row=row_idx, column=2, value=str(t.date) if t.date else "")
        ws.cell(row=row_idx, column=3, value=str(t.time) if t.time else "")
        ws.cell(row=row_idx, column=4, value=t.sender_name or "")
        ws.cell(row=row_idx, column=5, value=t.sender_tele or "")
        ws.cell(row=row_idx, column=6, value=t.receiver_name or "")
        ws.cell(row=row_idx, column=7, value=t.receiver_tele or "")
        ws.cell(row=row_idx, column=8, value=t.transfered_amount or 0)
        ws.cell(row=row_idx, column=9, value=t.exchange_rate or 0)
        ws.cell(row=row_idx, column=10, value=t.transfer_amount or 0)
        ws.cell(row=row_idx, column=11, value=t.transfer_type.Transfer_type if t.transfer_type else "")
        ws.cell(row=row_idx, column=12, value=t.office_name.office_name if t.office_name else "")
        ws.cell(row=row_idx, column=13, value=t.receiver_region or "")
        ws.cell(row=row_idx, column=14, value=t.remarks or "")
        for col in [8, 9, 10]:
            ws.cell(row=row_idx, column=col).number_format = '#,##0.000'
    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 14
    ws.column_dimensions['C'].width = 10
    ws.column_dimensions['D'].width = 18
    ws.column_dimensions['E'].width = 15
    ws.column_dimensions['F'].width = 18
    ws.column_dimensions['G'].width = 15
    ws.column_dimensions['H'].width = 16
    ws.column_dimensions['I'].width = 12
    ws.column_dimensions['J'].width = 16
    ws.column_dimensions['K'].width = 14
    ws.column_dimensions['L'].width = 18
    ws.column_dimensions['M'].width = 16
    ws.column_dimensions['N'].width = 18
    wb.save(response)
    return response


def export_capital_excel(request):
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="capital.xlsx"'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "الأرصدة"
    headers = ["التاريخ", "العميل", "وارد نقدي", "نقدي ليبي", "سعر الصرف", "نوع الإيداع", "ملاحظات"]
    header_font = openpyxl.styles.Font(bold=True, color="FFFFFF", size=11)
    header_fill = openpyxl.styles.PatternFill(start_color="059669", end_color="059669", fill_type="solid")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = openpyxl.styles.Alignment(horizontal="center", vertical="center")
    rows = Capital.objects.select_related("client").order_by("-date", "-id")
    for row_idx, c in enumerate(rows, 2):
        ws.cell(row=row_idx, column=1, value=str(c.date) if c.date else "")
        ws.cell(row=row_idx, column=2, value=c.client.name if c.client else "")
        ws.cell(row=row_idx, column=3, value=c.cash_in or 0)
        ws.cell(row=row_idx, column=4, value=c.libyan_cash or 0)
        ws.cell(row=row_idx, column=5, value=c.exchange_rate or 0)
        ws.cell(row=row_idx, column=6, value=c.in_type or "")
        ws.cell(row=row_idx, column=7, value=c.remarks or "")
        for col in [3, 4, 5]:
            ws.cell(row=row_idx, column=col).number_format = '#,##0.000'
    ws.column_dimensions['A'].width = 14
    ws.column_dimensions['B'].width = 20
    ws.column_dimensions['C'].width = 16
    ws.column_dimensions['D'].width = 16
    ws.column_dimensions['E'].width = 12
    ws.column_dimensions['F'].width = 14
    ws.column_dimensions['G'].width = 22
    wb.save(response)
    return response


def import_transfers_excel(request):
    if request.method != "POST" or "excel_file" not in request.FILES:
        return redirect("import_whatsapp")
    try:
        wb = openpyxl.load_workbook(request.FILES["excel_file"])
        ws = wb.active
        created = 0
        errors = []
        ahmed = ClientBalance.objects.filter(name__contains="أحمد ياسين").first()
        from_system, _ = FromSource.objects.get_or_create(from_field="نظام")
        order_num_str = _next_order_number()
        order_num_parts = order_num_str.rsplit("-", 1)
        order_num_seq = int(order_num_parts[-1]) if len(order_num_parts) > 1 else 1
        order_num_prefix = "-".join(order_num_parts[:-1]) + "-" if len(order_num_parts) > 1 else order_num_str
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
            try:
                if not row or not row[0]:
                    continue
                amount_egp = float(row[7] or 0)
                rate = float(row[8] or 0)
                amount_lyd = float(row[9] or 0)
                if amount_egp <= 0:
                    continue
                if ahmed:
                    ahmed.egp_balance -= amount_egp
                    ahmed.lyd_balance += amount_lyd
                    ahmed.save()
                tt_name = str(row[10] or "كاش").strip()
                tt, _ = TransferType.objects.get_or_create(Transfer_type=tt_name)
                office_name_str = str(row[11] or "").strip()
                office_obj = None
                if office_name_str:
                    office_obj, _ = OfficeName.objects.get_or_create(office_name=office_name_str)
                Database.objects.create(
                    order_number=str(row[0] or f"{order_num_prefix}{order_num_seq:03d}"),
                    date=datetime.strptime(str(row[1]), "%Y-%m-%d").date() if row[1] else datetime.now().date(),
                    time=datetime.strptime(str(row[2]), "%H:%M:%S").time() if row[2] else datetime.now().time(),
                    sender_name=str(row[3] or ""),
                    sender_tele=str(row[4] or ""),
                    receiver_name=str(row[5] or ""),
                    receiver_tele=str(row[6] or ""),
                    transfered_amount=amount_egp,
                    exchange_rate=rate,
                    transfer_amount=amount_lyd,
                    transfer_type=tt,
                    office_name=office_obj,
                    receiver_region=str(row[12] or ""),
                    remarks=str(row[13] or ""),
                    from_source=from_system,
                )
                created += 1
                order_num_seq += 1
            except Exception as e:
                errors.append(f"صف {row_idx}: {e}")
        if created:
            messages.success(request, f"✅ تم استيراد {created} حوالة من ملف Excel بنجاح.")
        for err in errors:
            messages.error(request, f"❌ {err}")
    except Exception as e:
        messages.error(request, f"❌ خطأ في قراءة الملف: {e}")
    return redirect("import_whatsapp")


def import_capital_excel(request):
    if request.method != "POST" or "excel_file" not in request.FILES:
        return redirect("import_balance")
    client_id = request.POST.get("client_id")
    if not client_id:
        messages.error(request, "❌ يجب اختيار العميل أولاً.")
        return redirect("import_balance")
    client = get_object_or_404(ClientBalance, id=client_id)
    try:
        wb = openpyxl.load_workbook(request.FILES["excel_file"])
        ws = wb.active
        created = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            try:
                if not row or not row[2]:
                    continue
                egp = float(row[2] or 0)
                lyd = float(row[3] or 0)
                rate = float(row[4] or 0)
                if egp <= 0:
                    continue
                date_str = str(row[0]) if row[0] else ""
                if date_str and "-" in date_str:
                    date_val = datetime.strptime(date_str, "%Y-%m-%d").date()
                else:
                    date_val = datetime.now().date()
                client.egp_balance += egp
                client.lyd_balance += lyd
                Capital.objects.create(
                    cash_in=egp,
                    libyan_cash=lyd,
                    exchange_rate=rate,
                    date=date_val,
                    in_type=str(row[5] or "إيداع"),
                    remarks=str(row[6] or ""),
                    client=client,
                )
                created += 1
            except Exception:
                pass
        client.save()
        if created:
            messages.success(request, f"✅ تم استيراد {created} رصيد لحساب {client.name} من ملف Excel.")
    except Exception as e:
        messages.error(request, f"❌ خطأ في قراءة الملف: {e}")
    return redirect("import_balance")


@csrf_exempt
def quick_client(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            return JsonResponse({"error": "الاسم مطلوب"}, status=400)
        client = ClientBalance.objects.create(name=name)
        return JsonResponse({"id": client.id, "name": client.name})
    return JsonResponse({"error": "method not allowed"}, status=405)


@csrf_exempt
def quick_office(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        code = request.POST.get("code", "").strip()
        if not name:
            return JsonResponse({"error": "الاسم مطلوب"}, status=400)
        office, _ = OfficeName.objects.get_or_create(office_name=name, defaults={"code": code})
        return JsonResponse({"id": office.id, "name": office.office_name, "code": office.code})
    return JsonResponse({"error": "method not allowed"}, status=405)


def transaction_receipt(request, id):
    t = get_object_or_404(Database, id=id)
    return render(request, "transaction_receipt.html", {"t": t})


def profits_report(request):
    from datetime import datetime

    year = request.GET.get("year", "")
    month_from = request.GET.get("month_from", "")
    month_to = request.GET.get("month_to", "")

    if not year:
        year = datetime.now().year
    year = int(year)

    if month_from:
        month_from = int(month_from)
    else:
        month_from = 1

    if month_to:
        month_to = int(month_to)
    else:
        month_to = 12

    month_names = {
        1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل",
        5: "مايو", 6: "يونيو", 7: "يوليو", 8: "أغسطس",
        9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
    }

    data = []
    total_profit = 0
    total_db_egp = 0
    total_db_lyd = 0
    total_cap_egp = 0
    total_cap_lyd = 0
    total_egp_to_lyd = 0
    total_lyd_surplus = 0

    for m in range(month_from, month_to + 1):
        tx_qs = Database.objects.filter(date__year=year, date__month=m)
        cap_qs = Capital.objects.filter(date__year=year, date__month=m)

        db_egp = tx_qs.aggregate(t=Sum("transfered_amount"))["t"] or 0
        db_lyd = tx_qs.aggregate(t=Sum("transfer_amount"))["t"] or 0
        cap_egp = cap_qs.aggregate(t=Sum("cash_in"))["t"] or 0
        cap_lyd = cap_qs.aggregate(t=Sum("libyan_cash"))["t"] or 0

        avg_rate = round(cap_egp / cap_lyd, 3) if cap_lyd else 0
        egypt_surplus_egp = cap_egp - db_egp
        egypt_surplus_lyd = egypt_surplus_egp / avg_rate if avg_rate else 0
        lyd_surplus = cap_lyd - db_lyd
        profit = egypt_surplus_lyd - lyd_surplus if avg_rate else 0

        total_db_egp += db_egp
        total_db_lyd += db_lyd
        total_cap_egp += cap_egp
        total_cap_lyd += cap_lyd
        total_egp_to_lyd += egypt_surplus_lyd
        total_lyd_surplus += lyd_surplus
        total_profit += profit

        db_avg_rate = round(db_egp / db_lyd, 3) if db_lyd else 0
        cap_avg_rate = round(cap_egp / cap_lyd, 3) if cap_lyd else 0

        data.append({
            "month": m,
            "month_name": month_names[m],
            "db_egp": db_egp,
            "db_lyd": db_lyd,
            "cap_egp": cap_egp,
            "cap_lyd": cap_lyd,
            "avg_rate": avg_rate,
            "db_avg_rate": db_avg_rate,
            "cap_avg_rate": cap_avg_rate,
            "egp_to_lyd": egypt_surplus_lyd,
            "lyd_surplus": lyd_surplus,
            "profit": profit,
        })

    months = [
        (1, "يناير"), (2, "فبراير"), (3, "مارس"), (4, "أبريل"),
        (5, "مايو"), (6, "يونيو"), (7, "يوليو"), (8, "أغسطس"),
        (9, "سبتمبر"), (10, "أكتوبر"), (11, "نوفمبر"), (12, "ديسمبر"),
    ]
    current_year = datetime.now().year
    years = list(range(current_year - 2, current_year + 3))

    total_db_avg_rate = round(total_db_egp / total_db_lyd, 3) if total_db_lyd else 0
    total_cap_avg_rate = round(total_cap_egp / total_cap_lyd, 3) if total_cap_lyd else 0
    total_egp_balance = total_cap_egp - total_db_egp

    return render(request, "profits_report.html", {
        "title": "تقرير الأرباح",
        "data": data,
        "year": year,
        "month_from": month_from,
        "month_to": month_to,
        "years": years,
        "months": months,
        "total_db_egp": total_db_egp,
        "total_db_lyd": total_db_lyd,
        "total_cap_egp": total_cap_egp,
        "total_cap_lyd": total_cap_lyd,
        "total_egp_to_lyd": total_egp_to_lyd,
        "total_lyd_surplus": total_lyd_surplus,
        "total_egp_balance": total_egp_balance,
        "total_db_avg_rate": total_db_avg_rate,
        "total_cap_avg_rate": total_cap_avg_rate,
        "total_profit": total_profit,
    })


def export_profits_report(request):
    from datetime import datetime
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from django.http import HttpResponse

    year = int(request.GET.get("year", datetime.now().year))
    month_from = int(request.GET.get("month_from", 1))
    month_to = int(request.GET.get("month_to", 12))

    month_names = {
        1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل",
        5: "مايو", 6: "يونيو", 7: "يوليو", 8: "أغسطس",
        9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
    }
    month_from_name = month_names.get(month_from, "")
    month_to_name = month_names.get(month_to, "")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "تقرير الأرباح"

    header_font = Font(name="Cairo", size=14, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="065f46", end_color="065f46", fill_type="solid")
    ws.merge_cells("A1:L1")
    ws["A1"] = f"تقرير الأرباح — الفترة من {month_from_name} الى {month_to_name} {year}"
    ws["A1"].font = header_font
    ws["A1"].fill = header_fill
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 40

    sub_font = Font(name="Cairo", size=10, bold=True, color="FFFFFF")
    ws.merge_cells("A2:L2")
    ws["A2"] = "شركة اليمامة المالية"
    ws["A2"].font = sub_font
    ws["A2"].fill = header_fill
    ws["A2"].alignment = Alignment(horizontal="center")
    ws.row_dimensions[2].height = 28

    headers = ["#", "الشهر", "إيداعات (مصري)", "حوالات (مصري)", "الفرق ÷ السعر", "إيداعات (ليبي)", "حوالات (ليبي)", "الفرق الليبي", "سعر الحوالات", "سعر الارصدة", "متوسط السعر", "صافي الربح"]
    header_row = 4
    col_header_fill = PatternFill(start_color="f0fdf9", end_color="f0fdf9", fill_type="solid")
    col_header_font = Font(name="Cairo", size=10, bold=True, color="374151")
    thin_border = Border(
        left=Side(style="thin", color="e5e7eb"),
        right=Side(style="thin", color="e5e7eb"),
        top=Side(style="thin", color="e5e7eb"),
        bottom=Side(style="thin", color="e5e7eb"),
    )
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=h)
        cell.font = col_header_font
        cell.fill = col_header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    total_db_egp = 0
    total_db_lyd = 0
    total_cap_egp = 0
    total_cap_lyd = 0
    total_egp_to_lyd = 0
    total_lyd_surplus = 0
    total_profit = 0

    row = header_row + 1
    data_font = Font(name="Cairo", size=10)
    profit_pos_font = Font(name="Cairo", size=10, bold=True, color="059669")
    profit_neg_font = Font(name="Cairo", size=10, bold=True, color="dc2626")

    for m in range(month_from, month_to + 1):
        tx_qs = Database.objects.filter(date__year=year, date__month=m)
        cap_qs = Capital.objects.filter(date__year=year, date__month=m)

        db_egp = tx_qs.aggregate(t=Sum("transfered_amount"))["t"] or 0
        db_lyd = tx_qs.aggregate(t=Sum("transfer_amount"))["t"] or 0
        cap_egp = cap_qs.aggregate(t=Sum("cash_in"))["t"] or 0
        cap_lyd = cap_qs.aggregate(t=Sum("libyan_cash"))["t"] or 0

        avg_rate = round(cap_egp / cap_lyd, 3) if cap_lyd else 0
        egypt_surplus_egp = cap_egp - db_egp
        egypt_surplus_lyd = egypt_surplus_egp / avg_rate if avg_rate else 0
        lyd_surplus = cap_lyd - db_lyd
        profit = egypt_surplus_lyd - lyd_surplus if avg_rate else 0

        total_db_egp += db_egp
        total_db_lyd += db_lyd
        total_cap_egp += cap_egp
        total_cap_lyd += cap_lyd
        total_egp_to_lyd += egypt_surplus_lyd
        total_lyd_surplus += lyd_surplus
        total_profit += profit

        row_data = [m, month_names[m], cap_egp, db_egp, round(egypt_surplus_lyd, 2), cap_lyd, db_lyd, round(lyd_surplus, 2), round(db_egp / db_lyd, 3) if db_lyd else 0, round(cap_egp / cap_lyd, 3) if cap_lyd else 0, avg_rate, round(profit, 2)]
        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=row, column=col_idx, value=val)
            cell.font = data_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border
            if col_idx == 10:
                cell.font = profit_pos_font if profit >= 0 else profit_neg_font
            if col_idx == 9:
                cell.number_format = "0.000"
        row += 1

    total_row = row
    total_fill = PatternFill(start_color="e8f5e9", end_color="e8f5e9", fill_type="solid")
    total_font = Font(name="Cairo", size=10, bold=True)
    total_data = ["", "الإجمالي", total_cap_egp, total_db_egp, round(total_egp_to_lyd, 2), total_cap_lyd, total_db_lyd, round(total_lyd_surplus, 2), "—", "—", "—", round(total_profit, 2)]
    for col_idx, val in enumerate(total_data, 1):
        cell = ws.cell(row=total_row, column=col_idx, value=val)
        cell.font = total_font
        cell.fill = total_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    col_widths = [6, 14, 18, 18, 16, 18, 18, 16, 14, 14, 14, 18]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    filename = f"تقرير الارباح عن الفترة من {month_from_name} الى {month_to_name} سنة {year}.xlsx"
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def edit_capital(request, id):
    obj = get_object_or_404(Capital, id=id)
    if request.method == "POST":
        form = CapitalForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "✅ تم تعديل الرصيد بنجاح.")
            return redirect("capital_list")
    else:
        form = CapitalForm(instance=obj)
    return render(request, "deposit_form.html", {
        "title": "تعديل رصيد",
        "form": form,
        "submit_label": "تحديث الرصيد",
        "back_url": "capital_list",
    })


def edit_transaction(request, id):
    obj = get_object_or_404(Database, id=id)
    clients, total_egp = _get_clients_with_remaining()
    offices = OfficeName.objects.all()
    if request.method == "POST":
        post_data = request.POST.copy()
        client_id = post_data.get("from_source", "")
        if not client_id:
            messages.error(request, "❌ يجب اختيار 'من رصيد' قبل الحفظ.")
            form = DatabaseForm(post_data, instance=obj)
            return render(request, "form.html", {
                "title": "تعديل حوالة",
                "form": form,
                "submit_label": "تحديث الحوالة",
                "clients": clients,
                "total_egp": total_egp,
                "offices": offices,
            })
        client_name = ClientBalance.objects.filter(id=client_id).values_list("name", flat=True).first()
        if client_name:
            from_obj, _ = FromSource.objects.get_or_create(from_field=client_name)
            post_data["from_source"] = from_obj.id
        form = DatabaseForm(post_data, instance=obj)
        if form.is_valid():
            t = form.save()
            return render(request, "transaction_success.html", {"t": t})
    else:
        form = DatabaseForm(instance=obj)
    return render(request, "form.html", {
        "title": "تعديل حوالة",
        "form": form,
        "submit_label": "تحديث الحوالة",
        "clients": clients,
        "total_egp": total_egp,
        "offices": offices,
    })


def dismiss_carryover(request, period):
    if request.method == "POST":
        request.session["carryover_reminder_dismissed"] = period
    return JsonResponse({"ok": True})


def carryover_balance(request):
    clients, total_egp = _get_clients_with_remaining()
    source_year = request.GET.get("source_year") or request.POST.get("source_year") or ""
    source_month = request.GET.get("source_month") or request.POST.get("source_month") or ""
    target_month = request.GET.get("target_month") or request.POST.get("target_month") or ""
    person_id = request.GET.get("person_id") or request.POST.get("person_id") or ""

    selected_client = None
    source_egp = 0
    source_lyd = 0
    source_rate = 0
    carryover_exists = False

    if person_id:
        selected_client = ClientBalance.objects.filter(id=person_id).first()

    if request.method == "POST" and person_id and source_month and source_year and target_month:
        if selected_client:
            sy = int(source_year)
            sm = int(source_month)
            tm = int(target_month)
            carryover_exists = Capital.objects.filter(
                client=selected_client, in_type="ترحيل",
                date__year=sy, date__month=tm,
            ).exists()
            if not carryover_exists:
                dep = Capital.objects.filter(client=selected_client).exclude(in_type="ترحيل").aggregate(total=Sum("cash_in"))["total"] or 0
                used = Database.objects.filter(from_source__from_field__iexact=selected_client.name).aggregate(total=Sum("transfered_amount"))["total"] or 0
                egp = dep - used
                lyd_cap = Capital.objects.filter(client=selected_client).exclude(in_type="ترحيل").aggregate(total=Sum("libyan_cash"))["total"] or 0
                avg_rate = round(dep / lyd_cap, 3) if lyd_cap else 1
                lyd = round(egp / avg_rate, 2) if avg_rate else 0
                Capital.objects.create(
                    cash_in=-egp,
                    libyan_cash=-lyd,
                    date=datetime(sy, sm, 1),
                    in_type="ترحيل",
                    exchange_rate=avg_rate,
                    remarks=f"ترحيل سالب من شهر {sm} (خصم)",
                    client=selected_client,
                )
                Capital.objects.create(
                    cash_in=egp,
                    libyan_cash=lyd,
                    date=datetime(sy, tm, 1),
                    in_type="ترحيل",
                    exchange_rate=avg_rate,
                    remarks=f"ترحيل موجب إلى شهر {tm} (إضافة)",
                    client=selected_client,
                )
                messages.success(request, f"✅ تم ترحيل {egp:,.2f} EGP لحساب {selected_client.name} من شهر {sm} إلى شهر {tm}.")
                return redirect("carryover_balance")
    elif selected_client and source_year and source_month:
        sy = int(source_year)
        sm = int(source_month)
        tm = int(target_month) if target_month else (sm + 1 if sm < 12 else 1)
        carryover_exists = Capital.objects.filter(
            client=selected_client, in_type="ترحيل",
            date__year=sy, date__month=tm,
        ).exists()
        dep = Capital.objects.filter(client=selected_client).exclude(in_type="ترحيل").aggregate(total=Sum("cash_in"))["total"] or 0
        used = Database.objects.filter(from_source__from_field__iexact=selected_client.name).aggregate(total=Sum("transfered_amount"))["total"] or 0
        source_egp = dep - used
        lyd_cap = Capital.objects.filter(client=selected_client).exclude(in_type="ترحيل").aggregate(total=Sum("libyan_cash"))["total"] or 0
        avg_rate = round(dep / lyd_cap, 3) if lyd_cap else 1
        source_rate = avg_rate
        source_lyd = round(source_egp / source_rate, 2) if source_rate else 0

    return render(request, "carryover.html", {
        "title": "ترحيل الرصيد",
        "clients": clients,
        "selected_client": selected_client,
        "source_egp": source_egp,
        "source_lyd": source_lyd,
        "source_rate": source_rate,
        "carryover_exists": carryover_exists,
    })


def _next_internal_order():
    from datetime import date
    today = date.today()
    prefix = f"د-{today.strftime('%Y%m%d')}"
    last = InternalTransfer.objects.filter(order_number__startswith=prefix).order_by("-id").first()
    if last:
        seq = int(last.order_number[-3:]) + 1
    else:
        seq = 1
    return f"{prefix}-{seq:03d}"


@csrf_exempt
def add_internal_transfer(request):
    if request.method == "POST":
        form = InternalTransferForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.order_number = _next_internal_order()
            if not obj.date:
                obj.date = datetime.now().date()
            if not obj.time:
                obj.time = datetime.now().time()
            obj.save()
            messages.success(request, f"تم إرسال الحوالة داخلية #{obj.order_number} بنجاح")
            return redirect("internal_transfer_success", id=obj.id)
    else:
        form = InternalTransferForm(initial={
            "date": datetime.now().date(),
            "time": datetime.now().strftime("%H:%M"),
        })
    offices = OfficeName.objects.all()
    return render(request, "internal_transfer_form.html", {
        "form": form,
        "offices": offices,
        "title": "إرسال حوالة داخلية",
    })


def internal_transfer_success(request, id):
    obj = get_object_or_404(InternalTransfer, id=id)
    return render(request, "internal_transfer_success.html", {"t": obj})


def internal_transfer_list(request):
    user = get_current_user(request)
    q = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    status = request.GET.get("status", "").strip()

    qs = InternalTransfer.objects.all()
    if user and not user.is_admin and user.office_name:
        qs = qs.filter(
            models.Q(sender_office__office_name=user.office_name) |
            models.Q(receiver_office__office_name=user.office_name)
        )
    if q:
        qs = qs.filter(
            models.Q(order_number__icontains=q) |
            models.Q(sender_name__icontains=q) |
            models.Q(receiver_name__icontains=q) |
            models.Q(sender_tele__icontains=q) |
            models.Q(receiver_tele__icontains=q)
        )
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if status:
        qs = qs.filter(status=status)

    agg = qs.aggregate(
        total_sent=Sum("sent_amount"),
        total_commission=Sum("office_commission"),
        total_received=Sum("received_amount"),
        count=Count("id"),
    )

    return render(request, "internal_transfer_list.html", {
        "transfers": qs[:500],
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "selected_status": status,
        "total_sent": agg["total_sent"] or 0,
        "total_commission": agg["total_commission"] or 0,
        "total_received": agg["total_received"] or 0,
        "count": agg["count"] or 0,
        "title": "الحوالات الداخلية",
    })


def internal_transfer_receipt(request, id):
    obj = get_object_or_404(InternalTransfer, id=id)
    return render(request, "internal_transfer_receipt.html", {"t": obj})


@csrf_exempt
def bulk_delete_internal(request):
    if request.method == "POST":
        ids = request.POST.getlist("ids")
        if ids:
            InternalTransfer.objects.filter(id__in=ids).delete()
            messages.success(request, f"تم حذف {len(ids)} حوالة")
        else:
            InternalTransfer.objects.all().delete()
            messages.success(request, "تم حذف جميع الحوالات الداخلية")
    return redirect("internal_transfer_list")


@csrf_exempt
def quick_area(request):
    if request.method == "POST":
        import json
        data = json.loads(request.body)
        name = data.get("name", "").strip()
        if name:
            obj, created = DeliveryArea.objects.get_or_create(name=name)
            return JsonResponse({"id": obj.id, "name": obj.name, "created": created})
    elif request.method == "GET":
        areas = list(DeliveryArea.objects.values("id", "name"))
        return JsonResponse({"areas": areas})
    return JsonResponse({"error": "invalid"}, status=400)


@csrf_exempt
def update_internal_status(request, id):
    if request.method == "POST":
        import json
        data = json.loads(request.body)
        status = data.get("status", "")
        valid = ["pending", "sent", "received", "cancelled"]
        if status in valid:
            InternalTransfer.objects.filter(id=id).update(status=status)
            return JsonResponse({"ok": True, "status": status})
    return JsonResponse({"error": "invalid"}, status=400)


@login_required_custom
def users_list(request):
    user = get_current_user(request)
    if not user or not user.has_perm("perm_users"):
        messages.error(request, "ليس لديك صلاحية الوصول لهذه الصفحة")
        return redirect("home")
    users = SystemUser.objects.all().order_by("-is_admin", "username")
    return render(request, "users_list.html", {"users": users})


@login_required_custom
def add_user(request):
    user = get_current_user(request)
    if not user or not user.has_perm("perm_users"):
        messages.error(request, "ليس لديك صلاحية الوصول لهذه الصفحة")
        return redirect("home")
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        office_name = request.POST.get("office_name", "").strip()
        is_admin = request.POST.get("is_admin") == "1"
        if not username or not password:
            messages.error(request, "اسم المستخدم وكلمة المرور مطلوبان")
        elif SystemUser.objects.filter(username=username).exists():
            messages.error(request, "اسم المستخدم موجود بالفعل")
        else:
            new_user = SystemUser(
                username=username,
                office_name=office_name,
                is_admin=is_admin,
                perm_home=request.POST.get("perm_home") == "1",
                perm_transactions=request.POST.get("perm_transactions") == "1",
                perm_add_transaction=request.POST.get("perm_add_transaction") == "1",
                perm_edit_transaction=request.POST.get("perm_edit_transaction") == "1",
                perm_delete_transaction=request.POST.get("perm_delete_transaction") == "1",
                perm_capital=request.POST.get("perm_capital") == "1",
                perm_add_capital=request.POST.get("perm_add_capital") == "1",
                perm_edit_capital=request.POST.get("perm_edit_capital") == "1",
                perm_delete_capital=request.POST.get("perm_delete_capital") == "1",
                perm_expenses=request.POST.get("perm_expenses") == "1",
                perm_add_expense=request.POST.get("perm_add_expense") == "1",
                perm_edit_expense=request.POST.get("perm_edit_expense") == "1",
                perm_delete_expense=request.POST.get("perm_delete_expense") == "1",
                perm_import_whatsapp=request.POST.get("perm_import_whatsapp") == "1",
                perm_import_balance=request.POST.get("perm_import_balance") == "1",
                perm_internal_transfer=request.POST.get("perm_internal_transfer") == "1",
                perm_profits_report=request.POST.get("perm_profits_report") == "1",
                perm_users=request.POST.get("perm_users") == "1",
            )
            new_user.set_password(password)
            new_user.save()
            messages.success(request, f"✅ تم إضافة المستخدم {username} بنجاح")
            return redirect("users_list")
    return render(request, "user_form.html", {"mode": "add"})


@login_required_custom
def edit_user(request, id):
    current_user = get_current_user(request)
    if not current_user or not current_user.has_perm("perm_users"):
        messages.error(request, "ليس لديك صلاحية الوصول لهذه الصفحة")
        return redirect("home")
    user_obj = get_object_or_404(SystemUser, id=id)
    if request.method == "POST":
        user_obj.office_name = request.POST.get("office_name", "").strip()
        user_obj.is_admin = request.POST.get("is_admin") == "1"
        user_obj.perm_home = request.POST.get("perm_home") == "1"
        user_obj.perm_transactions = request.POST.get("perm_transactions") == "1"
        user_obj.perm_add_transaction = request.POST.get("perm_add_transaction") == "1"
        user_obj.perm_edit_transaction = request.POST.get("perm_edit_transaction") == "1"
        user_obj.perm_delete_transaction = request.POST.get("perm_delete_transaction") == "1"
        user_obj.perm_capital = request.POST.get("perm_capital") == "1"
        user_obj.perm_add_capital = request.POST.get("perm_add_capital") == "1"
        user_obj.perm_edit_capital = request.POST.get("perm_edit_capital") == "1"
        user_obj.perm_delete_capital = request.POST.get("perm_delete_capital") == "1"
        user_obj.perm_expenses = request.POST.get("perm_expenses") == "1"
        user_obj.perm_add_expense = request.POST.get("perm_add_expense") == "1"
        user_obj.perm_edit_expense = request.POST.get("perm_edit_expense") == "1"
        user_obj.perm_delete_expense = request.POST.get("perm_delete_expense") == "1"
        user_obj.perm_import_whatsapp = request.POST.get("perm_import_whatsapp") == "1"
        user_obj.perm_import_balance = request.POST.get("perm_import_balance") == "1"
        user_obj.perm_internal_transfer = request.POST.get("perm_internal_transfer") == "1"
        user_obj.perm_profits_report = request.POST.get("perm_profits_report") == "1"
        user_obj.perm_users = request.POST.get("perm_users") == "1"
        new_pass = request.POST.get("password", "").strip()
        if new_pass:
            user_obj.set_password(new_pass)
        user_obj.save()
        messages.success(request, f"✅ تم تعديل بيانات المستخدم {user_obj.username} بنجاح")
        return redirect("users_list")
    return render(request, "user_form.html", {"mode": "edit", "user_obj": user_obj})


@login_required_custom
def delete_user(request, id):
    current_user = get_current_user(request)
    if not current_user or not current_user.has_perm("perm_users"):
        messages.error(request, "ليس لديك صلاحية الوصول لهذه الصفحة")
        return redirect("home")
    user_obj = get_object_or_404(SystemUser, id=id)
    if user_obj.is_admin:
        messages.error(request, "لا يمكن حذف مدير النظام")
        return redirect("users_list")
    user_obj.delete()
    messages.success(request, f"تم حذف المستخدم {user_obj.username}")
    return redirect("users_list")


@login_required_custom
def messages_inbox(request):
    user = get_current_user(request)
    if not user:
        return redirect("login")
    from .models import Message
    msgs = Message.objects.filter(receiver=user).order_by("-created_at")
    unread_count = msgs.filter(is_read=False).count()
    return render(request, "messages_inbox.html", {
        "messages_list": msgs,
        "unread_count": unread_count,
    })


@login_required_custom
def messages_sent(request):
    user = get_current_user(request)
    if not user:
        return redirect("login")
    from .models import Message
    msgs = Message.objects.filter(sender=user).order_by("-created_at")
    return render(request, "messages_sent.html", {"messages_list": msgs})


@login_required_custom
def message_compose(request):
    user = get_current_user(request)
    if not user:
        return redirect("login")
    from .models import Message, SystemUser
    users = SystemUser.objects.exclude(id=user.id).order_by("username")
    if request.method == "POST":
        receiver_id = request.POST.get("receiver")
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        if receiver_id and subject and body:
            receiver = SystemUser.objects.filter(id=receiver_id).first()
            if receiver:
                Message.objects.create(sender=user, receiver=receiver, subject=subject, body=body)
                messages.success(request, f"تم إرسال الرسالة إلى {receiver.username}")
                return redirect("messages_sent")
            else:
                messages.error(request, "المستلم غير موجود")
        else:
            messages.error(request, "يرجى ملء جميع الحقول")
    return render(request, "message_compose.html", {"users": users})


@login_required_custom
def message_detail(request, id):
    user = get_current_user(request)
    if not user:
        return redirect("login")
    from .models import Message
    msg = get_object_or_404(Message, id=id)
    if msg.receiver != user and msg.sender != user:
        messages.error(request, "ليس لديك صلاحية لعرض هذه الرسالة")
        return redirect("messages_inbox")
    if msg.receiver == user and not msg.is_read:
        msg.is_read = True
        msg.save()
    if request.method == "POST" and msg.receiver == user:
        reply_text = request.POST.get("reply", "").strip()
        if reply_text:
            from django.utils import timezone
            msg.reply = reply_text
            msg.is_replied = True
            msg.reply_date = timezone.now()
            msg.save()
            messages.success(request, "تم إرسال الرد بنجاح")
            return redirect("messages_inbox")
    return render(request, "message_detail.html", {"msg": msg})


@login_required_custom
def message_delete(request, id):
    user = get_current_user(request)
    if not user:
        return redirect("login")
    from .models import Message
    msg = get_object_or_404(Message, id=id)
    if msg.sender == user or msg.receiver == user:
        msg.delete()
        messages.success(request, "تم حذف الرسالة")
    return redirect("messages_inbox")


@login_required_custom
def api_unread_count(request):
    user = get_current_user(request)
    if not user:
        return JsonResponse({"count": 0, "new_messages": []})
    from .models import Message
    unread = Message.objects.filter(receiver=user, is_read=False).order_by("-created_at")
    count = unread.count()
    new_msgs = []
    last_seen_id = request.session.get("last_seen_msg_id", 0)
    for m in unread:
        if m.id > last_seen_id:
            new_msgs.append({
                "id": m.id,
                "sender": m.sender.username,
                "subject": m.subject,
                "body": m.body[:100],
                "time": m.created_at.strftime("%H:%M"),
            })
    if unread.exists():
        request.session["last_seen_msg_id"] = unread.first().id
    return JsonResponse({"count": count, "new_messages": new_msgs})


@login_required_custom
def api_new_internal_transfers(request):
    user = get_current_user(request)
    if not user:
        return JsonResponse({"count": 0, "new_transfers": []})
    qs = InternalTransfer.objects.all()
    if not user.is_admin and user.office_name:
        qs = qs.filter(
            models.Q(sender_office__office_name=user.office_name) |
            models.Q(receiver_office__office_name=user.office_name)
        )
    new_transfers = []
    last_seen_id = request.session.get("last_seen_transfer_id", 0)
    recent = qs.filter(id__gt=last_seen_id).order_by("-id")[:10]
    for t in recent:
        if t.id > last_seen_id:
            office_name = ""
            if t.receiver_office and t.receiver_office.office_name == user.office_name:
                office_name = t.sender_office.office_name if t.sender_office else ""
                direction = "من"
            else:
                office_name = t.receiver_office.office_name if t.receiver_office else ""
                direction = "إلى"
            new_transfers.append({
                "id": t.id,
                "order_number": t.order_number,
                "sender_name": t.sender_name,
                "receiver_name": t.receiver_name,
                "sent_amount": t.sent_amount,
                "office_name": office_name,
                "direction": direction,
                "date": t.date.strftime("%Y-%m-%d"),
            })
    all_ids = list(qs.values_list("id", flat=True)[:100])
    if all_ids:
        request.session["last_seen_transfer_id"] = max(all_ids)
    return JsonResponse({"count": len(new_transfers), "new_transfers": new_transfers})


@login_required_custom
def export_internal_excel(request):
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="internal_transfers.xlsx"'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "الحوالات الداخلية"
    headers = ["رقم الحوالة", "التاريخ", "الوقت", "المرسل", "هاتف المرسل", "مكتب الإرسال", "المستلم", "هاتف المستلم", "مكتب الاستقبال", "المنطقة", "المبلغ المرسل", "العمولة", "المبلغ المستلم", "الحالة", "ملاحظات المرسل", "ملاحظات المستلم"]
    header_font = openpyxl.styles.Font(bold=True, color="FFFFFF", size=11)
    header_fill = openpyxl.styles.PatternFill(start_color="0d9488", end_color="0d9488", fill_type="solid")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = openpyxl.styles.Alignment(horizontal="center", vertical="center")
    rows = InternalTransfer.objects.all().order_by("-id")
    status_map = {"pending": "قيد الإرسال", "sent": "تم الإرسال", "received": "تم الاستلام", "cancelled": "ملغي"}
    for row_idx, t in enumerate(rows, 2):
        ws.cell(row=row_idx, column=1, value=t.order_number or "")
        ws.cell(row=row_idx, column=2, value=str(t.date) if t.date else "")
        ws.cell(row=row_idx, column=3, value=str(t.time) if t.time else "")
        ws.cell(row=row_idx, column=4, value=t.sender_name or "")
        ws.cell(row=row_idx, column=5, value=t.sender_tele or "")
        ws.cell(row=row_idx, column=6, value=t.sender_office.office_name if t.sender_office else "")
        ws.cell(row=row_idx, column=7, value=t.receiver_name or "")
        ws.cell(row=row_idx, column=8, value=t.receiver_tele or "")
        ws.cell(row=row_idx, column=9, value=t.receiver_office.office_name if t.receiver_office else "")
        ws.cell(row=row_idx, column=10, value=t.delivery_area_name or (t.delivery_area.name if t.delivery_area else ""))
        ws.cell(row=row_idx, column=11, value=t.sent_amount or 0)
        ws.cell(row=row_idx, column=12, value=t.office_commission or 0)
        ws.cell(row=row_idx, column=13, value=t.received_amount or 0)
        ws.cell(row=row_idx, column=14, value=status_map.get(t.status, t.status))
        ws.cell(row=row_idx, column=15, value=t.sender_notes or "")
        ws.cell(row=row_idx, column=16, value=t.receiver_notes or "")
        for col in [11, 12, 13]:
            ws.cell(row=row_idx, column=col).number_format = '#,##0.000'
    for i, w in enumerate([14, 12, 10, 16, 14, 16, 16, 14, 16, 16, 14, 12, 14, 12, 18, 18], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    wb.save(response)
    return response


@login_required_custom
def import_internal_excel(request):
    if request.method != "POST" or "excel_file" not in request.FILES:
        return redirect("internal_transfer_list")
    try:
        wb = openpyxl.load_workbook(request.FILES["excel_file"])
        ws = wb.active
        created = 0
        errors = []
        status_map = {"قيد الإرسال": "pending", "تم الإرسال": "sent", "تم الاستلام": "received", "ملغي": "cancelled", "pending": "pending", "sent": "sent", "received": "received", "cancelled": "cancelled"}
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
            try:
                if not row or not row[0]:
                    continue
                order_number = str(row[0] or "").strip()
                if not order_number:
                    errors.append(f"صف {row_idx}: رقم الحوالة فارغ")
                    continue
                if InternalTransfer.objects.filter(order_number=order_number).exists():
                    continue
                sender_office = None
                if row[5]:
                    sender_office, _ = OfficeName.objects.get_or_create(office_name=str(row[5]).strip())
                receiver_office = None
                if row[8]:
                    receiver_office, _ = OfficeName.objects.get_or_create(office_name=str(row[8]).strip())
                delivery_area = None
                delivery_area_name = str(row[9] or "").strip()
                if delivery_area_name:
                    delivery_area, _ = DeliveryArea.objects.get_or_create(name=delivery_area_name)
                status_str = str(row[13] or "قيد الإرسال").strip()
                status = status_map.get(status_str, "pending")
                sent = float(row[10] or 0)
                comm = float(row[11] or 0)
                received = float(row[12] or 0)
                if not received and sent and comm:
                    received = sent - comm
                InternalTransfer.objects.create(
                    order_number=order_number,
                    date=datetime.strptime(str(row[1]), "%Y-%m-%d").date() if row[1] else datetime.now().date(),
                    time=datetime.strptime(str(row[2]), "%H:%M:%S").time() if row[2] else datetime.now().time(),
                    sender_name=str(row[3] or ""),
                    sender_tele=str(row[4] or ""),
                    sender_office=sender_office,
                    receiver_name=str(row[6] or ""),
                    receiver_tele=str(row[7] or ""),
                    receiver_office=receiver_office,
                    delivery_area=delivery_area,
                    delivery_area_name=delivery_area_name if not delivery_area else "",
                    sent_amount=sent,
                    office_commission=comm,
                    received_amount=received,
                    status=status,
                    sender_notes=str(row[14] or ""),
                    receiver_notes=str(row[15] or ""),
                )
                created += 1
            except Exception as e:
                errors.append(f"صف {row_idx}: {e}")
        if created:
            messages.success(request, f"تم استيراد {created} حوالة داخلية من ملف Excel بنجاح.")
        for err in errors:
            messages.error(request, err)
    except Exception as e:
        messages.error(request, f"خطأ في قراءة الملف: {e}")
    return redirect("internal_transfer_list")


@login_required_custom
def export_expenses_excel(request):
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="expenses.xlsx"'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "المصروفات"
    headers = ["التاريخ", "نوع المصروف", "المبلغ", "المدفوع بواسطة", "ملاحظات"]
    header_font = openpyxl.styles.Font(bold=True, color="FFFFFF", size=11)
    header_fill = openpyxl.styles.PatternFill(start_color="d97706", end_color="d97706", fill_type="solid")
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = openpyxl.styles.Alignment(horizontal="center", vertical="center")
    rows = Expense.objects.all().order_by("-date", "-id")
    for row_idx, e in enumerate(rows, 2):
        ws.cell(row=row_idx, column=1, value=str(e.date) if e.date else "")
        ws.cell(row=row_idx, column=2, value=e.expense_type or "")
        ws.cell(row=row_idx, column=3, value=e.amount or 0)
        ws.cell(row=row_idx, column=4, value=e.paid_by or "")
        ws.cell(row=row_idx, column=5, value=e.notes or "")
        ws.cell(row=row_idx, column=3).number_format = '#,##0.000'
    ws.column_dimensions['A'].width = 14
    ws.column_dimensions['B'].width = 20
    ws.column_dimensions['C'].width = 16
    ws.column_dimensions['D'].width = 18
    ws.column_dimensions['E'].width = 24
    wb.save(response)
    return response


@login_required_custom
def import_expenses_excel(request):
    if request.method != "POST" or "excel_file" not in request.FILES:
        return redirect("expenses_list")
    try:
        wb = openpyxl.load_workbook(request.FILES["excel_file"])
        ws = wb.active
        created = 0
        errors = []
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
            try:
                if not row or not row[0]:
                    continue
                amount = float(row[2] or 0)
                if amount <= 0:
                    continue
                Expense.objects.create(
                    date=datetime.strptime(str(row[0]), "%Y-%m-%d").date() if row[0] else datetime.now().date(),
                    expense_type=str(row[1] or "أخرى").strip(),
                    amount=amount,
                    paid_by=str(row[3] or "").strip(),
                    notes=str(row[4] or "").strip(),
                )
                created += 1
            except Exception as e:
                errors.append(f"صف {row_idx}: {e}")
        if created:
            messages.success(request, f"تم استيراد {created} مصروف من ملف Excel بنجاح.")
        for err in errors:
            messages.error(request, err)
    except Exception as e:
        messages.error(request, f"خطأ في قراءة الملف: {e}")
    return redirect("expenses_list")


@login_required_custom
def toggle_maintenance(request):
    user = get_current_user(request)
    if not user or not user.is_admin:
        messages.error(request, "ليس لديك صلاحية")
        return redirect("home")
    if request.method == "POST":
        from .models import SystemSetting
        setting = SystemSetting.get()
        setting.maintenance_mode = not setting.maintenance_mode
        setting.save()
        status = "مفعل" if setting.maintenance_mode else "معطل"
        messages.success(request, f"تم {status} وضع الصيانة")
    return redirect("home")


@login_required_custom
def settings_page(request):
    user = get_current_user(request)
    if not user or not user.is_admin:
        messages.error(request, "ليس لديك صلاحية للوصول إلى الإعدادات")
        return redirect("home")

    from .models import (SystemSetting, DeliveryArea, OfficeName, FromSource,
                         TransferType, OrderType, BalanceType, CurrencyCapital, ClientBalance)
    setting = SystemSetting.get()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_maintenance":
            setting.maintenance_mode = request.POST.get("maintenance_mode") == "on"
            setting.maintenance_message = request.POST.get("maintenance_message", setting.maintenance_message)
            setting.save()
            messages.success(request, "تم حفظ إعدادات الصيانة")

        elif action == "add_area":
            name = request.POST.get("name", "").strip()
            if name:
                DeliveryArea.objects.get_or_create(name=name)
                messages.success(request, f"تم إضافة منطقة: {name}")
            else:
                messages.error(request, "أدخل اسم المنطقة")

        elif action == "delete_area":
            try:
                DeliveryArea.objects.filter(id=request.POST.get("item_id")).delete()
                messages.success(request, "تم حذف المنطقة")
            except Exception as e:
                messages.error(request, f"خطأ: {e}")

        elif action == "add_office":
            name = request.POST.get("name", "").strip()
            code = request.POST.get("code", "").strip()
            if name:
                OfficeName.objects.get_or_create(office_name=name, defaults={"code": code})
                messages.success(request, f"تم إضافة مكتب: {name}")
            else:
                messages.error(request, "أدخل اسم المكتب")

        elif action == "delete_office":
            try:
                OfficeName.objects.filter(id=request.POST.get("item_id")).delete()
                messages.success(request, "تم حذف المكتب")
            except Exception as e:
                messages.error(request, f"خطأ: {e}")

        elif action == "add_source":
            name = request.POST.get("name", "").strip()
            if name:
                FromSource.objects.get_or_create(from_field=name)
                messages.success(request, f"تم إضافة مصدر: {name}")
            else:
                messages.error(request, "أدخل اسم المصدر")

        elif action == "delete_source":
            try:
                FromSource.objects.filter(id=request.POST.get("item_id")).delete()
                messages.success(request, "تم حذف المصدر")
            except Exception as e:
                messages.error(request, f"خطأ: {e}")

        elif action == "add_transfer_type":
            name = request.POST.get("name", "").strip()
            if name:
                TransferType.objects.get_or_create(Transfer_type=name)
                messages.success(request, f"تم إضافة نوع: {name}")
            else:
                messages.error(request, "أدخل اسم النوع")

        elif action == "delete_transfer_type":
            try:
                TransferType.objects.filter(id=request.POST.get("item_id")).delete()
                messages.success(request, "تم حذف النوع")
            except Exception as e:
                messages.error(request, f"خطأ: {e}")

        elif action == "add_order_type":
            name = request.POST.get("name", "").strip()
            if name:
                OrderType.objects.get_or_create(order_type=name)
                messages.success(request, f"تم إضافة نوع طلب: {name}")
            else:
                messages.error(request, "أدخل اسم النوع")

        elif action == "delete_order_type":
            try:
                OrderType.objects.filter(id=request.POST.get("item_id")).delete()
                messages.success(request, "تم حذف نوع الطلب")
            except Exception as e:
                messages.error(request, f"خطأ: {e}")

        elif action == "add_balance_type":
            name = request.POST.get("name", "").strip()
            if name:
                BalanceType.objects.get_or_create(type=name)
                messages.success(request, f"تم إضافة نوع رصيد: {name}")
            else:
                messages.error(request, "أدخل اسم النوع")

        elif action == "delete_balance_type":
            try:
                BalanceType.objects.filter(id=request.POST.get("item_id")).delete()
                messages.success(request, "تم حذف نوع الرصيد")
            except Exception as e:
                messages.error(request, f"خطأ: {e}")

        elif action == "add_currency":
            name = request.POST.get("name", "").strip()
            if name:
                CurrencyCapital.objects.get_or_create(currency_type=name)
                messages.success(request, f"تم إضافة عملة: {name}")
            else:
                messages.error(request, "أدخل اسم العملة")

        elif action == "delete_currency":
            try:
                CurrencyCapital.objects.filter(id=request.POST.get("item_id")).delete()
                messages.success(request, "تم حذف العملة")
            except Exception as e:
                messages.error(request, f"خطأ: {e}")

        return redirect("settings_page")

    ctx = {
        "title": "الإعدادات",
        "setting": setting,
        "areas": list(DeliveryArea.objects.all()),
        "offices": list(OfficeName.objects.all()),
        "sources": list(FromSource.objects.all()),
        "transfer_types": list(TransferType.objects.all()),
        "order_types": list(OrderType.objects.all()),
        "balance_types": list(BalanceType.objects.all()),
        "currencies": list(CurrencyCapital.objects.all()),
    }
    return render(request, "settings.html", ctx)


@login_required_custom
def toggle_user_active(request, id):
    user = get_current_user(request)
    if not user or not user.is_admin:
        messages.error(request, "ليس لديك صلاحية")
        return redirect("home")
    target = get_object_or_404(SystemUser, id=id)
    if target.id == user.id:
        messages.error(request, "لا يمكن تعطيل حسابك الخاص")
        return redirect("users_list")
    if request.method == "POST":
        target.is_active = not target.is_active
        target.save()
        status = "تفعيل" if target.is_active else "تعطيل"
        messages.success(request, f"تم {status} حساب {target.username}")
    return redirect("users_list")


@login_required_custom
def import_alerts(request):
    user = get_current_user(request)
    alerts = ImportAlert.objects.all()
    if not user or not user.is_admin:
        alerts = alerts[:0]
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "mark_read":
            alert_id = request.POST.get("alert_id")
            if alert_id:
                ImportAlert.objects.filter(id=alert_id).update(is_read=True)
            return redirect("import_alerts")
        elif action == "mark_all_read":
            ImportAlert.objects.filter(is_read=False).update(is_read=True)
            messages.success(request, "تم تعيين الكل كمقروء")
            return redirect("import_alerts")
        elif action == "clear_all":
            ImportAlert.objects.all().delete()
            messages.success(request, "تم حذف جميع التنبيهات")
            return redirect("import_alerts")
    unread_count = ImportAlert.objects.filter(is_read=False).count()
    return render(request, "import_alerts.html", {
        "title": "تنبيهات الاستيراد",
        "alerts": alerts,
        "unread_count": unread_count,
    })


def smart_import(request):
    clients = ClientBalance.objects.all().order_by("name")
    from django.db.models import Sum
    db_count = Database.objects.count()
    db_total_egp = Database.objects.aggregate(s=Sum("transfered_amount"))["s"] or 0
    cap_count = Capital.objects.count()
    cap_total_egp = Capital.objects.aggregate(s=Sum("cash_in"))["s"] or 0
    all_alerts = ImportAlert.objects.all()[:50]
    return render(request, "smart_import.html", {
        "title": "استيراد ذكي",
        "clients": clients,
        "today": dt.now().date(),
        "db_count": db_count,
        "db_total_egp": db_total_egp,
        "cap_count": cap_count,
        "cap_total_egp": cap_total_egp,
        "all_alerts": all_alerts,
    })


def clear_all_alerts(request):
    if request.method != "POST":
        return redirect("smart_import")
    user = get_current_user(request)
    if not user or not user.is_admin:
        messages.error(request, "غير مصرح")
        return redirect("smart_import")
    ImportAlert.objects.all().delete()
    messages.success(request, "تم حذف جميع التنبيهات")
    return redirect("smart_import")


@csrf_exempt
def api_smart_detect(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    text = request.POST.get("text", "").strip()
    content_type = request.POST.get("type", "")
    if not text:
        return JsonResponse({"error": "no text"}, status=400)

    if content_type == "balance":
        parsed = parse_balance_lines(text)
        results = []
        for item in parsed:
            results.append({
                "egp": item.get("egp", 0),
                "rate": item.get("rate", 0),
                "lyd": item.get("lyd", 0),
            })
        return JsonResponse({"results": results, "type": "balance", "count": len(results)})
    else:
        parsed = parse_whatsapp_text(text)
        results = []
        for item in parsed:
            results.append({
                "receiver_tele": item.get("receiver_tele", ""),
                "transfer_type": item.get("transfer_type", "كاش"),
                "amount_egp": item.get("amount_egp", 0),
                "exchange_rate": item.get("exchange_rate", 0),
                "amount_lyd": item.get("amount_lyd", 0),
            })
        return JsonResponse({"results": results, "type": "transfer", "count": len(results)})



@csrf_exempt
def api_extension_receive(request):
    """API endpoint for Chrome extension to send extracted messages."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    text = data.get("text", "").strip()
    content_type = data.get("type", "auto")
    if not text:
        return JsonResponse({"error": "no text"}, status=400)

    if content_type == "auto":
        t = text.lower()
        balance_score = sum(3 if k in t else 0 for k in ["جنيه", "ج م", "القيمة", "القيمه"])
        balance_score += sum(2 if k in t else 0 for k in ["مصري", "سعر"])
        transfer_score = sum(3 if k in t else 0 for k in ["حوالة", "حواله", "فودافون", "فادفون", "فدفون", "تحويل بنكي"])
        transfer_score += sum(2 if k in t else 0 for k in ["كاش", "انستا", "صك"])
        if re.search(r'01\d{9}', text):
            transfer_score += 2
        content_type = "transfer" if transfer_score >= 2 else "balance" if balance_score >= 3 else "transfer"

    if content_type == "balance":
        parsed = parse_balance_lines(text)
        results = [{"egp": i.get("egp",0), "rate": i.get("rate",0), "lyd": i.get("lyd",0)} for i in parsed]
        return JsonResponse({"results": results, "type": "balance", "count": len(results)})
    else:
        parsed = parse_whatsapp_text(text)
        last_rate = None
        for item in parsed:
            if item.get("exchange_rate"):
                last_rate = item["exchange_rate"]
            elif last_rate:
                item["exchange_rate"] = last_rate
                if item.get("amount_egp") and last_rate > 0:
                    item["amount_lyd"] = round(item["amount_egp"] / last_rate, 2)
        results = [{
            "receiver_tele": i.get("receiver_tele",""),
            "transfer_type": i.get("transfer_type","كاش"),
            "amount_egp": i.get("amount_egp",0),
            "exchange_rate": i.get("exchange_rate",0),
            "amount_lyd": i.get("amount_lyd",0),
        } for i in parsed]
        return JsonResponse({"results": results, "type": "transfer", "count": len(results)})


@login_required_custom
def backups_list(request):
    user = get_current_user(request)
    if not user or not user.is_admin:
        messages.error(request, "ليس لديك صلاحية")
        return redirect("index")
    _ensure_backup_dir()
    files = []
    for fn in os.listdir(BACKUP_DIR):
        if fn.endswith('.csv'):
            fp = os.path.join(BACKUP_DIR, fn)
            stat = os.stat(fp)
            if fn.startswith("transactions"):
                ftype = "حوالات"
            elif fn.startswith("capital"):
                ftype = "أرصدة"
            elif fn.startswith("full_backup"):
                ftype = "full"
            else:
                ftype = "أخرى"
            files.append({
                "name": fn,
                "type": ftype,
                "size": f"{stat.st_size / 1024:.1f} KB",
                "date": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
    files.sort(key=lambda x: x["date"], reverse=True)
    return render(request, "backups_list.html", {
        "title": "النسخ الاحتياطية",
        "backups": files,
    })


@login_required_custom
def backup_now(request):
    user = get_current_user(request)
    if not user or not user.is_admin:
        messages.error(request, "ليس لديك صلاحية")
        return redirect("index")
    filename = _backup_all("manual")
    if filename:
        messages.success(request, f"✅ تم إنشاء نسخة احتياطية كاملة: {filename}")
    else:
        messages.error(request, "لا توجد بيانات للنسخ")
    return redirect("backups_list")


@login_required_custom
def backup_restore(request, filename):
    user = get_current_user(request)
    if not user or not user.is_admin:
        messages.error(request, "ليس لديك صلاحية")
        return redirect("index")
    filepath = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(filepath):
        messages.error(request, "الملف غير موجود")
        return redirect("backups_list")
    if filename.startswith("transactions"):
        count = _restore_transactions(filepath)
        messages.success(request, f"✅ تم استعادة {count} حوالة من النسخة الاحتياطية: {filename}")
        return redirect("transactions")
    elif filename.startswith("capital"):
        count = _restore_capital(filepath)
        messages.success(request, f"✅ تم استعادة {count} رصيد من النسخة الاحتياطية: {filename}")
        return redirect("capital_list")
    elif filename.startswith("full_backup"):
        count = _restore_all(filepath)
        messages.success(request, f"✅ تم استعادة {count} سجل من النسخة الاحتياطية الكاملة: {filename}")
        return redirect("backups_list")
    else:
        messages.error(request, "نوع ملف غير معروف")
        return redirect("backups_list")


@login_required_custom
def backup_download(request, filename):
    user = get_current_user(request)
    if not user or not user.is_admin:
        messages.error(request, "ليس لديك صلاحية")
        return redirect("index")
    filepath = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(filepath):
        messages.error(request, "الملف غير موجود")
        return redirect("backups_list")
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        response = HttpResponse(f.read(), content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


@login_required_custom
def backup_delete(request, filename):
    user = get_current_user(request)
    if not user or not user.is_admin:
        messages.error(request, "ليس لديك صلاحية")
        return redirect("index")
    filepath = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        messages.success(request, f"تم حذف النسخة الاحتياطية: {filename}")
    return redirect("backups_list")


@login_required_custom
def whatsapp_message_edit(request, msg_id):
    user = get_current_user(request)
    if not user:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "auth"}, status=401)
        return redirect("index")
    msg = get_object_or_404(WhatsAppMessage, id=msg_id)
    if request.method == "POST":
        old_amount, old_rate, old_commission, old_currency, old_phone, old_ttype = _parse_wa_message(msg.message_text)
        old_values = {
            "phone_number": msg.phone_number or "",
            "amount": ("" if msg.amount is None else str(msg.amount)),
            "exchange_rate": ("" if msg.exchange_rate is None else str(msg.exchange_rate)),
            "commission": ("" if msg.commission is None else str(msg.commission)),
            "currency": msg.currency or "",
            "transfer_type": msg.transfer_type or "",
            "direction": msg.direction or "",
        }
        edit_source = (request.POST.get("edit_source") or "").strip()
        if edit_source not in ("ai", "inline", "manual"):
            edit_source = "manual"
        msg.sender = request.POST.get("sender", msg.sender)
        posted_text = request.POST.get("message_text", None)
        if posted_text:  # لا يُمسح النص أبداً بقيمة فارغة (الذكاء الاصطناعي يرسل فارغاً)
            msg.message_text = posted_text
        msg.phone_number = request.POST.get("phone_number", "")
        msg.amount = request.POST.get("amount") or None
        msg.exchange_rate = request.POST.get("exchange_rate") or None
        msg.commission = request.POST.get("commission") or None
        cur = (request.POST.get("currency") or "").strip()
        cur = cur.replace("د.ل", "د ل").replace("د.ل", "د ل").replace("ج.م", "ج م").replace("ج.م", "ج م")
        if cur in ("د.ل", "d.ly", "LYD", "lyd"):
            cur = "د ل"
        elif cur in ("ج.م", "EGP", "egp", "EG", "eg"):
            cur = "ج م"
        msg.currency = cur
        msg.transfer_type = request.POST.get("transfer_type", msg.transfer_type)
        msg.direction = request.POST.get("direction", msg.direction)
        new_values = {
            "phone_number": msg.phone_number or "",
            "amount": ("" if msg.amount is None else str(msg.amount)),
            "exchange_rate": ("" if msg.exchange_rate is None else str(msg.exchange_rate)),
            "commission": ("" if msg.commission is None else str(msg.commission)),
            "currency": msg.currency or "",
            "transfer_type": msg.transfer_type or "",
            "direction": msg.direction or "",
        }
        field_labels = {
            "phone_number": "التليفون", "amount": "المبلغ", "exchange_rate": "السعر",
            "commission": "العمولة", "currency": "العملة", "transfer_type": "النوع", "direction": "الاتجاه",
        }
        changes = []
        for k, nv in new_values.items():
            if (nv or "") != (old_values.get(k) or ""):
                changes.append({"field": k, "label": field_labels.get(k, k), "old": old_values.get(k) or "—", "new": nv or "—"})
        if changes:
            msg.status = "معدلة"
            msg.edited_by = edit_source
            try:
                prev_log = json.loads(msg.edit_log) if msg.edit_log else []
            except Exception:
                prev_log = []
            prev_log.append({"source": edit_source, "at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"), "changes": changes})
            msg.edit_log = json.dumps(prev_log[-20:], ensure_ascii=False)
        msg.save()
        if (msg.amount and not old_amount) or (msg.exchange_rate and not old_rate) or \
           (msg.commission and not old_commission) or (msg.currency and not old_currency) or \
           (msg.phone_number and not old_phone):
            _save_parser_rule(
                msg.message_text,
                msg.amount or old_amount,
                msg.exchange_rate or old_rate,
                msg.commission or old_commission,
                msg.currency or old_currency,
                msg.phone_number or old_phone,
                msg.transfer_type,
            )
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": True})
        messages.success(request, "تم تعديل الرسالة")
        return redirect("whatsapp_integration")
    prefill = {
        "phone": request.GET.get("phone", ""),
        "amount": request.GET.get("amount", ""),
        "rate": request.GET.get("rate", ""),
        "commission": request.GET.get("commission", ""),
        "currency": request.GET.get("currency", ""),
    }
    return render(request, "whatsapp_message_edit.html", {"msg": msg, "prefill": prefill})


@login_required_custom
def whatsapp_message_delete(request, msg_id):
    user = get_current_user(request)
    if not user:
        return redirect("index")
    msg = get_object_or_404(WhatsAppMessage, id=msg_id)
    msg.delete()
    messages.success(request, "تم حذف الرسالة")
    return redirect("whatsapp_integration")


WA_ZAJEL_GROUP = "120363411662450255@g.us"
WA_STATEMENT_NAME = "مكتب الزاجل الجميل"
WA_STATEMENT_NO = "141"


def _ar_int_words(n):
    n = int(n)
    if n == 0:
        return "صفر"
    ones = ["", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة",
            "عشرة", "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر", "ستة عشر",
            "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
    tens = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
    hundreds = ["", "مائة", "مائتان", "ثلاثمائة", "أربعمائة", "خمسمائة", "ستمائة", "سبعمائة",
                "ثمانمائة", "تسعمائة"]

    def three(num):
        parts = []
        h, rest = divmod(num, 100)
        if h:
            parts.append(hundreds[h])
        if rest:
            if rest < 20:
                parts.append(ones[rest])
            else:
                t, o = divmod(rest, 10)
                if o:
                    parts.append(ones[o] + " و" + tens[t])
                else:
                    parts.append(tens[t])
        return " و".join(parts)

    groups = []
    millions, rem = divmod(n, 1000000)
    thousands, ones_part = divmod(rem, 1000)
    if millions:
        if millions == 1:
            groups.append("مليون")
        elif millions == 2:
            groups.append("مليونان")
        elif 3 <= millions <= 10:
            groups.append(_ar_int_words(millions) + " ملايين")
        else:
            groups.append(_ar_int_words(millions) + " مليوناً")
    if thousands:
        if thousands == 1:
            groups.append("ألف")
        elif thousands == 2:
            groups.append("ألفان")
        elif 3 <= thousands <= 10:
            groups.append(_ar_int_words(thousands) + " آلاف")
        else:
            groups.append(_ar_int_words(thousands) + " ألفاً")
    if ones_part:
        groups.append(three(ones_part))
    return " و".join(groups)


def _ar_amount_words(amount):
    amount = round(abs(float(amount)), 3)
    dinars = int(amount)
    piasters = int(round((amount - dinars) * 1000))
    parts = []
    if dinars or piasters == 0:
        parts.append(_ar_int_words(dinars) + " دينار ليبي")
    if piasters:
        parts.append(_ar_int_words(piasters) + " قرشاً")
    return " و".join(parts)


@login_required_custom
def whatsapp_settlement(request):
    user = get_current_user(request)
    if not user:
        return redirect("index")

    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    qs = WhatsAppMessage.objects.filter(group_jid=WA_ZAJEL_GROUP).order_by("timestamp", "id")
    if date_from:
        qs = qs.filter(timestamp__date__gte=date_from)
    if date_to:
        qs = qs.filter(timestamp__date__lte=date_to)

    st = SystemSetting.get()
    rate_timeline = _wa_rate_timeline()
    rows = []
    skipped_no_rate = 0
    credit_total = 0.0
    debit_total = 0.0
    running = 0.0

    for idx, msg in enumerate(qs, start=1):
        if not msg.amount:
            continue
        currency = (msg.currency or "").strip()
        rate = msg.exchange_rate
        if not rate and st.wa_rate_currency == currency:
            rate = _wa_rate_at(msg.timestamp, rate_timeline) or st.wa_current_rate

        if currency == "ج م":
            if not rate:
                skipped_no_rate += 1
                continue
            lyd = msg.amount / rate
            kind = "صادر: ليبي ← مصري" if msg.direction == "صادر" else "وارد: مصري ← ليبي"
        elif currency == "د ل":
            lyd = msg.amount
            kind = "صادر: ليبي محلي" if msg.direction == "صادر" else "وارد: ليبي محلي"
        else:
            lyd = msg.amount
            kind = "بدون عملة"

        credit = 0.0
        debit = 0.0
        if msg.direction == "وارد":
            debit = lyd
            debit_total += lyd
        else:
            credit = lyd
            credit_total += lyd

        # رصيد حساب الزاجل لدينا: وارد (أحيل إلينا) يزيد رصيده دائن · صادر (أرسلناه له) ينقصه إلى مدين
        running += debit - credit
        rows.append({
            "msg": msg,
            "ref": "%s-%s" % (WA_STATEMENT_NO, 67600 + idx),
            "kind": kind,
            "lyd": lyd,
            "credit": credit,
            "debit": debit,
            "balance": running,
        })

    balance = running
    if balance < -0.004:
        balance_kind = "مدين"
        balance_label = "الرصيد مدين بقيمة"
        balance_color = "#dc2626"
    elif balance > 0.004:
        balance_kind = "دائن"
        balance_label = "الرصيد دائن بقيمة"
        balance_color = "#16a34a"
    else:
        balance_kind = "متساوي"
        balance_label = "الرصيد متساوي (صفر)"
        balance_color = "#475569"

    return render(request, "whatsapp_settlement.html", {
        "title": "كشف حساب",
        "rows": rows,
        "credit_total": credit_total,
        "debit_total": debit_total,
        "balance": balance,
        "balance_kind": balance_kind,
        "balance_label": balance_label,
        "balance_color": balance_color,
        "balance_words": _ar_amount_words(balance),
        "date_from": date_from,
        "date_to": date_to,
        "skipped_no_rate": skipped_no_rate,
        "stmt_name": WA_STATEMENT_NAME,
        "stmt_no": WA_STATEMENT_NO,
        "row_count": len(rows),
    })


@login_required_custom
def whatsapp_messages_api(request):
    messages_list = WhatsAppMessage.objects.all().order_by("-timestamp", "-id")[:1000]
    return render(request, "whatsapp_messages_partial.html", {
        "messages_list": messages_list,
        "group_names": _wa_group_names(),
    })


def _is_pid_running(pid):
    if not pid:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle == 0:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


@login_required_custom
def whatsapp_integration(request):
    user = get_current_user(request)
    if not user:
        return redirect("index")
    messages_list = WhatsAppMessage.objects.all().order_by("-timestamp", "-id")[:1000]
    bot_status = "مفصول"
    pid_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "whatsapp-bot", "whatsapp_bot.pid")
    if os.path.exists(pid_path):
        try:
            with open(pid_path, "r") as f:
                pid = int(f.read().strip())
            if _is_pid_running(pid):
                bot_status = "متصل"
        except (ValueError, PermissionError):
            bot_status = "مفصول"
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "start_bot":
            pid_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "whatsapp-bot", "whatsapp_bot.pid")
            if os.path.exists(pid_path):
                try:
                    with open(pid_path, "r") as f:
                        pid = int(f.read().strip())
                    if _is_pid_running(pid):
                        messages.error(request, "البوت يعمل بالفعل")
                        return redirect("whatsapp_integration")
                except (ValueError, PermissionError):
                    pass
            try:
                env = os.environ.copy()
                if SystemSetting.get().sync_full_history:
                    env["SYNC_FULL_HISTORY"] = "1"
                else:
                    env.pop("SYNC_FULL_HISTORY", None)
                if SystemSetting.get().monitor_all_groups:
                    env["MONITOR_ALL"] = "1"
                else:
                    env.pop("MONITOR_ALL", None)
                subprocess.Popen(
                    ["node", "index.js"],
                    cwd=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "whatsapp-bot"),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    shell=True,
                    env=env,
                )
                messages.success(request, "تم تشغيل بوت الواتساب")
            except Exception as e:
                messages.error(request, f"خطأ في التشغيل: {str(e)}")
            return redirect("whatsapp_integration")
        elif action == "stop_bot":
            pid_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "whatsapp-bot", "whatsapp_bot.pid")
            if os.path.exists(pid_path):
                try:
                    with open(pid_path, "r") as f:
                        pid = int(f.read().strip())
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                    os.remove(pid_path)
                    messages.success(request, "تم إيقاف البوت")
                except Exception:
                    pass
            try:
                qr_state = WhatsAppQRState.get()
                qr_state.qr_code = None
                qr_state.status = "disconnected"
                qr_state.save()
            except Exception:
                pass
            return redirect("whatsapp_integration")
        elif action == "relink_bot":
            bot_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "whatsapp-bot")
            pid_path = os.path.join(bot_dir, "whatsapp_bot.pid")
            steps = []
            if os.path.exists(pid_path):
                try:
                    with open(pid_path, "r") as f:
                        pid = int(f.read().strip())
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                    os.remove(pid_path)
                    steps.append("تم إيقاف البوت")
                except Exception:
                    pass
            import shutil
            session_path = os.path.join(bot_dir, "auth_session")
            if os.path.exists(session_path):
                shutil.rmtree(session_path)
                steps.append("تم حذف الجلسة")
            try:
                qr_state = WhatsAppQRState.get()
                qr_state.qr_code = None
                qr_state.status = "waiting"
                qr_state.save()
                steps.append("تمت إعادة تعيين حالة QR")
            except Exception:
                pass
            try:
                env = os.environ.copy()
                if SystemSetting.get().sync_full_history:
                    env["SYNC_FULL_HISTORY"] = "1"
                else:
                    env.pop("SYNC_FULL_HISTORY", None)
                if SystemSetting.get().monitor_all_groups:
                    env["MONITOR_ALL"] = "1"
                else:
                    env.pop("MONITOR_ALL", None)
                log_path = os.path.join(bot_dir, "bot.log")
                log_f = open(log_path, "ab")
                subprocess.Popen(
                    ["node", "index.js"],
                    cwd=bot_dir,
                    stdout=log_f,
                    stderr=log_f,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    shell=True,
                    env=env,
                )
                steps.append("تم تشغيل البوت من جديد")
            except Exception as e:
                messages.error(request, f"خطأ في التشغيل: {str(e)}")
                return redirect("whatsapp_integration")
            messages.success(request, " | ".join(steps) + " — امسح رمز QR الجديد الآن")
            return redirect("whatsapp_integration")
        elif action == "reprocess":
            msg_id = request.POST.get("msg_id")
            if msg_id:
                msg = get_object_or_404(WhatsAppMessage, id=msg_id)
                if (msg.message_text or "").startswith("[صورة]"):
                    # صورة — لا تُحلَّل كحوالة: فتح التعديل بدون أي بيانات مقترحة
                    return redirect("whatsapp_message_edit", msg_id=msg.id)
                amount, rate, commission, currency, phone, _ = _parse_wa_message_learned(msg.message_text)
                from urllib.parse import urlencode
                params = urlencode({
                    "phone": phone or "",
                    "amount": amount or "",
                    "rate": rate or "",
                    "commission": commission or "",
                    "currency": currency or "",
                })
                return redirect(f"{reverse('whatsapp_message_edit', args=[msg.id])}?{params}")
            all_msgs = WhatsAppMessage.objects.all()
            fixed_count = 0
            st_rate = SystemSetting.get()
            rate_timeline = _wa_rate_timeline()

            def _recompute_direction(m):
                """الاتجاه حسب المرسل: صادر = حسابنا (fromMe) أو من قائمة المخرجين · وارد = كل غير ذلك."""
                if m.edited_by in ("manual", "ai", "inline"):
                    return m.direction
                # رسائل مزامنة التاريخ القديمة: غياب المرسل الفردي (sender = المجموعة) يعني من حسابنا (fromMe)
                if m.from_me or (m.sender and m.sender == m.group_jid) or st_rate.wa_is_outbound(m.sender_pn, m.sender):
                    return "صادر"
                return "وارد"

            for msg in all_msgs:
                if msg.edited_by == "not_rate":
                    continue
                # إعادة احتساب الاتجاه لجميع الرسائل (بما فيها الصور النظيفة) حسب المرسل
                direction_changed = False
                if msg.edited_by not in ("manual", "ai", "inline"):
                    new_dir = "صادر" if (msg.from_me or st_rate.wa_is_outbound(msg.sender_pn, msg.sender)) else "وارد"
                    if new_dir != msg.direction:
                        direction_changed = True
                        try:
                            prev_dir_log = json.loads(msg.edit_log) if msg.edit_log else []
                        except Exception:
                            prev_dir_log = []
                        prev_dir_log.append({"source": "reprocess", "at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"), "changes": [
                            {"field": "direction", "label": "الاتجاه", "old": msg.direction or "—", "new": new_dir},
                        ]})
                        msg.edit_log = json.dumps(prev_dir_log[-20:], ensure_ascii=False)
                        msg.direction = new_dir
                        msg.status = "معدلة"
                        msg.edited_by = "reprocess"
                        fixed_count += 1
                        msg.save()
                if (msg.message_text or "").startswith("[صورة]"):
                    # الصور تأكيد استلام فقط — أي مبلغ/رقم محفوظ عليها يُمسح (بوسترات السعر بلا مبلغ تبقى كما هي)
                    # التعديل اليدوي/الذكي الصريح من المستخدم لا يُلغى هنا
                    if msg.amount is None and not msg.phone_number:
                        continue
                    if msg.edited_by in ("manual", "ai", "inline"):
                        continue
                    old_values = {
                        "amount": ("" if msg.amount is None else str(msg.amount)),
                        "exchange_rate": ("" if msg.exchange_rate is None else str(msg.exchange_rate)),
                        "commission": ("" if msg.commission is None else str(msg.commission)),
                        "currency": msg.currency or "",
                        "phone_number": msg.phone_number or "",
                        "transfer_type": msg.transfer_type or "",
                    }
                    msg.amount = None
                    msg.exchange_rate = None
                    msg.commission = None
                    msg.currency = None
                    msg.phone_number = ""
                    new_values = {
                        "amount": "",
                        "exchange_rate": "",
                        "commission": "",
                        "currency": "",
                        "phone_number": "",
                        "transfer_type": msg.transfer_type or "",
                    }
                    field_labels = {
                        "amount": "المبلغ", "exchange_rate": "السعر", "commission": "العمولة",
                        "currency": "العملة", "phone_number": "التليفون", "transfer_type": "النوع",
                    }
                    changes = []
                    for k, nv in new_values.items():
                        if (nv or "") != (old_values.get(k) or ""):
                            changes.append({"field": k, "label": field_labels.get(k, k), "old": old_values.get(k) or "—", "new": nv or "—"})
                    if changes:
                        fixed_count += 1
                        msg.status = "معدلة"
                        msg.edited_by = "reprocess"
                        try:
                            prev_log = json.loads(msg.edit_log) if msg.edit_log else []
                        except Exception:
                            prev_log = []
                        prev_log.append({"source": "reprocess", "at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"), "changes": changes})
                        msg.edit_log = json.dumps(prev_log[-20:], ensure_ascii=False)
                        msg.save()
                    continue
                amount, rate, commission, currency, _, ttype = _parse_wa_message_learned(msg.message_text)
                if not rate and amount and currency and st_rate.wa_rate_currency == currency:
                    rate = _wa_rate_at(msg.timestamp, rate_timeline) or st_rate.wa_current_rate
                old_values = {
                    "amount": ("" if msg.amount is None else str(msg.amount)),
                    "exchange_rate": ("" if msg.exchange_rate is None else str(msg.exchange_rate)),
                    "commission": ("" if msg.commission is None else str(msg.commission)),
                    "currency": msg.currency or "",
                    "transfer_type": msg.transfer_type or "",
                    "direction": msg.direction or "",
                }
                msg.amount = amount
                msg.exchange_rate = rate
                msg.commission = commission
                msg.currency = currency
                msg.transfer_type = ttype
                msg.direction = _recompute_direction(msg)
                new_values = {
                    "amount": ("" if msg.amount is None else str(msg.amount)),
                    "exchange_rate": ("" if msg.exchange_rate is None else str(msg.exchange_rate)),
                    "commission": ("" if msg.commission is None else str(msg.commission)),
                    "currency": msg.currency or "",
                    "transfer_type": msg.transfer_type or "",
                    "direction": msg.direction or "",
                }
                field_labels = {
                    "amount": "المبلغ", "exchange_rate": "السعر", "commission": "العمولة",
                    "currency": "العملة", "transfer_type": "النوع", "direction": "الاتجاه",
                }
                changes = []
                for k, nv in new_values.items():
                    if (nv or "") != (old_values.get(k) or ""):
                        changes.append({"field": k, "label": field_labels.get(k, k), "old": old_values.get(k) or "—", "new": nv or "—"})
                if changes:
                    fixed_count += 1
                    msg.status = "معدلة"
                    msg.edited_by = "reprocess"
                    try:
                        prev_log = json.loads(msg.edit_log) if msg.edit_log else []
                    except Exception:
                        prev_log = []
                    prev_log.append({"source": "reprocess", "at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"), "changes": changes})
                    msg.edit_log = json.dumps(prev_log[-20:], ensure_ascii=False)
                msg.save()
            messages.success(request, f"تمت معالجة {all_msgs.count()} رسالة — عُدل منها {fixed_count}")
            return redirect("whatsapp_integration")
        elif action == "sync_messages":
            allow_dup = _as_bool(request.POST.get("allow_dup"), default=False)
            _write_sync_request({
                "mode": "all",
                "since": 0,
                "until": int(timezone.now().timestamp()),
                "allow_dup": allow_dup,
            })
            messages.success(
                request,
                "تم إرسال طلب سحب كل الرسائل السابقة "
                f"({'مع التكرار' if allow_dup else 'بدون تكرار'}) — تصل بتواريخها الأصلي"
            )
            return redirect(f"{reverse('whatsapp_integration')}?syncing=1")
        elif action == "sync_recent":
            last_msg = WhatsAppMessage.objects.order_by("-timestamp").first()
            if not last_msg:
                messages.error(request, "لا توجد رسائل سابقة — استخدم «سحب الكل» أولاً")
                return redirect("whatsapp_integration")
            if bot_status != "متصل":
                messages.error(request, "البوت غير مشغّل — اضغط تشغيل البوت ثم أعد المحاولة")
                return redirect("whatsapp_integration")
            # catchup: إعادة اتصال الواتساب — يرسل هو الرسائل الفائتة (offline buffer) تلقائيًا
            _write_sync_request({"mode": "catchup"})
            messages.success(
                request,
                f"تم إرسال طلب متابعة من آخر رسالة محفوظة "
                f"({last_msg.timestamp.strftime('%Y-%m-%d %H:%M')}) — "
                "ستُعاد إعادة اتصال الواتساب وتصل الرسائل الفائتة تلقائيًا"
            )
            return redirect(f"{reverse('whatsapp_integration')}?syncing=1")
        elif action == "sync_range":
            date_from = (request.POST.get("date_from") or "").strip()
            date_to = (request.POST.get("date_to") or "").strip()
            allow_dup = _as_bool(request.POST.get("allow_dup"), default=False)
            if not date_from or not date_to:
                messages.error(request, "يجب تحديد تاريخ البداية والنهاية")
                return redirect("whatsapp_integration")
            try:
                d1 = datetime.strptime(date_from, "%Y-%m-%d")
                d2 = datetime.strptime(date_to, "%Y-%m-%d")
            except ValueError:
                messages.error(request, "صيغة التاريخ غير صحيحة")
                return redirect("whatsapp_integration")
            if d2 < d1:
                messages.error(request, "تاريخ النهاية يجب أن يكون بعد تاريخ البداية")
                return redirect("whatsapp_integration")
            since_ts = int(d1.replace(tzinfo=WA_TZ).timestamp())
            until_ts = int(d2.replace(hour=23, minute=59, second=59, tzinfo=WA_TZ).timestamp())
            if until_ts > int(timezone.now().timestamp()) + 60:
                until_ts = int(timezone.now().timestamp())
            _write_sync_request({
                "mode": "range",
                "since": since_ts,
                "until": until_ts,
                "allow_dup": allow_dup,
            })
            messages.success(
                request,
                f"تم إرسال طلب سحب رسائل من {date_from} إلى {date_to} "
                f"({'مع التكرار' if allow_dup else 'بدون تكرار'})"
            )
            return redirect(f"{reverse('whatsapp_integration')}?syncing=1")
        elif action == "save_monitor_all":
            setting = SystemSetting.get()
            setting.monitor_all_groups = _as_bool(request.POST.get("monitor_all_groups"), default=False)
            setting.save()
            if setting.monitor_all_groups:
                messages.success(request, "تم تفعيل استقبال رسائل كل المجموعات")
            else:
                messages.success(request, "تم إلغاء استقبال كل المجموعات - سيتم الاعتماد على المجموعة المحفوظة")
            return redirect("whatsapp_integration")
        elif action == "save_group":
            group_jid = request.POST.get("group_jid", "").strip()
            setting = SystemSetting.get()
            group_jid = _normalize_group_jid(group_jid)
            setting.monitored_group_jid = group_jid
            setting.monitor_all_groups = False
            setting.save()
            messages.success(request, "تم حفظ معرف المجموعة")
            return redirect("whatsapp_integration")
        elif action == "save_groups_multi":
            raw = request.POST.get("group_jids", "")
            jids = []
            for part in raw.split(","):
                j = _normalize_group_jid(part.strip())
                if j and j not in jids:
                    jids.append(j)
            setting = SystemSetting.get()
            setting.monitored_group_jids = ",".join(jids)
            if jids:
                setting.monitored_group_jid = jids[0]
                setting.monitor_all_groups = False
            setting.save()
            messages.success(request, f"تم حفظ {len(jids)} غرفة للمراقبة")
            return redirect("whatsapp_integration")
        elif action == "save_full_history":
            setting = SystemSetting.get()
            setting.sync_full_history = _as_bool(request.POST.get("sync_full_history"), default=False)
            setting.save()
            if setting.sync_full_history:
                messages.success(request, "تم تفعيل مزامنة التاريخ الكامل - أوقف البوت ثم أعد تشغيله (ويفضل حذف الجلسة وإعادة الربط)")
            else:
                messages.success(request, "تم إلغاء مزامنة التاريخ الكامل")
            return redirect("whatsapp_integration")
        elif action == "save_outbound_senders":
            setting = SystemSetting.get()
            raw = (request.POST.get("wa_outbound_senders") or "").strip()
            # تنظيف: احتفظ بالأرقام والفاصلة/الفاصلة العربية فقط
            cleaned = re.sub(r"[^\d,،\s]", "", raw)
            parts = [p.strip() for p in re.split(r"[,،]", cleaned) if re.sub(r"\D", "", p)]
            setting.wa_outbound_senders = ",".join(parts)
            setting.save(update_fields=["wa_outbound_senders"])
            messages.success(request, f"تم حفظ {len(parts)} رقم كمخرج (صادر) — اضغط «المصحح» لإعادة احتساب اتجاه الرسائل القديمة")
            return redirect("whatsapp_integration")
        elif action == "delete_session":
            import shutil
            session_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "whatsapp-bot", "auth_session")
            if os.path.exists(session_path):
                shutil.rmtree(session_path)
                messages.success(request, "تم حذف الجلسة")
            else:
                messages.error(request, "لا توجد جلسة")
            try:
                qr_state = WhatsAppQRState.get()
                qr_state.qr_code = None
                qr_state.status = "waiting"
                qr_state.save()
            except Exception:
                pass
            return redirect("whatsapp_integration")
        elif action == "delete_all_messages":
            count = WhatsAppMessage.objects.count()
            WhatsAppMessage.objects.all().delete()
            messages.success(request, f"تم حذف جميع الرسائل ({count})")
            return redirect("whatsapp_integration")
        elif action == "delete_empty_messages":
            from django.db.models import Q as _Q
            ids_raw = (request.POST.get("delete_ids") or "").strip()
            ids = []
            if ids_raw:
                for part in ids_raw.split(","):
                    part = part.strip()
                    if part.isdigit():
                        ids.append(int(part))
            if ids:
                empty_qs = WhatsAppMessage.objects.filter(id__in=ids)
            else:
                # صور التأكيد لا تُحذف ضمنياً حتى بلا مبلغ/رقم — هي إثباتات استلام
                empty_qs = WhatsAppMessage.objects.filter(
                    _Q(amount__isnull=True) | _Q(amount=0),
                    exchange_rate__isnull=True,
                ).exclude(
                    _Q(message_text__startswith="[صورة]")
                ).filter(_Q(phone_number__isnull=True) | _Q(phone_number=""))
            deleted_count = empty_qs.count()
            empty_qs.delete()
            messages.success(request, f"تم حذف {deleted_count} رسالة بلا حوالة ولا رقم")
            return redirect("whatsapp_integration")
        elif action == "recompute_rates":
            st_rate = SystemSetting.get()
            rate_timeline = _wa_rate_timeline()
            checked = 0
            updated = 0
            for msg in WhatsAppMessage.objects.all().order_by("timestamp", "id"):
                if msg.amount is None:
                    continue
                if msg.edited_by and msg.edited_by not in ("reprocess", "timeline"):
                    continue
                checked += 1
                old_currency = (msg.currency or "").strip()
                _, parsed_rate, parsed_commission, parsed_currency, _, _ = _parse_wa_message_learned(msg.message_text)
                cur = old_currency
                cur_changed = False
                if not cur and parsed_currency:
                    msg.currency = parsed_currency
                    cur = parsed_currency
                    cur_changed = True
                if parsed_rate:
                    new_rate = parsed_rate
                elif cur == "د ل":
                    new_rate = None
                else:
                    new_rate = _wa_rate_at(msg.timestamp, rate_timeline)
                old_rate = msg.exchange_rate
                if (new_rate or 0) == (old_rate or 0) and not cur_changed:
                    continue
                updated += 1
                msg.exchange_rate = new_rate
                msg.edited_by = "timeline"
                try:
                    prev_log = json.loads(msg.edit_log) if msg.edit_log else []
                except Exception:
                    prev_log = []
                changes = []
                if cur_changed:
                    changes.append({"field": "currency", "label": "العملة", "old": old_currency or "—", "new": cur})
                if (new_rate or 0) != (old_rate or 0):
                    changes.append({
                        "field": "exchange_rate", "label": "السعر",
                        "old": ("—" if old_rate is None else str(old_rate)),
                        "new": ("—" if new_rate is None else str(new_rate)),
                    })
                prev_log.append({
                    "source": "rate_timeline",
                    "at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "changes": changes,
                })
                msg.edit_log = json.dumps(prev_log[-20:], ensure_ascii=False)
                fields = ["exchange_rate", "edited_by", "edit_log"]
                if cur_changed:
                    fields.append("currency")
                msg.save(update_fields=fields)
            if rate_timeline:
                last_ts, last_rate = rate_timeline[-1]
                st_rate.wa_current_rate = last_rate
                st_rate.wa_rate_currency = "ج م"
                st_rate.wa_rate_updated = last_ts
                st_rate.save(update_fields=["wa_current_rate", "wa_rate_currency", "wa_rate_updated"])
            messages.success(request, f"تمت إعادة احتساب الأسعار: فُحصت {checked} حوالة، عُدّل منها {updated} — أحداث السعر {len(rate_timeline)}")
            return redirect("whatsapp_integration")
        elif action == "exclude_rate_poster":
            msg = get_object_or_404(WhatsAppMessage, id=request.POST.get("msg_id"))
            old_rate = msg.exchange_rate
            msg.exchange_rate = None
            msg.edited_by = "not_rate"
            msg.status = "ملغية"
            try:
                prev_log = json.loads(msg.edit_log) if msg.edit_log else []
            except Exception:
                prev_log = []
            prev_log.append({
                "source": "exclude_rate_poster",
                "at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
                "changes": [{
                    "field": "exchange_rate", "label": "السعر",
                    "old": ("—" if old_rate is None else str(old_rate)), "new": "مستبعد من إعلانات السعر",
                }],
            })
            msg.edit_log = json.dumps(prev_log[-20:], ensure_ascii=False)
            msg.save(update_fields=["exchange_rate", "edited_by", "status", "edit_log"])
            messages.success(request, f"تم استبعاد الرسالة من إعلانات السعر (كان {old_rate or '—'}) — الحوالات بعدها تُحتسب على السعر السابق")
            return redirect("whatsapp_integration")
        elif action == "delete_inside_range":
            date_from = (request.POST.get("date_from") or "").strip()
            date_to = (request.POST.get("date_to") or "").strip()
            if not date_from or not date_to:
                messages.error(request, "يجب تحديد تاريخ البداية والنهاية أولاً (من فلاتر الشريط)")
                return redirect("whatsapp_integration")
            try:
                datetime.strptime(date_from, "%Y-%m-%d")
                datetime.strptime(date_to, "%Y-%m-%d")
            except ValueError:
                messages.error(request, "صيغة التاريخ غير صحيحة")
                return redirect("whatsapp_integration")
            if date_to < date_from:
                messages.error(request, "تاريخ النهاية يجب أن يكون بعد تاريخ البداية")
                return redirect("whatsapp_integration")
            qs_in = WhatsAppMessage.objects.filter(timestamp__date__gte=date_from, timestamp__date__lte=date_to)
            deleted_count = qs_in.count()
            qs_in.delete()
            messages.success(
                request,
                f"تم حذف {deleted_count} رسالة بين {date_from} و {date_to} — "
                "بقيت الرسائل خارج هذا المدى"
            )
            return redirect("whatsapp_integration")
    setting = SystemSetting.get()
    # عند التحويل بـ ?syncing=1 يعمل جافاسكريبت في المتصفح بجمع النتيجة — لا نستهلكها هنا
    if not request.GET.get("syncing"):
        result = _consume_sync_result()
        if result:
            level, text = _sync_result_message(result)
            if level == "success":
                messages.success(request, text)
            else:
                messages.error(request, text)
    return render(request, "whatsapp_integration.html", {
        "title": "ربط الواتساب",
        "messages_list": messages_list,
        "bot_status": bot_status,
        "monitored_group": setting.monitored_group_jid,
        "monitored_group_jids": setting.monitored_group_jids,
        "monitor_all_groups": setting.monitor_all_groups,
        "sync_full_history": setting.sync_full_history,
        "wa_current_rate": setting.wa_current_rate,
        "wa_rate_currency": setting.wa_rate_currency,
        "wa_rate_updated": setting.wa_rate_updated,
        "wa_outbound_senders": setting.wa_outbound_senders,
        "group_names": _wa_group_names(),
    })


@login_required_custom
def whatsapp_sync_status(request):
    """تستهلك نتيجة السحب من البوت وتعيدها JSON — يستخدمها جافاسكريبت بعد أزرار المزامنة."""
    result = _consume_sync_result()
    if not result:
        return JsonResponse({"done": False})
    level, text = _sync_result_message(result)
    return JsonResponse({"done": True, "ok": level == "success", "text": text})


@csrf_exempt
def whatsapp_config(request):
    setting = SystemSetting.get()
    return JsonResponse({
        "group_jid": setting.monitored_group_jid or "",
        "group_jids": setting.monitored_group_jids or "",
        "monitor_all": bool(setting.monitor_all_groups),
        "sync_full_history": bool(setting.sync_full_history),
    })


@csrf_exempt
def whatsapp_report_group(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            group_jid = _normalize_group_jid(data.get("group_jid", ""))
            if group_jid:
                setting = SystemSetting.get()
                setting.monitored_group_jid = group_jid
                setting.save()
                return JsonResponse({"ok": True, "group_jid": group_jid})
            return JsonResponse({"ok": False, "error": "no group_jid"})
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse({"ok": False, "error": "method not allowed"}, status=405)


@csrf_exempt
def whatsapp_ai_analyze(request):
    import re
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)
    try:
        data = json.loads(request.body)
        ids = data.get("ids") or []
        results = []
        for msg in WhatsAppMessage.objects.filter(id__in=ids):
            text = msg.message_text or ""
            if text.startswith("[صورة]"):
                # الصور تأكيد استلام فقط — لا تُعرض كحوالات في التحليل الذكي أبداً
                results.append({
                    "id": msg.id,
                    "text": text[:300],
                    "amount": None,
                    "rate": (msg.exchange_rate if msg.amount is None else None),
                    "currency": (msg.currency if msg.amount is None else None),
                    "phone": "",
                    "phone_fixed": False,
                    "phone_valid": False,
                    "ttype": msg.transfer_type,
                    "direction": msg.direction,
                    "is_transfer": False,
                    "is_image": True,
                    "missing_fields": [],
                })
                continue
            amount, rate, commission, currency, phone, ttype = _parse_wa_message_learned(text)
            phone_fixed = False
            phone_valid = False
            if phone:
                digits = re.sub(r'\D', '', phone)
                if len(digits) == 9 and digits.startswith('9'):
                    phone = '0' + digits
                    phone_fixed = True
                    phone_valid = True
                elif len(digits) == 10 and digits.startswith('1'):
                    phone = '0' + digits
                    phone_fixed = True
                    phone_valid = True
                elif len(digits) == 10 and digits.startswith('09'):
                    phone_valid = True
                elif len(digits) == 11 and digits.startswith('01'):
                    phone_valid = True
                else:
                    phone_valid = False
            missing = []
            if not amount:
                missing.append('amount')
            if not phone:
                missing.append('phone')
            elif not phone_valid:
                missing.append('phone')
            if not rate:
                missing.append('rate')
            if not currency:
                missing.append('currency')
            is_transfer = bool(amount and phone and phone_valid)
            results.append({
                "id": msg.id,
                "text": text[:300],
                "amount": amount,
                "rate": rate,
                "currency": currency,
                "phone": phone,
                "phone_fixed": phone_fixed,
                "phone_valid": phone_valid,
                "ttype": ttype,
                "direction": msg.direction,
                "is_transfer": is_transfer,
                "missing_fields": missing,
            })
        return JsonResponse({"ok": True, "results": results})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


_GROUPS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "whatsapp-bot", "groups.json")


def _wa_group_names():
    names = {}
    try:
        if os.path.exists(_GROUPS_FILE):
            with open(_GROUPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for g in data.get("groups") or []:
                gid = str(g.get("id") or "").strip()
                gname = str(g.get("name") or "").strip()
                if gid and gname:
                    names[gid] = gname
    except Exception:
        pass
    return names


@csrf_exempt
def whatsapp_groups(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            groups = data.get("groups") or []
            clean = []
            for g in groups:
                gid = _normalize_group_jid(str(g.get("id", "")))
                if gid:
                    clean.append({
                        "id": gid,
                        "name": str(g.get("name", ""))[:120],
                        "members": int(g.get("members") or 0),
                    })
            with open(_GROUPS_FILE, "w", encoding="utf-8") as f:
                json.dump({"groups": clean, "updated_at": int(timezone.now().timestamp())}, f, ensure_ascii=False)
            return JsonResponse({"ok": True, "count": len(clean)})
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
    groups = []
    updated_at = 0
    if os.path.exists(_GROUPS_FILE):
        try:
            with open(_GROUPS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            groups = d.get("groups") or []
            updated_at = int(d.get("updated_at") or 0)
        except Exception:
            pass
    return JsonResponse({"ok": True, "groups": groups, "updated_at": updated_at})


@csrf_exempt
def whatsapp_anchor(request):
    since = 0
    try:
        since = int(request.GET.get("since") or 0)
    except (TypeError, ValueError):
        since = 0
    qs = WhatsAppMessage.objects.exclude(wamsg_id="").order_by("timestamp")
    setting = SystemSetting.get()
    jids = [j for j in (setting.monitored_group_jids or "").split(",") if j.strip()]
    if not jids and setting.monitored_group_jid:
        jids = [setting.monitored_group_jid]
    if jids:
        qs = qs.filter(group_jid__in=jids)
    anchor = None
    covers_since = False
    if since:
        since_dt = datetime.fromtimestamp(since, WA_TZ).replace(tzinfo=None)
        anchor = qs.filter(timestamp__gte=since_dt).first()
        covers_since = anchor is not None
    if anchor is None:
        anchor = qs.last()
        covers_since = anchor is not None and _naive_to_unix(anchor.timestamp) >= since
    if anchor is None:
        return JsonResponse({"available": False, "has_wamsg": False})
    return JsonResponse({
        "available": True,
        "has_wamsg": True,
        "covers_since": covers_since,
        "id": anchor.wamsg_id,
        "from_me": bool(anchor.from_me),
        "ts": _naive_to_unix(anchor.timestamp),
    })


@csrf_exempt
def whatsapp_bulk_delete(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            ids = data.get("ids", [])
            if ids:
                deleted = WhatsAppMessage.objects.filter(id__in=ids).delete()
                return JsonResponse({"ok": True, "count": deleted[0]})
            return JsonResponse({"ok": False, "error": "no ids"})
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
    return JsonResponse({"ok": False, "error": "method not allowed"}, status=405)


@csrf_exempt
def whatsapp_qr_update(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            state = WhatsAppQRState.get()
            state.qr_code = data.get("qr")
            state.status = data.get("status", "disconnected")
            state.save()
            return JsonResponse({"ok": True})
        except Exception:
            return JsonResponse({"ok": False}, status=400)
    return JsonResponse({"ok": False}, status=405)


def whatsapp_qr_status(request):
    import qrcode
    import base64
    from io import BytesIO
    state = WhatsAppQRState.get()
    qr_data = state.qr_code
    if qr_data and state.status == "waiting":
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(qr_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        img_str = base64.b64encode(buffer.getvalue()).decode()
        return JsonResponse({"qr": img_str, "status": state.status})
    return JsonResponse({"qr": qr_data, "status": state.status})


@csrf_exempt
def whatsapp_webhook(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            text = data.get("message", "")
            sender = data.get("sender", "")
            jid = data.get("jid", "")
            wamsg_id = str(data.get("msg_id") or "").strip()
            allow_dup = _as_bool(data.get("allow_dup"), default=True)
            if not allow_dup and _wa_is_duplicate(wamsg_id, sender, jid, text):
                return JsonResponse({"status": "success", "saved": False, "duplicate": True})
            amount, rate, commission, currency, phone, ttype = _parse_wa_message_learned(text)
            from_image = _as_bool(data.get("from_image"), default=False)
            img_rate = img_currency = img_amount = img_phone = img_ttype = None
            if from_image:
                img_rate = data.get("image_rate")
                img_currency = data.get("image_currency")
                img_amount = data.get("image_amount")
                img_phone = data.get("image_phone")
                img_ttype = data.get("image_ttype")
                if img_currency and not currency:
                    cur = str(img_currency).strip()
                    if cur in ("ج م", "د ل"):
                        currency = cur
            st = SystemSetting.get()
            msg_ts = _wa_ts_to_naive(data.get("timestamp"))
            # الاتجاه حسب المرسل لا العملة: صادر = حسابنا (fromMe) أو من قائمة المخرجين · وارد = كل غير ذلك
            sender_pn = re.sub(r"\D", "", str(data.get("sender_pn") or ""))
            from_me = _as_bool(data.get("from_me"), default=False)
            if sender_pn and sender.endswith("@lid"):
                try:
                    lid_map = st.wa_lid_map_dict()
                    if lid_map.get(sender) != sender_pn:
                        lid_map[sender] = sender_pn
                        st.wa_lid_map = json.dumps(lid_map, ensure_ascii=False)
                        st.save(update_fields=["wa_lid_map"])
                except Exception:
                    pass
            direction = "صادر" if (from_me or st.wa_is_outbound(sender_pn, sender)) else "وارد"
            # الصور تأكيد استلام فقط — لا تُقرأ أي حوالة من أي صورة (ما عدا إعلان السعر بلا مبلغ/رقم)
            is_image_msg = from_image or str(text or "").startswith("[صورة]")
            if is_image_msg:
                if from_image and (img_rate or img_amount or img_phone):
                    # المرجع بيانات الذكاء الاصطناعي المنظمة لا نص الوصف
                    is_rate_poster = bool(img_rate and not img_amount and not img_phone)
                    poster_rate = img_rate
                else:
                    is_rate_poster = bool(rate and not amount and not phone)
                    poster_rate = rate
                if is_rate_poster:
                    try:
                        st.wa_current_rate = float(poster_rate)
                        st.wa_rate_currency = (str(img_currency).strip() if img_currency in ("ج م", "د ل") else (currency or "ج م"))
                        st.wa_rate_updated = timezone.now()
                        st.save(update_fields=["wa_current_rate", "wa_rate_currency", "wa_rate_updated"])
                    except (ValueError, TypeError):
                        pass
                    rate = float(poster_rate)
                else:
                    rate = None
                amount = None
                phone = ''
                commission = None
                ttype = ""
            elif rate and not amount and not phone and (not currency or currency == "ج م"):
                try:
                    st.wa_current_rate = float(rate)
                    st.wa_rate_currency = "ج م"
                    st.wa_rate_updated = msg_ts
                    st.save(update_fields=["wa_current_rate", "wa_rate_currency", "wa_rate_updated"])
                except (ValueError, TypeError):
                    pass
            elif not rate and amount and currency and st.wa_rate_currency == currency:
                rate = _wa_rate_at(msg_ts) or st.wa_current_rate
            WhatsAppMessage.objects.create(
                sender=sender,
                group_jid=jid,
                phone_number=phone,
                message_text=text,
                amount=amount,
                exchange_rate=rate,
                commission=commission,
                currency=currency,
                direction=direction,
                status=data.get("status", "فعالة"),
                transfer_type=ttype,
                wamsg_id=wamsg_id,
                timestamp=msg_ts,
                from_me=from_me,
                sender_pn=sender_pn,
            )
            return JsonResponse({"status": "success", "saved": True})
        except Exception:
            return JsonResponse({"status": "error"}, status=400)
    return JsonResponse({"status": "error"}, status=405)


_WA_SUMMARY_RE = re.compile(
    r'المطلوب|الإجمالي|الاجمالي|اجمالي|المجموع|كشف\s*حساب|كشف\s*الحساب|الرصيد\s*الكلي|الرصيد\s*الحالي|صافي\s*الرصيد|المنقول|المحول|المرحول|المتبقي|الباقي\s*عليه',
    re.IGNORECASE,
)
_WA_TRANSFER_ACTION_RE = re.compile(
    r'فودافون|فودافـون|انستا|استلا|حوالة|حواله|تسليم|استلام|شحن|كاش|محفظة|زاجل|الزاجل|وصلت|سلمت|أرسلت|ارسلت|استلمت|تحويل',
    re.IGNORECASE,
)


def _wa_is_summary_line(text):
    """سطور كشف حساب/أرصدة معلنة (مثل: المطلوب من الشركة مبلغ...) — ليست حوالة ولا تُدخل في الكشف."""
    if not text:
        return False
    if not _WA_SUMMARY_RE.search(text):
        return False
    t = re.sub(r'@\d{6,}', ' ', text)
    if re.search(r'(?<!\d)(01\d{9}|09\d{9}|0\d{9})(?!\d)', t):
        return False
    return not _WA_TRANSFER_ACTION_RE.search(text)


def _wa_is_question_line(text):
    """رسائل أسئلة مثل «اخر حوالة ... 10000 مصري على كم؟» — سؤال عن السعر لا حوالة حقيقية."""
    if not text:
        return False
    t = re.sub(r'@\d{6,}', ' ', text)
    if re.search(r'(?<!\d)(01\d{9}|09\d{9}|0\d{9})(?!\d)', t):
        return False
    return bool(re.search(
        r'على\s*كم|\bبكم\b|كم\s*؟|\bكام\b|سعرها\s*كم|السعر\s*كم|شو\s*سعر|ما\s*هو\s*السعر',
        t, re.IGNORECASE,
    ))


def _wa_direction(currency):
    """اتجاه الحوالة حسب العملة: د ل (من مكتبنا إلى مكتب الزاجل) = صادر/دائن · ج م = وارد/مدين."""
    return "صادر" if (currency or "").strip() == "د ل" else "وارد"


def _parse_wa_message(text):
    if not text:
        return None, None, None, None, "", "تحويل"
    # strip tashkeel (diacritics), tatweel, markdown decorations
    t = re.sub(r'[ً-ْٰـ]', '', text)
    if _wa_is_summary_line(t) or _wa_is_question_line(t):
        return None, None, None, None, "", "تحويل"
    t = t.replace('،', '.')
    t = re.sub(r'[*_☆★#`~^=|]', ' ', t)
    t = re.sub(r'\s+', ' ', t)

    amount = None
    exchange_rate = None
    commission = None
    currency = None
    phone_number = ""
    transfer_type = "تحويل"

    if re.search(r'ليبى|ليبي|دينار|LYD|د\.?\s?ل|🇱🇾', t, re.IGNORECASE):
        currency = "د ل"
    elif re.search(r'مصري|جنيه|EGP|ج\.?\s?م|🇪🇬|\bEG\b', t, re.IGNORECASE):
        currency = "ج م"

    if re.search(r'فودافون', t):
        transfer_type = "فودافون كاش"
    elif re.search(r'انستا', t, re.IGNORECASE):
        transfer_type = "انستا كاش"
    elif 'محفظة' in t:
        transfer_type = "محفظة"

    # strip mentions BEFORE phone extraction (mentions are not phones)
    t_nomention = re.sub(r'@\d{6,}', ' ', t)

    phone_matches = re.findall(r'(?<!\d)(01\d{9}|09\d{9}|0\d{9})(?!\d)', t_nomention)
    if phone_matches:
        phone_number = max(phone_matches, key=len)
        if len(phone_number) == 9:
            phone_number = '0' + phone_number

    # استنتاج العملة من دلائل القرينة حين لا تُذكر صراحة:
    # رقم مصري (01x) يثبت الحوالة لمصر · رقم ليبي (09x) يثبت الحوالة لليبيا · كلمات مصرية (فودافون/انستا) أو ليبية
    if not currency:
        if phone_number.startswith("01"):
            currency = "ج م"
        elif phone_number.startswith("09"):
            currency = "د ل"
        elif re.search(r'فودافون|فودافـون|انستا|instapay|امان', t, re.IGNORECASE):
            currency = "ج م"
        elif re.search(r'ليبيانا|البريد\s*الليبي|محفظة\s*ليبية|ليبي\s*كاش', t, re.IGNORECASE):
            currency = "د ل"

    cleaned = re.sub(r'(?<!\d)(01\d{9}|09\d{9}|0\d{9})(?!\d)', ' ', t_nomention)
    cleaned = re.sub(r'@\d{6,}', ' ', cleaned)

    def _num(s):
        s = s.strip().replace(',', '')
        if re.fullmatch(r'\d{1,3}\.\d{3}', s):
            return float(s.replace('.', ''))
        return float(s.replace(',', ''))

    for pat in (r'(?:القيمه|القيمة|المبلغ|مبلغ|بلغ)\s*:?\s*《?\s*([\d.,]+)',
                r'([\d.,]+)\s*(?:د\.?\s?ل|ج\.?\s?م|جنيه|دينار|LYD|EGP)'):
        m = re.search(pat, cleaned, re.IGNORECASE)
        if m:
            try:
                amount = _num(m.group(1))
                break
            except ValueError:
                pass

    if amount is None:
        for n in re.findall(r'\d[\d.,]*', cleaned):
            try:
                v = _num(n)
                digits = re.sub(r'\D', '', n)
                dec = re.search(r'[.,]\d{1,2}$', n)
                if dec:
                    continue
                if len(digits) >= 3 and v >= 100 and v < 1e12:
                    amount = v
                    break
            except ValueError:
                pass

    rm = re.search(r'(?:سعر\s*(?:الصرف)?|rate)\s*:?\s*(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+[.,]\d{1,2})', cleaned, re.IGNORECASE)
    if not rm:
        rm = re.search(r'(?<![\d.,])(\d+[.,]\d{1,2})(?![\d.,])', cleaned)
    if rm:
        try:
            exchange_rate = float(rm.group(1).replace(',', '.'))
        except ValueError:
            pass

    cm = re.search(r'عمول[هة]\s*:?\s*([\d.]+)', cleaned)
    if cm:
        try:
            commission = float(cm.group(1).replace(',', '.'))
        except ValueError:
            pass

    return amount, exchange_rate, commission, currency, phone_number, transfer_type


_PARSER_RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parser_rules.json")


def _norm_learn_key(text):
    import re
    if not text:
        return ""
    t = re.sub(r'[ً-ْٰـ]', '', text)
    t = re.sub(r'[*_☆★#`~^=|]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip().lower()
    return t


def _load_parser_rules():
    try:
        with open(_PARSER_RULES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_parser_rule(text, amount, rate, commission, currency, phone, ttype):
    key = _norm_learn_key(text)
    if not key or len(key) < 8:
        return
    rules = _load_parser_rules()
    entry = {}
    if amount not in (None, ""):
        entry["amount"] = float(amount)
    if rate not in (None, ""):
        entry["rate"] = float(rate)
    if commission not in (None, ""):
        entry["commission"] = float(commission)
    if currency:
        entry["currency"] = currency
    if phone:
        entry["phone"] = phone
    if ttype:
        entry["ttype"] = ttype
    if not entry:
        return
    rules[key] = entry
    with open(_PARSER_RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=0)


def _apply_learned_rule(text, amount, rate, commission, currency, phone, ttype):
    key = _norm_learn_key(text)
    if not key:
        return amount, rate, commission, currency, phone, ttype
    entry = _load_parser_rules().get(key)
    if not entry:
        return amount, rate, commission, currency, phone, ttype
    if amount in (None, "") and entry.get("amount") is not None:
        amount = entry["amount"]
    if rate in (None, "") and entry.get("rate") is not None:
        rate = entry["rate"]
    if commission in (None, "") and entry.get("commission") is not None:
        commission = entry["commission"]
    if not currency and entry.get("currency"):
        currency = entry["currency"]
    if not phone and entry.get("phone"):
        phone = entry["phone"]
    if ttype == "تحويل" and entry.get("ttype"):
        ttype = entry["ttype"]
    return amount, rate, commission, currency, phone, ttype


def _parse_wa_message_learned(text):
    amount, rate, commission, currency, phone, ttype = _parse_wa_message(text)
    return _apply_learned_rule(text, amount, rate, commission, currency, phone, ttype)
