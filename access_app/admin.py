from django.contrib import admin
from .models import Database, Capital, Expense, ClientBalance, FromSource, OfficeName, OrderType, TransferType, T1Summary, BalanceType, CurrencyCapital, Dbcash, InternalTransfer, DeliveryArea


@admin.register(Database)
class DatabaseAdmin(admin.ModelAdmin):
    list_display = ("id", "order_number", "sender_name", "receiver_name", "transfer_amount", "date")
    list_filter = ("office_name", "order_type", "transfer_type")
    search_fields = ("sender_name", "receiver_name", "order_number")


@admin.register(Capital)
class CapitalAdmin(admin.ModelAdmin):
    list_display = ("id", "cash_in", "libyan_cash", "date", "in_type", "client")
    list_filter = ("in_type",)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("id", "expense_type", "amount", "date", "paid_by")


@admin.register(ClientBalance)
class ClientBalanceAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "egp_balance", "lyd_balance")


@admin.register(FromSource)
class FromSourceAdmin(admin.ModelAdmin):
    list_display = ("id", "from_field")


@admin.register(OfficeName)
class OfficeNameAdmin(admin.ModelAdmin):
    list_display = ("id", "office_name")


@admin.register(OrderType)
class OrderTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "order_type")


@admin.register(TransferType)
class TransferTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "Transfer_type")


@admin.register(T1Summary)
class T1SummaryAdmin(admin.ModelAdmin):
    list_display = ("id", "date_range", "total_in", "total_out")


@admin.register(BalanceType)
class BalanceTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "type")


@admin.register(CurrencyCapital)
class CurrencyCapitalAdmin(admin.ModelAdmin):
    list_display = ("id", "currency_type")


@admin.register(Dbcash)
class DbcashAdmin(admin.ModelAdmin):
    list_display = ("id", "order_number", "sender_name", "receiver_name", "transfer_amount", "date")


@admin.register(InternalTransfer)
class InternalTransferAdmin(admin.ModelAdmin):
    list_display = ("id", "order_number", "sender_name", "receiver_name", "sent_amount", "office_commission", "received_amount", "status", "date")
    list_filter = ("status", "sender_office", "receiver_office")
    search_fields = ("order_number", "sender_name", "receiver_name", "sender_tele", "receiver_tele")


@admin.register(DeliveryArea)
class DeliveryAreaAdmin(admin.ModelAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)
