FROM php:8.2-cli

RUN apt-get update && apt-get install -y \
    libpq-dev \
    libsqlite3-dev \
    curl \
    && docker-php-ext-install pdo pdo_pgsql pdo_sqlite \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY server/ /app/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1

CMD ["sh", "-c", "php -S 0.0.0.0:${PORT:-8000} /app/index.php"]
