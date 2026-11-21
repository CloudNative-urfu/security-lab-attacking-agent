-- Отдельная БД для LiteLLM Proxy: Prisma-миграции требуют чистую БД, нельзя в общую ag.
-- Выполняется только при первичной инициализации тома postgres.
CREATE DATABASE litellm;
