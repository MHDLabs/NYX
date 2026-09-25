# Dockerfile — NYX PHP Relay Server (Lightweight CLI Mode)
# No Apache = No MPM conflicts. Perfect for blind relay.

FROM php:8.1-cli

# 1. Install system dependencies for PostgreSQL AND SQLite
RUN apt-get update && apt-get install -y \
    libpq-dev \
    libsqlite3-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 2. Install PHP extensions (PDO, PostgreSQL, SQLite)
RUN docker-php-ext-install pdo pdo_pgsql pdo_sqlite

# 3. Set working directory and copy server files
WORKDIR /app
COPY server/ /app/

# 4. Expose port 8000 (Standard for PHP CLI server, Railway maps this automatically)
EXPOSE 8000

# 5. Start the PHP built-in server, pointing document root to /app
# The -t flag ensures that requests like /register.php are routed correctly
CMD ["php", "-S", "0.0.0.0:8000", "-t", "/app"]
