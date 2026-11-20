from django.urls import path
from endoreg_db.views.video.ai import label_list, add_label, delete_label, update_label

url_patterns = [
    # Label interface
    # GET, Returns list of labels
    path("ai/label-list", label_list, name="label-list"),
    # POST, updates label by deleting old one and adding new one
    path("ai/label-update", update_label, name="label-update"),
    # DELETE, deletes a label
    path("ai/label-delete", delete_label, name="label-delete"),
    # POST, allows adding a new label
    path("ai/label-add", add_label, name="label-add"),
]
