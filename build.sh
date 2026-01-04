#!/usr/bin/env bash
set -o errexit

echo "Current directory:"
pwd
echo "Files here:"
ls -la

pip install -r ./requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate
