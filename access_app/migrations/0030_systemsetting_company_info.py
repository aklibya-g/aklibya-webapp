from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("access_app", "0029_systemsetting_gemini_api_key")]

    operations = [
        migrations.AddField(model_name="systemsetting", name="company_name", field=models.CharField(default="شركة العربي للحوالات المالية", blank=True, max_length=200, verbose_name="اسم الشركة")),
        migrations.AddField(model_name="systemsetting", name="company_address", field=models.CharField(default="", blank=True, max_length=300, verbose_name="عنوان الشركة")),
        migrations.AddField(model_name="systemsetting", name="company_phone", field=models.CharField(default="", blank=True, max_length=100, verbose_name="هاتف الشركة")),
        migrations.AddField(model_name="systemsetting", name="company_logo", field=models.ImageField(blank=True, null=True, upload_to="company/", verbose_name="شعار الشركة")),
    ]
