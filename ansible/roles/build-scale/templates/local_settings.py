#@PydevCodeAnalysisIgnore

# Include all the default settings.
from settings import *

# Use the following lines to enable developer/debug mode.
DEBUG = {{ django_debug }}
TEMPLATE_DEBUG = DEBUG

# Set the external URL context here
FORCE_SCRIPT_NAME = '/{{ scale_url_prefix }}/api'
USE_X_FORWARDED_HOST = True

ALLOWED_HOSTS = [{{ allowed_hosts }}]

STATIC_ROOT = 'static'
STATIC_URL = '/{{ scale_url_prefix }}/static/'

# Local time zone for this installation. Choices can be found here:
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# Not all choices may be available on all operating systems.
# In a Windows environment this must be set to your system time zone.
TIME_ZONE = 'UTC'

SECRET_KEY = "{{ inventory_hostname | password_hash('sha512') }}"

# The template database to use when creating your new database.
# By using your own template that already includes the postgis extension,
# you can avoid needing to run the unit tests as a PostgreSQL superuser.
POSTGIS_TEMPLATE = 'template_postgis'

DATABASES = {
   'default': {
      'ENGINE': 'django.contrib.gis.db.backends.postgis',
      'NAME': '{{ db_name }}',
      'USER': '{{ db_username }}',
      'PASSWORD': '{{ db_password }}',
      'HOST': '{{ db_host }}',
      'PORT': '5432',
      'TEST': {'NAME': 'test_scale_scale'},
   },
}

# Node settings
NODE_WORK_DIR = '{{ node_work_dir }}'

# If this is true, we don't delete the job_dir after it is finished.
# This might fill up the disk but can be useful for debugging.
SKIP_CLEANUP_JOB_DIR = False

# Master settings
MESOS_MASTER = '{{ mesos_zk }}'

# Metrics collection directory
METRICS_DIR = '/tmp'

# Base URL for influxdb access in the form http://<machine>:8086/db/<cadvisor_db_name>/series?u=<username>&p=<password>&
# An invalid or None entry will disable gathering of these statistics
#INFLUXDB_BASE_URL = None
