from django.contrib.auth.models import AbstractUser
from django.db import models

class Staff(AbstractUser):
    # Tell Django the primary key is called 'staff_id' in MySQL
    staff_id = models.AutoField(primary_key=True, db_column='staff_id')
    
    email = models.EmailField(unique=True)
    
    ROLE_CHOICES = [
        ('Assistant', 'Assistant'),
        ('Librarian', 'Librarian'),
        ('Admin', 'Admin'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='Assistant')
    hire_date = models.DateField(null=True, blank=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.role})"

    class Meta:
        db_table = 'staff'
        managed = False

class Member(models.Model):
    member_id = models.AutoField(primary_key=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    email = models.EmailField(max_length=100, unique=True)
    phone_number = models.CharField(max_length=15)
    member_type = models.CharField(max_length=20)
    date_joined = models.DateField()
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    class Meta:
        db_table = 'members'
        managed = False   # <-- SQL OWNS THIS TABLE