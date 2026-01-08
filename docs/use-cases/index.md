# Overview

Use case examples and complete solutions showcasing Quick Suite capabilities.

{% set use_cases = get_use_cases() %}

{% for category, projects in use_cases.items() %}

## {{ category }}

{% for project in projects %}

- [{{ project.title }}]({{ project.url }}) - {{ project.description }}
{% endfor %}

{% endfor %}
