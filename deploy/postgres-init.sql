-- Runs once, when the docker-compose Postgres volume is first created.
CREATE DATABASE mintgate_test OWNER mintgate;
\connect mintgate
CREATE EXTENSION IF NOT EXISTS vector;
\connect mintgate_test
CREATE EXTENSION IF NOT EXISTS vector;
