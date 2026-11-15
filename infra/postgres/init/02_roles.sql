-- Roles de la base. Las contraseñas se leen de variables de entorno del
-- contenedor (<ROL>_PASSWORD) y nunca se versionan. Si alguna falta o está
-- vacía, el init falla y el volumen no queda inicializado a medias.

\getenv airflow_password AIRFLOW_PASSWORD
\if :{?airflow_password}
\else
    \set airflow_password ''
\endif
SELECT :'airflow_password' = '' AS airflow_password_missing \gset
\if :airflow_password_missing
    DO $$ BEGIN RAISE EXCEPTION 'Falta la variable de entorno AIRFLOW_PASSWORD'; END $$;
\endif

\getenv superset_password SUPERSET_PASSWORD
\if :{?superset_password}
\else
    \set superset_password ''
\endif
SELECT :'superset_password' = '' AS superset_password_missing \gset
\if :superset_password_missing
    DO $$ BEGIN RAISE EXCEPTION 'Falta la variable de entorno SUPERSET_PASSWORD'; END $$;
\endif

\getenv etl_writer_password ETL_WRITER_PASSWORD
\if :{?etl_writer_password}
\else
    \set etl_writer_password ''
\endif
SELECT :'etl_writer_password' = '' AS etl_writer_password_missing \gset
\if :etl_writer_password_missing
    DO $$ BEGIN RAISE EXCEPTION 'Falta la variable de entorno ETL_WRITER_PASSWORD'; END $$;
\endif

\getenv bi_reader_password BI_READER_PASSWORD
\if :{?bi_reader_password}
\else
    \set bi_reader_password ''
\endif
SELECT :'bi_reader_password' = '' AS bi_reader_password_missing \gset
\if :bi_reader_password_missing
    DO $$ BEGIN RAISE EXCEPTION 'Falta la variable de entorno BI_READER_PASSWORD'; END $$;
\endif

\getenv dq_runner_password DQ_RUNNER_PASSWORD
\if :{?dq_runner_password}
\else
    \set dq_runner_password ''
\endif
SELECT :'dq_runner_password' = '' AS dq_runner_password_missing \gset
\if :dq_runner_password_missing
    DO $$ BEGIN RAISE EXCEPTION 'Falta la variable de entorno DQ_RUNNER_PASSWORD'; END $$;
\endif

\getenv tms_writer_password TMS_WRITER_PASSWORD
\if :{?tms_writer_password}
\else
    \set tms_writer_password ''
\endif
SELECT :'tms_writer_password' = '' AS tms_writer_password_missing \gset
\if :tms_writer_password_missing
    DO $$ BEGIN RAISE EXCEPTION 'Falta la variable de entorno TMS_WRITER_PASSWORD'; END $$;
\endif

CREATE ROLE airflow    LOGIN PASSWORD :'airflow_password';
CREATE ROLE superset   LOGIN PASSWORD :'superset_password';
CREATE ROLE etl_writer LOGIN PASSWORD :'etl_writer_password';
CREATE ROLE bi_reader  LOGIN PASSWORD :'bi_reader_password';
CREATE ROLE dq_runner  LOGIN PASSWORD :'dq_runner_password';
CREATE ROLE tms_writer LOGIN PASSWORD :'tms_writer_password';

\unset airflow_password
\unset superset_password
\unset etl_writer_password
\unset bi_reader_password
\unset dq_runner_password
\unset tms_writer_password

REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- Escritura de las capas: solo el proceso ETL
GRANT USAGE, CREATE ON SCHEMA raw, stg, quarantine, core, marts, meta TO etl_writer;
GRANT USAGE ON SCHEMA tms TO etl_writer;

-- OLTP simulado: solo el generador escribe en tms; el ETL lo lee
GRANT USAGE, CREATE ON SCHEMA tms TO tms_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE tms_writer IN SCHEMA tms GRANT SELECT ON TABLES TO etl_writer;

-- Consumo: solo lectura sobre capas publicadas
GRANT USAGE ON SCHEMA core, marts, meta TO bi_reader;
GRANT USAGE ON SCHEMA raw, stg, quarantine, core, marts, meta TO dq_runner;
GRANT SELECT ON ALL TABLES IN SCHEMA core, marts, meta TO dq_runner;
GRANT INSERT ON ALL TABLES IN SCHEMA meta TO dq_runner;

-- Privilegios por defecto para objetos que cree el ETL
ALTER DEFAULT PRIVILEGES FOR ROLE etl_writer IN SCHEMA core   GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE etl_writer IN SCHEMA marts  GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE etl_writer IN SCHEMA meta   GRANT SELECT ON TABLES TO bi_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE etl_writer IN SCHEMA core   GRANT SELECT ON TABLES TO dq_runner;
ALTER DEFAULT PRIVILEGES FOR ROLE etl_writer IN SCHEMA marts  GRANT SELECT ON TABLES TO dq_runner;
ALTER DEFAULT PRIVILEGES FOR ROLE etl_writer IN SCHEMA meta   GRANT SELECT ON TABLES TO dq_runner;

-- Sin escritura para los roles de consumo
REVOKE CREATE ON SCHEMA public FROM bi_reader, dq_runner;
