# -*- coding: utf-8 -*-
# Generated by Django 1.10.6 on 2017-03-13 18:26
from __future__ import unicode_literals

import django.contrib.postgres.fields.jsonb
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('recipe', '0016_recipefile_data'),
    ]

    operations = [
        migrations.AlterField(
            model_name='recipe',
            name='data',
            field=django.contrib.postgres.fields.jsonb.JSONField(null=True),
        ),
        migrations.AlterField(
            model_name='recipetype',
            name='definition',
            field=django.contrib.postgres.fields.jsonb.JSONField(null=True),
        ),
        migrations.AlterField(
            model_name='recipetyperevision',
            name='definition',
            field=django.contrib.postgres.fields.jsonb.JSONField(null=True),
        ),
    ]