FROM php:8.2-cli

RUN apt-get update && apt-get install -y \
    libpq-dev \
    libsqlite3-dev \
    curl \
    && docker-php-ext-install pdo pdo_pgsql pdo_sqlite \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy server files correctly
COPY server/ /app/

# Environment variable for port, defaulting to 8000
ENV PORT=8000

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:${PORT}/api/health.php || exit 1

# Start using built-in PHP server with router
CMD ["sh", "-c", "php -S 0.0.0.0:${PORT} /app/router.php"]