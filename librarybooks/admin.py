from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Staff, Member

# Register the custom Staff (User) model with a nice admin interface
@admin.register(Staff)
class StaffAdmin(UserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'role', 'is_active')
    list_filter = ('role', 'is_active', 'is_superuser')
    search_fields = ('email', 'first_name', 'last_name')
    
    # Add our custom fields to the admin edit form
    fieldsets = UserAdmin.fieldsets + (
        ('Library Role', {'fields': ('role', 'hire_date')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Library Role', {'fields': ('role', 'hire_date')}),
    )

# Register the Member model
@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'email', 'member_type', 'is_active')
    list_filter = ('member_type', 'is_active')
    search_fields = ('first_name', 'last_name', 'email')