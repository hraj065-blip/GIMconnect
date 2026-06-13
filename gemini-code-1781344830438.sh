#!/bin/bash
echo "Building the project..."
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate