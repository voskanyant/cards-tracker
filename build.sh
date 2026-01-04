#!/usr/bin/env bash
set -o errexit

pip install -r ./requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate

# Create admin user (won’t fail deploy if it already exists)
python manage.py createsuperuser --noinput || true
