{#
    Usa el esquema declarado en dbt_project.yml tal cual, sin prefijarlo con el
    esquema del perfil. Necesario para respetar los nombres raw/stg/core/marts.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}
        {{ default_schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
