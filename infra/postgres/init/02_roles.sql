-- Roles de desarrollo local. Las credenciales de producción se inyectan por entorno
-- y nunca se versionan. Estos valores solo aplican al contenedor local.

CREATE ROLE airflow   LOGIN PASSWORD 'airflow_dev';
CREATE ROLE superset  LOGIN PASSWORD 'superset_dev';
CREATE ROLE etl_writer LOGIN PASSWORD 'etl_writer_dev';
CREATE ROLE bi_reader  LOGIN PASSWORD 'bi_reader_dev';
CREATE ROLE dq_runner  LOGIN PASSWORD 'dq_runner_dev';

REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- Escritura de las capas: solo el proceso ETL
GRANT USAGE, CREATE ON SCHEMA raw, stg, quarantine, core, marts, meta TO etl_writer;
GRANT USAGE ON SCHEMA tms TO etl_writer;

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
