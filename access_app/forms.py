from django import forms
from .models import Database, Capital, ClientBalance, FromSource, TransferType, Expense, InternalTransfer, DeliveryArea, OfficeName

CITIES = [
    ("", "---------"),
    ("الإسكندرية", "الإسكندرية"),
    ("البحيرة", "البحيرة — دمنهور"),
    ("الغربية", "الغربية — طنطا"),
    ("الدقهلية", "الدقهلية — المنصورة"),
    ("الشرقية", "الشرقية — الزقازيق"),
    ("القليوبية", "القليوبية — بنها"),
    ("المنوفية", "المنوفية — شبين الكوم"),
    ("كفر الشيخ", "كفر الشيخ"),
    ("دمياط", "دمياط"),
    ("بورسعيد", "بورسعيد"),
    ("الإسماعيلية", "الإسماعيلية"),
    ("السويس", "السويس"),
    ("القاهرة", "القاهرة"),
    ("الجيزة", "الجيزة"),
    ("الفيوم", "الفيوم"),
    ("بني سويف", "بني سويف"),
    ("المنيا", "المنيا"),
    ("أسيوط", "أسيوط"),
    ("سوهاج", "سوهاج"),
    ("قنا", "قنا"),
    ("الأقصر", "الأقصر"),
    ("أسوان", "أسوان"),
    ("مطروح", "مطروح — مرسى مطروح"),
    ("الوادي الجديد", "الوادي الجديد — الخارجة"),
    ("شمال سيناء", "شمال سيناء — العريش"),
    ("جنوب سيناء", "جنوب سيناء — الطور"),
    ("البحر الأحمر", "البحر الأحمر — الغردقة"),
    ("مدينة نصر", "مدينة نصر"),
    ("شبرا الخيمة", "شبرا الخيمة"),
    ("المحلة الكبرى", "المحلة الكبرى"),
    ("كفر الدوار", "كفر الدوار"),
    ("العبور", "العبور"),
    ("الشروق", "الشروق"),
    ("السادس من أكتوبر", "السادس من أكتوبر"),
    ("العلمين الجديدة", "العلمين الجديدة"),
    ("المنصورة", "المنصورة"),
    ("طنطا", "طنطا"),
    ("الزقازيق", "الزقازيق"),
]

TRANSFER_CHOICES = [
    ("", "---------"),
    ("كاش", "كاش"),
    ("فودافون كاش", "فودافون كاش"),
    ("انستا باي", "انستا باي"),
]


class _BaseForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, f in self.fields.items():
            if isinstance(f.widget, forms.RadioSelect):
                continue
            is_select = isinstance(f.widget, forms.Select)
            current = f.widget.attrs.get("class", "")
            cls = "form-select form-select-sm" if is_select else "form-control form-control-sm"
            if current:
                current = current.replace("form-control", "").replace("form-select", "").strip()
            f.widget.attrs["class"] = (current + " " + cls).strip()


class DatabaseForm(_BaseForm):
    receiver_region = forms.ChoiceField(
        choices=CITIES, label="منطقة المستلم", required=False,
        widget=forms.Select()
    )
    transfer_type = forms.ChoiceField(
        choices=TRANSFER_CHOICES, label="طريقة التحويل", required=False,
        widget=forms.Select()
    )

    def clean_transfer_type(self):
        name = self.cleaned_data.get("transfer_type")
        if not name:
            return None
        obj, _ = TransferType.objects.get_or_create(Transfer_type=name)
        return obj

    class Meta:
        model = Database
        fields = "__all__"
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "time": forms.TimeInput(attrs={"type": "time"}),
            "remarks": forms.Textarea(attrs={"rows": 2, "style": "resize:none;"}),
        }


class CapitalDepositForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=ClientBalance.objects.all(),
        label="رصيد من",
        required=True,
        widget=forms.Select(attrs={"class": "form-control", "id": "id_client"}),
    )
    amount_egp = forms.FloatField(
        label="القيمة بالمصري",
        required=True,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "id": "dep_egp"}),
    )
    exchange_rate = forms.FloatField(
        label="سعر الصرف",
        required=True,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "id": "dep_rate"}),
    )
    amount_lyd = forms.FloatField(
        label="المبلغ الليبي",
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "readonly": True, "id": "dep_lyd"}),
    )
    date = forms.DateField(
        label="التاريخ",
        required=True,
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
    )
    notes = forms.CharField(
        label="ملاحظات",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "style": "resize:none;"}),
    )


class ExpenseForm(_BaseForm):
    class Meta:
        model = Expense
        fields = "__all__"
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2, "style": "resize:none;"}),
        }


class CapitalForm(_BaseForm):
    class Meta:
        model = Capital
        fields = "__all__"
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }


class InternalTransferForm(_BaseForm):
    delivery_area = forms.ModelChoiceField(
        queryset=DeliveryArea.objects.all(),
        label="منطقة التسليم",
        required=False,
        widget=forms.Select(attrs={"id": "id_delivery_area"}),
    )
    new_area = forms.CharField(
        label="إضافة منطقة جديدة",
        required=False,
        widget=forms.TextInput(attrs={"id": "id_new_area", "placeholder": "اسم المنطقة الجديدة..."}),
    )

    class Meta:
        model = InternalTransfer
        fields = ["sender_office", "sender_name", "sender_tele", "sender_notes",
                  "receiver_office", "receiver_name", "receiver_tele", "receiver_notes",
                  "delivery_area", "sent_amount", "office_commission", "status"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "time": forms.TimeInput(attrs={"type": "time"}),
            "sender_notes": forms.Textarea(attrs={"rows": 2, "style": "resize:none;"}),
            "receiver_notes": forms.Textarea(attrs={"rows": 2, "style": "resize:none;"}),
        }

    def clean(self):
        cleaned = super().clean()
        new_area = cleaned.get("new_area", "").strip()
        delivery_area = cleaned.get("delivery_area")
        if new_area and not delivery_area:
            obj, _ = DeliveryArea.objects.get_or_create(name=new_area)
            cleaned["delivery_area"] = obj
        if not delivery_area and not new_area:
            raise forms.ValidationError("يرجى اختيار منطقة التسليم أو إضافة منطقة جديدة")
        sent = cleaned.get("sent_amount", 0) or 0
        commission = cleaned.get("office_commission", 0) or 0
        cleaned["received_amount"] = sent - commission
        return cleaned
