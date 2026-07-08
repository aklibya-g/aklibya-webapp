from django.db import models
from django.contrib.auth.hashers import make_password, check_password


class BalanceType(models.Model):
    type = models.CharField(max_length=100, verbose_name="نوع الرصيد")

    class Meta:
        verbose_name = "نوع رصيد"
        verbose_name_plural = "أنواع الرصيد"

    def __str__(self):
        return self.type or str(self.id)


class CurrencyCapital(models.Model):
    currency_type = models.CharField(max_length=100, verbose_name="نوع العملة")

    class Meta:
        verbose_name = "نوع عملة"
        verbose_name_plural = "أنواع العملات"

    def __str__(self):
        return self.currency_type or str(self.id)


class OfficeName(models.Model):
    office_name = models.CharField(max_length=200, verbose_name="اسم المكتب")
    code = models.CharField(max_length=10, verbose_name="الكود", blank=True, default="")

    class Meta:
        verbose_name = "مكتب"
        verbose_name_plural = "المكاتب"

    def __str__(self):
        return self.office_name or str(self.id)


class OrderType(models.Model):
    order_type = models.CharField(max_length=200, verbose_name="نوع الطلب")

    class Meta:
        verbose_name = "نوع طلب"
        verbose_name_plural = "أنواع الطلبات"

    def __str__(self):
        return self.order_type or str(self.id)


class TransferType(models.Model):
    Transfer_type = models.CharField(max_length=200, verbose_name="نوع التحويل")

    class Meta:
        verbose_name = "نوع تحويل"
        verbose_name_plural = "أنواع التحويل"

    def __str__(self):
        return self.Transfer_type or str(self.id)


class FromSource(models.Model):
    from_field = models.CharField(max_length=200, db_column="from", verbose_name="من")

    class Meta:
        verbose_name = "مصدر"
        verbose_name_plural = "المصادر"

    def __str__(self):
        return self.from_field or str(self.id)


class ClientBalance(models.Model):
    name = models.CharField(max_length=200, verbose_name="الاسم")
    phone = models.CharField(max_length=20, verbose_name="رقم الهاتف", blank=True, null=True)
    egp_balance = models.FloatField(verbose_name="الرصيد المصري", default=0)
    lyd_balance = models.FloatField(verbose_name="الرصيد الليبي", default=0)
    last_updated = models.DateTimeField(verbose_name="آخر تحديث", auto_now=True)

    class Meta:
        verbose_name = "رصيد عميل"
        verbose_name_plural = "أرصدة العملاء"

    def __str__(self):
        return self.name


class Capital(models.Model):
    cash_in = models.FloatField(verbose_name="وارد نقدي", null=True, blank=True)
    libyan_cash = models.FloatField(verbose_name="نقدي ليبي", null=True, blank=True)
    date = models.DateField(verbose_name="التاريخ", null=True, blank=True)
    in_type = models.CharField(max_length=200, verbose_name="نوع الإيداع", null=True, blank=True)
    from_source = models.ForeignKey(FromSource, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="من")
    exchange_rate = models.FloatField(verbose_name="سعر الصرف", null=True, blank=True)
    balance_type = models.ForeignKey(BalanceType, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="نوع الرصيد")
    remarks = models.TextField(verbose_name="ملاحظات", null=True, blank=True)
    client = models.ForeignKey("ClientBalance", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="رصيد من")

    class Meta:
        verbose_name = "رصيد"
        verbose_name_plural = "الأرصدة"

    def __str__(self):
        return f"رصيد #{self.id} - {self.client.name if self.client else '-'}"


class Database(models.Model):
    transfered_amount = models.FloatField(verbose_name="المبلغ المحول", null=True, blank=True)
    date = models.DateField(verbose_name="التاريخ", null=True, blank=True)
    time = models.TimeField(verbose_name="الوقت", null=True, blank=True)
    office_name = models.ForeignKey(OfficeName, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="اسم المكتب")
    order_number = models.CharField(max_length=200, verbose_name="رقم الطلب", null=True, blank=True)
    order_type = models.ForeignKey(OrderType, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="نوع الطلب")
    sender_name = models.CharField(max_length=200, verbose_name="اسم المرسل", null=True, blank=True)
    sender_tele = models.CharField(max_length=200, verbose_name="هاتف المرسل", null=True, blank=True)
    receiver_region = models.CharField(max_length=200, verbose_name="منطقة المستلم", null=True, blank=True)
    transfer_type = models.ForeignKey(TransferType, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="نوع التحويل")
    transfer_amount = models.FloatField(verbose_name="مبلغ التحويل", null=True, blank=True)
    exchange_rate = models.FloatField(verbose_name="سعر الصرف", null=True, blank=True)
    remarks = models.TextField(verbose_name="ملاحظات", null=True, blank=True)
    receiver_name = models.CharField(max_length=200, verbose_name="اسم المستلم", null=True, blank=True)
    receiver_tele = models.CharField(max_length=200, verbose_name="هاتف المستلم", null=True, blank=True)
    from_source = models.ForeignKey(FromSource, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="من")

    class Meta:
        verbose_name = "عملية"
        verbose_name_plural = "العمليات"

    def __str__(self):
        return f"عملية #{self.id}"


class Dbcash(models.Model):
    transfered_amount = models.FloatField(verbose_name="المبلغ المحول", null=True, blank=True)
    date = models.DateTimeField(verbose_name="التاريخ", null=True, blank=True)
    time = models.DateTimeField(verbose_name="الوقت", null=True, blank=True)
    office_name = models.ForeignKey(OfficeName, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="اسم المكتب")
    order_number = models.CharField(max_length=200, verbose_name="رقم الطلب", null=True, blank=True)
    order_type = models.ForeignKey(OrderType, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="نوع الطلب")
    sender_name = models.CharField(max_length=200, verbose_name="اسم المرسل", null=True, blank=True)
    sender_tele = models.CharField(max_length=200, verbose_name="هاتف المرسل", null=True, blank=True)
    receiver_region = models.CharField(max_length=200, verbose_name="منطقة المستلم", null=True, blank=True)
    transfer_type = models.ForeignKey(TransferType, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="نوع التحويل")
    transfer_amount = models.FloatField(verbose_name="مبلغ التحويل", null=True, blank=True)
    exchange_rate = models.FloatField(verbose_name="سعر الصرف", null=True, blank=True)
    remarks = models.TextField(verbose_name="ملاحظات", null=True, blank=True)
    receiver_name = models.CharField(max_length=200, verbose_name="اسم المستلم", null=True, blank=True)
    receiver_tele = models.CharField(max_length=200, verbose_name="هاتف المستلم", null=True, blank=True)
    from_source = models.ForeignKey(FromSource, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="من")

    class Meta:
        verbose_name = "نقدية"
        verbose_name_plural = "النقدية"

    def __str__(self):
        return f"نقدية #{self.id}"


class T1Summary(models.Model):
    date_range = models.CharField(max_length=200, verbose_name="الفترة", null=True, blank=True)
    total_in = models.FloatField(verbose_name="إجمالي الوارد", null=True, blank=True)
    total_out = models.FloatField(verbose_name="إجمالي المنصرف", null=True, blank=True)
    exchange_rate = models.FloatField(verbose_name="سعر الصرف", null=True, blank=True)
    profit = models.FloatField(verbose_name="الربح", null=True, blank=True)
    profit_rate = models.FloatField(verbose_name="نسبة الربح", null=True, blank=True)
    net_profit_lira = models.FloatField(verbose_name="صافي الربح ليرة", null=True, blank=True)
    net_profit_dollar = models.FloatField(verbose_name="صافي الربح دولار", null=True, blank=True)
    total_lira = models.FloatField(verbose_name="إجمالي ليرة", null=True, blank=True)
    total_dollar = models.FloatField(verbose_name="إجمالي دولار", null=True, blank=True)

    class Meta:
        verbose_name = "ملخص"
        verbose_name_plural = "ملخصات"

    def __str__(self):
        return f"ملخص #{self.id}"


class Expense(models.Model):
    date = models.DateField(verbose_name="التاريخ", null=True, blank=True)
    amount = models.FloatField(verbose_name="المبلغ", null=True, blank=True)
    expense_type = models.CharField(max_length=200, verbose_name="نوع المصروف", null=True, blank=True)
    notes = models.TextField(verbose_name="ملاحظات", null=True, blank=True)
    paid_by = models.CharField(max_length=200, verbose_name="مدفوع بواسطة", null=True, blank=True)

    class Meta:
        verbose_name = "مصروف"
        verbose_name_plural = "المصروفات"

    def __str__(self):
        return f"مصروف #{self.id}"


class DeliveryArea(models.Model):
    name = models.CharField(max_length=200, unique=True, verbose_name="اسم المنطقة")

    class Meta:
        verbose_name = "منطقة تسليم"
        verbose_name_plural = "مناطق التسليم"
        ordering = ["name"]

    def __str__(self):
        return self.name or str(self.id)


class InternalTransfer(models.Model):
    order_number = models.CharField(max_length=50, verbose_name="رقم الحوالة", unique=True)
    date = models.DateField(verbose_name="التاريخ")
    time = models.TimeField(verbose_name="الوقت")

    sender_office = models.ForeignKey(OfficeName, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="مكتب الإرسال", related_name="internal_sent")
    sender_name = models.CharField(max_length=200, verbose_name="اسم المرسل")
    sender_tele = models.CharField(max_length=20, verbose_name="هاتف المرسل", blank=True)
    sender_notes = models.TextField(verbose_name="ملاحظات المرسل", blank=True)

    receiver_office = models.ForeignKey(OfficeName, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="مكتب الاستقبال", related_name="internal_received")
    receiver_name = models.CharField(max_length=200, verbose_name="اسم المستلم")
    receiver_tele = models.CharField(max_length=20, verbose_name="هاتف المستلم", blank=True)
    receiver_notes = models.TextField(verbose_name="ملاحظات المستلم", blank=True)

    delivery_area = models.ForeignKey(DeliveryArea, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="منطقة التسليم")
    delivery_area_name = models.CharField(max_length=200, verbose_name="منطقة التسليم (نص)", blank=True)

    sent_amount = models.FloatField(verbose_name="المبلغ المرسل", default=0)
    office_commission = models.FloatField(verbose_name="عمولة المكتب", default=0)
    received_amount = models.FloatField(verbose_name="المبلغ المستلم", default=0)

    status = models.CharField(max_length=20, verbose_name="الحالة", choices=[
        ("pending", "قيد الإرسال"),
        ("sent", "تم الإرسال"),
        ("received", "تم الاستلام"),
        ("cancelled", "ملغي"),
    ], default="pending")

    class Meta:
        verbose_name = "حوالة داخلية"
        verbose_name_plural = "الحوالات الداخلية"
        ordering = ["-id"]

    def __str__(self):
        return f"حوالة داخلية #{self.order_number}"

    def save(self, *args, **kwargs):
        if not self.received_amount and self.sent_amount and self.office_commission:
            self.received_amount = self.sent_amount - self.office_commission
        super().save(*args, **kwargs)


class SystemUser(models.Model):
    username = models.CharField(max_length=100, unique=True, verbose_name="اسم المستخدم")
    password = models.CharField(max_length=255, verbose_name="كلمة المرور")
    office_name = models.CharField(max_length=200, verbose_name="اسم المكتب", blank=True, default="")
    is_admin = models.BooleanField(default=False, verbose_name="مدير النظام")
    is_active = models.BooleanField(default=True, verbose_name="الحساب مفعل")

    perm_home = models.BooleanField(default=True, verbose_name="الرئيسية")
    perm_transactions = models.BooleanField(default=True, verbose_name="عرض الحوالات")
    perm_add_transaction = models.BooleanField(default=True, verbose_name="إضافة حوالة")
    perm_edit_transaction = models.BooleanField(default=True, verbose_name="تعديل حوالة")
    perm_delete_transaction = models.BooleanField(default=False, verbose_name="حذف حوالة")
    perm_capital = models.BooleanField(default=True, verbose_name="عرض الأرصدة")
    perm_add_capital = models.BooleanField(default=True, verbose_name="إضافة رصيد")
    perm_edit_capital = models.BooleanField(default=True, verbose_name="تعديل رصيد")
    perm_delete_capital = models.BooleanField(default=False, verbose_name="حذف رصيد")
    perm_expenses = models.BooleanField(default=True, verbose_name="عرض المصروفات")
    perm_add_expense = models.BooleanField(default=True, verbose_name="إضافة مصروف")
    perm_edit_expense = models.BooleanField(default=True, verbose_name="تعديل مصروف")
    perm_delete_expense = models.BooleanField(default=False, verbose_name="حذف مصروف")
    perm_import_whatsapp = models.BooleanField(default=True, verbose_name="استيراد من واتساب")
    perm_import_balance = models.BooleanField(default=True, verbose_name="استيراد أرصدة")
    perm_internal_transfer = models.BooleanField(default=True, verbose_name="الحوالات الداخلية")
    perm_profits_report = models.BooleanField(default=True, verbose_name="تقرير الأرباح")
    perm_users = models.BooleanField(default=False, verbose_name="إدارة المستخدمين")

    class Meta:
        verbose_name = "مستخدم"
        verbose_name_plural = "المستخدمون"

    def __str__(self):
        return self.username

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    def has_perm(self, perm_name):
        if self.is_admin:
            return True
        return getattr(self, perm_name, False)


class Message(models.Model):
    sender = models.ForeignKey(SystemUser, on_delete=models.CASCADE, related_name="sent_messages", verbose_name="المرسل")
    receiver = models.ForeignKey(SystemUser, on_delete=models.CASCADE, related_name="received_messages", verbose_name="المستلم")
    subject = models.CharField(max_length=300, verbose_name="الموضوع")
    body = models.TextField(verbose_name="الرسالة")
    is_read = models.BooleanField(default=False, verbose_name="مقروءة")
    reply = models.TextField(blank=True, default="", verbose_name="الرد")
    reply_date = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الرد")
    is_replied = models.BooleanField(default=False, verbose_name="تم الرد")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإرسال")

    class Meta:
        verbose_name = "رسالة"
        verbose_name_plural = "الرسائل"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.sender.username} -> {self.receiver.username}: {self.subject}"


class SystemSetting(models.Model):
    maintenance_mode = models.BooleanField(default=False, verbose_name="وضع الصيانة")
    maintenance_message = models.TextField(default="النظام تحت الصيانة حالياً. يرجى المحاولة لاحقاً.", verbose_name="رسالة الصيانة")

    class Meta:
        verbose_name = "إعدادات النظام"
        verbose_name_plural = "إعدادات النظام"

    def __str__(self):
        return "إعدادات النظام"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        return obj
