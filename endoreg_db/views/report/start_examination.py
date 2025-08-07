from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required

@staff_member_required
def start_examination(request):
    return render(request, 'admin/start_examination.html')

