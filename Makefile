SHELL := /bin/bash
COMPOSE := docker compose
DBT ?= dbt

.DEFAULT_GOAL := help

.PHONY: help up up-bi up-streaming down reset ps logs lint format test dbt-parse dbt-build data export-superset clean

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Levanta el perfil core (postgres, mongo, airflow)
	$(COMPOSE) up -d --wait

up-bi: ## Levanta core + BI (superset, redis)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.bi.yml up -d --wait

up-streaming: ## Levanta core + streaming (redpanda)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.streaming.yml up -d --wait

down: ## Detiene los servicios sin borrar volúmenes
	$(COMPOSE) down

reset: ## Detiene y borra volúmenes (pierde datos locales)
	$(COMPOSE) down -v

ps: ## Estado de los servicios
	$(COMPOSE) ps

logs: ## Logs del servicio indicado: make logs SERVICE=airflow-scheduler
	$(COMPOSE) logs -f $(SERVICE)

lint: ## Análisis estático
	ruff check src tests scripts
	black --check src tests scripts

format: ## Formatea el código
	ruff check --fix src tests scripts
	black src tests scripts

test: ## Pruebas unitarias
	pytest -q

dbt-parse: ## Valida el proyecto dbt
	$(DBT) parse --project-dir dbt --profiles-dir dbt

dbt-build: ## Construye y prueba los modelos dbt
	$(DBT) build --project-dir dbt --profiles-dir dbt

data: ## Genera datos sintéticos: make data SCALE=demo
	python -m nexora.generators.cli --scale $${SCALE:-demo} --seed $${SEED:-42} --out data/

export-superset: ## Exporta dashboards de Superset a YAML
	bash scripts/export_superset.sh

clean: ## Limpia artefactos locales
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage dbt/target dbt/logs data/tmp
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
