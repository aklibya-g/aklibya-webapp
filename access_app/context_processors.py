from datetime import datetime
from django.db.models import Sum
from .models import Capital, Database, ClientBalance, SystemUser


def carryover_reminder(request):
    now = datetime.now()
    prev_year = now.year - 1 if now.month == 1 else now.year
    prev_month = 12 if now.month == 1 else now.month - 1

    dismissed = request.session.get("carryover_reminder_dismissed")
    if dismissed == f"{prev_year}-{prev_month}":
        return {"carryover_reminder": None}

    clients = ClientBalance.objects.all().order_by("name")
    warning_clients = []

    for client in clients:
        has_data = Capital.objects.filter(
            client=client, date__year=prev_year, date__month=prev_month
        ).exists()
        if not has_data:
            continue
        carryover_done = Capital.objects.filter(
            client=client, date__year=prev_year, date__month=prev_month, in_type="ترحيل"
        ).exists()
        if carryover_done:
            continue
        dep = Capital.objects.filter(client=client).exclude(in_type="ترحيل").aggregate(
            total=Sum("cash_in")
        )["total"] or 0
        used = Database.objects.filter(from_source__from_field=client.name).aggregate(
            total=Sum("transfered_amount")
        )["total"] or 0
        remaining = dep - used
        if remaining != 0:
            warning_clients.append({"name": client.name, "remaining_egp": remaining})

    if not warning_clients:
        return {"carryover_reminder": None}

    return {
        "carryover_reminder": {
            "year": prev_year,
            "month": prev_month,
            "clients": warning_clients,
            "text": f"لم يتم ترحيل أرصدة شهر {prev_month}/{prev_year} بعد!",
        }
    }


def current_user_processor(request):
    user_id = request.session.get("user_id")
    if user_id:
        try:
            user = SystemUser.objects.get(id=user_id)
            return {"current_user": user}
        except SystemUser.DoesNotExist:
            pass
    return {"current_user": None}


def maintenance_processor(request):
    from .models import SystemSetting
    setting = SystemSetting.get()
    return {"maintenance_mode": setting.maintenance_mode, "maintenance_message": setting.maintenance_message}
