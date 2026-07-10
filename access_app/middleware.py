from django.http import JsonResponse
from django.shortcuts import redirect
from django.contrib import messages


class CorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith('/api/'):
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type, X-CSRFToken"
        return response


class MaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST":
            from .models import SystemSetting, SystemUser
            setting = SystemSetting.get()
            if setting.maintenance_mode:
                user_id = request.session.get("user_id")
                is_admin = request.session.get("is_admin", False)
                if is_admin:
                    return self.get_response(request)
                if "/api/toggle-maintenance/" in request.path:
                    return self.get_response(request)
                if "/logout/" in request.path:
                    return self.get_response(request)
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return JsonResponse({"error": "النظام تحت الصيانة"}, status=503)
                messages.error(request, "⚠️ النظام تحت الصيانة حالياً. لا يمكن التعديل.")
                return redirect(request.path)
        return self.get_response(request)
