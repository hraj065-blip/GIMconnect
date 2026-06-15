from django.contrib import admin
from django.utils.html import format_html
from django.urls import path, reverse
from django.shortcuts import redirect
from .models import (
    User, Connection, ConnectionRequest, Message, RevealRequest, Block, EmailOTP, Report
)

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    # Determine what columns show up on the main user list
    list_display = ('email', 'display_name', 'gender', 'photo_status', 'photo_preview', 'quick_actions')
    
    # Adds a filter sidebar to instantly see "Pending" users
    list_filter = ('photo_status', 'email_verified', 'gender')
    search_fields = ('email', 'display_name')
    ordering = ('-date_joined',)

    def photo_preview(self, obj):
        """Renders a thumbnail of the selfie directly in the list view."""
        if obj.photo:
            # Using Cloudinary URL directly
            return format_html(
                '<img src="{}" style="height: 80px; width: 80px; object-fit: cover; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1);" />',
                obj.photo.url
            )
        return "No Photo"
    photo_preview.short_description = "Selfie"

    def quick_actions(self, obj):
        """Creates one-click Approve/Reject buttons for pending photos."""
        if obj.photo_status == 'pending' and obj.photo:
            approve_url = reverse('admin:approve_photo', args=[obj.pk])
            reject_url = reverse('admin:reject_photo', args=[obj.pk])
            
            return format_html(
                '<a class="button" style="background-color: #28a745; color: white; padding: 6px 12px; border-radius: 6px; text-decoration: none; margin-right: 8px; font-weight: bold;" href="{}">Approve</a>'
                '<a class="button" style="background-color: #dc3545; color: white; padding: 6px 12px; border-radius: 6px; text-decoration: none; font-weight: bold;" href="{}">Reject</a>',
                approve_url, reject_url
            )
        elif obj.photo_status == 'approved':
            return format_html('<span style="color: #28a745; font-weight: bold;">✓ Approved</span>')
        elif obj.photo_status == 'rejected':
            return format_html('<span style="color: #dc3545; font-weight: bold;">✗ Rejected</span>')
        return ""
    quick_actions.short_description = "Moderation"

    def get_urls(self):
        """Registers the custom URL routes for the Approve/Reject buttons."""
        urls = super().get_urls()
        custom_urls = [
            path('<int:pk>/approve-photo/', self.admin_site.admin_view(self.approve_photo), name='approve_photo'),
            path('<int:pk>/reject-photo/', self.admin_site.admin_view(self.reject_photo), name='reject_photo'),
        ]
        return custom_urls + urls

    def approve_photo(self, request, pk):
        """Logic to approve the photo and return to the list."""
        user = self.get_object(request, pk)
        if user:
            user.photo_status = User.PhotoStatus.APPROVED
            user.save(update_fields=['photo_status'])
            self.message_user(request, f"Successfully approved the photo for {user.display_name}.")
        # Redirect back to the user list. (Assumes your app is named 'connect')
        return redirect('admin:connect_user_changelist')

    def reject_photo(self, request, pk):
        """Logic to reject the photo and return to the list."""
        user = self.get_object(request, pk)
        if user:
            user.photo_status = User.PhotoStatus.REJECTED
            user.save(update_fields=['photo_status'])
            self.message_user(request, f"Rejected the photo for {user.display_name}.", level='WARNING')
        return redirect('admin:connect_user_changelist')


# Optional: Register your other models so they appear in the admin too!
admin.site.register(Connection)
admin.site.register(ConnectionRequest)
admin.site.register(Message)
admin.site.register(RevealRequest)
admin.site.register(Block)
admin.site.register(EmailOTP)
admin.site.register(Report)
